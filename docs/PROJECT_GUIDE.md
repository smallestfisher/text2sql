# 项目指南

这份文档描述 Text2SQL 当前实现的运行、API、调试和语义资产维护方式。架构边界见 [ARCHITECTURE.md](./ARCHITECTURE.md)，未完成增强项见 [TODO.md](./TODO.md)。根目录 [README.md](../README.md) 保留快速启动和入口导航。

## 1. 当前实现边界

> 本节是当前代码事实：单实例、单业务数据库，不保留未上线旧设计的兼容层。

- 业务库固定为 Oracle，由 `BUSINESS_DATABASE_URL` 配置，只执行只读业务 SQL。
- runtime store 固定为 SQLite，由 `RUNTIME_DATABASE_URL` 配置，保存用户、会话、消息、状态、trace、query log、SQL audit、feedback、eval、物理 catalog、语义草稿和发布历史。
- 业务 SQL 生成、repair、AST 解析和 validator 都按 Oracle 规则运行。
- 语义资产通过管理中心写入 physical catalog、draft 和 release snapshot；查询运行时只读取当前会话绑定的 release snapshot。`tests/fixtures/` 和 `eval/` 下的 JSON 仅用于离线回归，不能成为生产运行时配置。
- 部署配置只从环境变量或 Secret 读取，管理中心“系统设置”只读展示当前值。
- embedding 向量写入 `VECTOR_CACHE_DIR` 下的可重建文件缓存，不占用 runtime 表。
- 启动时初始化 runtime schema 并检查业务库、`sqlglot` 和检索配置。向量配置不可用时明确降级到 BM25；业务库或 SQL 安全依赖不可用时阻断对应查询能力。
- 准确率修复优先沉淀到表结构说明、业务知识、样例、join pattern、retrieval、prompt 和 validator。
- 字段事实只沉淀在发布版本的 `tables_metadata` 和 `SemanticRuntime`。例如时间字段的物理存储格式由 release 的 `time_fields` 声明，SQL prompt 和 validator 只消费这些语义事实，不在业务链路里按具体表名写场景 if/else。

## 2. 主链路

```text
用户问题
  -> 读取 session state
  -> QuestionContext 当前问题准入
  -> 必要时带历史补全追问
  -> 生成 effective_question 和 semantic_brief
  -> 构建 SQL 输入上下文
  -> Terminal Gate 处理 invalid / clarification_needed
  -> Retrieval 检索表结构、业务知识、样例、join pattern 和向量证据
  -> 基于 retrieval 命中补齐 SQL 上下文表
  -> Terminal Gate 再次处理 retrieval 后仍需终止的问题
  -> ContextSummary 汇总进入 SQL 生成的证据
  -> SQL Prompt 组装真实表字段、业务知识和 few-shot
  -> LLM 生成 Oracle SQL
  -> SQL Validator 校验、标记风险并按需 repair
  -> Oracle 执行
  -> AnswerBuilder 生成用户响应
  -> SQLite 落库 response snapshot、trace、query log、SQL audit、消息和会话状态
  -> Workspace 聚合给前端恢复
```

前端主入口是 `POST /api/chat/query/stream`，非流式入口是 `POST /api/chat/query`。调试通过 trace、workspace、runtime query log 和 replay 完成。

## 3. 核心对象

### QuestionContext

`QuestionContext` 是自然语言理解入口，只负责判断当前问题是否能进入 SQL 生成，并把追问改写成完整问题。

关键字段：

- `original_question`：用户原始输入。
- `effective_question`：可独立理解的完整问题。
- `context_relation`：`new`、`follow_up` 或 `ambiguous`。
- `decision`：`answerable`、`clarification_needed` 或 `invalid`。
- `semantic_brief`：供检索和 SQL prompt 使用的语义摘要。
- `subject_domain`：业务域提示。
- `clarification_question`：需要补充信息时返回给用户的问题。

当前 QuestionContext 分两段执行：第一段不带历史，只判断当前问题是否自足；只有第一段返回 `follow_up` 或 `ambiguous` 时，第二段才带 `conversation_summary`、`last_turn`、`recent_turns` 和 `pending_clarification` 做追问补全。这个设计用于避免完整新问题被历史上下文污染。

### RetrievalContext

`RetrievalContext` 汇总进入 SQL prompt 的证据。检索来源包括：

- release `tables_metadata`：真实表、字段、关系、时间字段格式和语义名。
- release `business_knowledge`：业务规则、公式、默认口径和禁忌。
- release `examples_template`：人工确认过的 NL2SQL few-shot。
- release `join_patterns`：稳定 join 方式。
- 向量语料：启用后使用同一批资产生成，按 release 持久化到本地文件缓存。

检索命中进入 SQL prompt 前会做证据闭合：已选中的 join pattern 会补齐 companion tables；已选中的 business knowledge 会补齐其声明的真实表 schema。这个步骤只根据已命中的结构化证据补 schema，不在 Python 中按业务关键词硬编码表选择。

检索打分分两路融合：

- 关键词通道用 BM25，对中文先用 jieba 分词（叠加字符 bigram 兜底），并用当前 release 中业务知识和 join pattern 的关键词给分词器播种。jieba 不可用时退化为纯 bigram，不阻断索引构建。
- 向量通道启用后用同一批语料的 embedding 算 cosine。

两个通道的原始分量纲不同（BM25 约 0-50，cosine 约 0.3-0.9），直接相加会让向量被淹没。因此排序使用 `fusion_score`：按 `(retrieval_channel, source_type)` 分桶做 min-max 归一化到 `[0,1]` 再融合，同一文档跨通道命中时 `fusion_score` 相加。按 `source_type` 分桶是为了和检索证据配额一致，避免高分 example 或 knowledge 把同一通道里的 join pattern 压低。`hit.score` 保留 BM25 原量级，只供 trace、探针和调试观察原始命中强度，不再参与下游二次打分（见下文）。向量关闭时归一化是配额竞争桶内的保序变换，排序结果不变。

检索最终透出的 hit 数量按 source-type 配额总和计算（example 2、table schema 2、knowledge 2、join pattern 1），避免 rerank 已保留的证据在最后截断阶段被提前丢弃。

SQL prompt 二次选择 example、business knowledge 和 join pattern 时，不再直接使用 BM25/cosine 原始量级；检索命中先获得固定到场分，再通过 bounded retrieval boost 进入结构化 evidence 打分。example 的准入闸门也按归一化 `fusion_score` 判断，而非原始 `hit.score`，使纯向量召回、无结构重叠但语义相关的 example 仍能进入打分。每个问题仍只选择 1 条主 join pattern；一条 join pattern 可以覆盖多张表和多段 join path。

### SqlGenerationContext 与 ContextSummary

`SqlGenerationContext` 是 SQL prompt 的内部输入载体，只打包问题分类、语义摘要、检索选出的表、澄清状态和会话必要信息。它不是结构化业务规则引擎，不承载场景化硬编码约束。

`ContextSummary` 是检索后的上下文摘要，用来告诉前端和 SQL prompt 当前问题最终使用了哪些必要信息，包括业务域、语义摘要、候选表、检索域、检索指标、limit 和澄清状态。

### Trace 与 Runtime Log

每次查询都会生成 `trace_id`。Trace 记录阶段状态和关键元数据；query log、retrieval log 和 SQL audit 用同一个 `trace_id` 关联。

常见 trace step：

- `load_session`
- `question_context`
- `classification`
- `retrieve`
- `sql_context_tables`
- `validate_context`
- `terminal_gate`
- `build_sql_prompt`
- `generate_sql`
- `validate_sql`
- `execute`
- `chat_total`
- `response_snapshot`
- `cancelled` / `failed`

SSE 进度事件使用更偏用户流程的阶段名，常见包括 `accepted`、`load_session`、`question_analysis`、`retrieval`、`sql_generation`、`sql_validation`、`execution`、`answer_building`、`completed` 和 `failed`。

## 4. 后端运行

安装依赖并启动：

```bash
cp env.example .env
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --app-dir .
```

也可以用脚本管理进程：

```bash
scripts/devctl.sh start backend
scripts/devctl.sh status backend
scripts/devctl.sh logs backend
scripts/devctl.sh stop backend
```

本地数据库由根目录 [../docker-compose.yml](../docker-compose.yml) 提供：

```bash
docker compose up -d oracle
```

常用后端开关：

- `LOG_LEVEL=DEBUG`：输出主链路 `stage_io` 摘要。
- `VECTOR_TOP_K=8`：向量召回候选数。
- `VECTOR_API_KEY`：启用向量检索时必须可用；本机直接启动不会自动复用 `OPENAI_API_KEY`。
- `LLM_CACHE_PROMPT=false`：透传 llama.cpp OpenAI-compatible `cache_prompt` 开关；不设置时不发送该私有参数。
- `EXECUTION_CACHE_TTL_SECONDS` / `EXECUTION_CACHE_MAX_ENTRIES`：相同 SQL 的短期执行结果缓存。

`GET /api/admin/runtime/status` 会返回 LLM health、LLM metrics、向量检索和 retrieval corpus 状态。

## 5. 前端工作台

前端是独立的 `Vite + React + TypeScript` 工作台，默认通过 Vite 代理转发到后端 API。

界面结构：

- 左侧：会话列表、登录用户信息、管理员视图切换。
- 中间：消息流、欢迎态快捷问题卡片、输入框。
- 右侧：详情侧栏，包含 `结果 / SQL / Trace / 状态`。
- 管理中心：数据库结构与同步、语义草稿与发布历史、runtime 状态、日志、用户、角色、反馈、向量索引刷新和 replay。

运行：

```bash
cd frontend
npm install
npm run dev
```

如果后端地址不同：

```bash
VITE_API_ORIGIN=http://127.0.0.1:9000 npm run dev
```

生产构建：

```bash
npm run build
```

前端主数据入口是 `GET /api/chat/sessions/{session_id}/workspace`，一次性返回 `messages`、`state`、`latest_response`、`latest_trace`、`latest_sql_audit`、`latest_query_logs` 和 `trace_artifacts`。消息流、结果卡、SQL 面板、Trace 面板和状态面板都基于这份 workspace 数据渲染。

## 6. API 入口

Health：

- `GET /health`

Auth：

- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`

Chat / Workspace：

- `POST /api/chat/query/stream`
- `POST /api/chat/query`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`
- `POST /api/chat/feedback`
- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`

Admin：

- `GET /api/admin/dashboard`：管理中心聚合入口，一次性返回 runtime 状态、metrics、metadata、用户、角色、查询日志、反馈和评测汇总；任一子区异常仅降级该区，不影响其它区。
- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/risk-summary`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `POST /api/admin/runtime/vector/prewarm`
- `GET /api/admin/database/status`
- `GET /api/admin/database/catalog`
- `POST /api/admin/database/sync`
- `GET /api/admin/semantic/drafts`
- `GET /api/admin/semantic/drafts/{name}`
- `PUT /api/admin/semantic/drafts/{name}`
- `GET /api/admin/semantic/releases`
- `POST /api/admin/semantic/releases`
- `GET /api/admin/semantic/releases/{release_id}`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`
- `GET /api/admin/users`
- `GET /api/admin/roles`

完整路由以 `backend/app/api/routes` 为准。

## 7. SQL 治理

`SqlValidator` 是执行前硬边界，负责检查：

- 单语句。
- 只读查询。
- 禁止危险关键字和不安全结构。
- 表和字段必须存在于语义资产中。
- Oracle 语法边界和结果限制。
- SQL 来源必须在 SQL prompt 允许表范围内。
- 时间字段表达式必须匹配语义资产声明的物理格式。对 `YYYYMM`、`YYYYMMDD`、`YYYY-MM`、`YYYY-MM-DD` 这类字符串格式时间字段，不允许套 `TO_CHAR(field, 'YYYYMM')` 等 Oracle 日期格式化函数；应使用 `time_resolution.projection_example` 中的字符串表达式，例如 `SUBSTR(work_date, 1, 6)`，或使用匹配物理格式的范围过滤。
- 宽表扫描、超大结果等风险标记。
- SQL 质量警告，例如未保护零分母的除法、多表聚合不显式分层、位置排序。

errors 是执行前硬阻断；warnings 不会单独阻断执行，但会进入 `sql_validation`、trace、query log 和 SQL audit，并汇总为 `risk_level` 与 `risk_flags`。`risk_flags` 在写 query log 的 json 列时同步写入 `query_risk_flags` 索引表（`trace_id, source, flag`），启动时按 json 列回填缺失行；管理中心按 flag 过滤查询日志（`list_query_logs`/`count_query_logs`）和风险汇总（`summarize_query_risks`）都基于该表查询，json 列只作落库和调试观察。

SQL repair 只处理 validator 或执行错误反馈出来的问题。业务正确性主要依赖语义资产、检索命中、prompt 质量和样例质量。

## 8. 调试流程

一条真实业务问题答错时，先定位层级，再改长期资产。

### 5 分钟排查

1. 在工作台或 `POST /api/chat/query/stream` 复现问题。
2. 记录 `session_id` 和 `trace_id`。
3. 打开 `GET /api/chat/sessions/{session_id}/workspace`。
4. 先看 `question_context.decision`、`question_context.effective_question`、是否出现 `terminal_gate`、`retrieval.hit_count_by_source`、`sql_validation.valid`、`sql_validation.warnings`、`execution.status`、`answer.status`。
5. 再看 `GET /api/chat/traces/{trace_id}`、`GET /api/chat/traces/{trace_id}/retrieval`、`GET /api/chat/traces/{trace_id}/sql-audit`。
6. 修复后执行 `POST /api/admin/runtime/query-logs/{trace_id}/replay`。

### 分层判断

- 没理解用户问题或追问：看 QuestionContext。
- 直接返回无效或需澄清：看 `terminal_gate`、QuestionContext 和 `pending_clarification`。
- 关键表、知识或样例没进上下文：看 Retrieval。
- SQL 结构、字段、时间、口径错：看 SQL Prompt 和语义资产。
- SQL 被拦或没拦住：看 SQL Validator。
- SQL 对但结果错：看 Oracle 数据、时间、版本、过滤条件。
- 后端结果对但界面错：看 Workspace 和 response restore。

### Retrieval 排查

先看 `retrieval_terms`、`retrieval_channels`、`hit_count_by_source`、`hit_count_by_channel` 和 top hits 的 `source_type`、`source_id`、`score`、`matched_features`。

如果问题是检索证据覆盖不足，优先新增或更新 `eval/retrieval_cases.json`，再运行：

```bash
python3 -m unittest tests.test_retrieval_eval.RetrievalEvalTests
```

这个测试只验证检索命中和 SQL prompt 的 `available_tables`、`business_knowledge_entry_ids`、`join_pattern_ids`，不依赖 LLM 和 SQL 执行。

测试默认在 vector 关闭下运行，只校验 keyword 通道能稳定命中的证据。`retrieval_cases.json` 里的 `vector_only_expected_join_pattern_ids` 是只有 vector 通道参与排序时才能召回的证据，仅在 vector 启用时才会被校验。要验证 vector 通道在真实环境的命中质量，用只读探针打印两个通道的原始分数分布：

```bash
.venv/bin/python scripts/probe_retrieval_scores.py
```

探针会加载 `.env`（需要 `VECTOR_API_KEY`），对 `eval/retrieval_cases.json` 跑真实的 keyword BM25 和 vector cosine，打印每个通道的分数范围和 top 命中。它只读、不落库、不改代码，只调用 embedding API。

### SQL Prompt 排查

先看 trace 中 `build_sql_prompt` 的 `context_summary`：

- `selected_sources`
- `business_knowledge_entry_ids`
- `retrieved_example_ids`
- `join_pattern_ids`
- `table_schemas_count`
- `time_resolution_count`

`selected_sources` 是最终 SQL prompt 可用表。如果这里缺关键表，先回到 Retrieval 检查对应证据是否命中、证据 metadata 是否声明了该表。

`time_resolution_count` 大于 0 时，继续看 `evidence_context.time_resolution`。这里会给出 release 声明的逻辑时间名到物理字段映射、`grain`、`format` 和 `projection_example`。例如某字段声明为 `YYYYMMDD` 字符串时，月粒度表达应使用 `SUBSTR(field_name, 1, 6)`，而不是把它当日期字段套 `TO_CHAR`。

## 9. 语义资产维护

发布后会直接影响 SQL 生成的运行时资产：

- `tables_metadata` draft
- `business_knowledge` draft
- `join_patterns` draft
- `examples_template` draft

仓库不再保存生产语义 JSON。`tests/fixtures/` 中的模拟发布快照只供单元测试和 lint；检索算法的离线回归样本保存在：

- `eval/retrieval_cases.json`

### 存储位置

物理 catalog 来自当前 Oracle 的 schema sync。四类语义资产在管理中心中以 draft 保存，发布后形成不可变 release snapshot；查询运行时只加载当前会话绑定的 release。`tests/fixtures/` 和 `eval/retrieval_cases.json` 仅供离线回归，不会进入生产镜像。管理员创建或从真实 trace 沉淀的 evaluation case 保存在 SQLite 运行库中。

`subject_domain` 和 `domains` 是当前业务自行定义的字符串。前后端没有固定业务域列表，也不会从具体表名推断业务域。时间字段的业务别名通过 `time_fields.<field>.semantic_names` 在表结构编辑器中配置。

推荐使用管理中心的「当前数据库 / Schema 同步 / 草稿版本 / 发布历史」完成配置、校验和发布。保存 draft 不会改变线上查询，只有发布成功并激活后，新会话才使用新版本。

### 应该改哪里

- 表、字段、时间格式、枚举说明错误：在 `tables_metadata` 草稿中修改并发布。
- 可复用业务规则、公式、默认口径、禁忌：在 `business_knowledge` 草稿中修改。
- 稳定 join 方式：在 `join_patterns` 草稿中修改。
- 已人工确认正确的完整问题和 Oracle SQL：在 `examples_template` 草稿中修改。
- 防止真实问题回归：通过管理 API 创建 evaluation case，或从真实 trace 执行 `materialize-case`。
- 防止关键检索证据或表 schema 丢失：改 `eval/retrieval_cases.json`。

不要把业务事实写进 Python if/else；代码只负责加载、检索、排序、裁剪、渲染、组装、安全校验和审计。

### 时间字段格式

发布版本 `tables_metadata` 中的 `time_fields` 是时间表达式生成和执行前校验的共同事实来源。维护规则：

- `grain` 表示业务粒度，例如 `day` 或 `month`。
- `format` 表示物理字段存储格式，不表示自然语言输入格式。常见值包括 `YYYYMM`、`YYYYMMDD`、`YYYY-MM`、`YYYY-MM-DD`。
- `semantic_names` 声明该物理时间字段可承载的发布语义名；QuestionContext、SQL prompt 和 validator 通过它解析业务字段，不在代码中内置统一的月份或日期别名。
- 如果物理字段是字符串编码的日期或月份，SQL 应使用字符串表达式或匹配格式的字面量范围；不要把它当 Oracle `DATE/TIMESTAMP` 字段套 `TO_CHAR`、`TRUNC` 等日期函数。
- 如果真实数据库字段后来改成 `DATE/TIMESTAMP`，先更新 `time_fields` 的表达约定和对应测试，再调整 prompt/validator 行为；不要只在样例 SQL 中局部修正。

### 样例要求

`examples_template` draft 中的样例必填字段只有 `question` 和 `sql`，推荐补充 `id`、`subject_domain`、`metrics`、`dimensions`、`tags`、`notes`、`result_shape`。

样例 SQL 必须满足：

- 单条 Oracle 只读 `SELECT` 或 `WITH ... SELECT`。
- 使用真实物理表和物理字段。
- 默认包含 `FETCH FIRST n ROWS ONLY`。
- 时间过滤匹配真实存储格式。
- 对比类问题先在两侧事实表内按对齐粒度聚合，再 join 聚合结果。
- 比率类计算处理除零。
- 不使用 `SELECT *` 和 `ORDER BY 1`。

### 业务知识要求

`business_knowledge` draft 使用 entry + notes：

- `id`：稳定、唯一、可读，使用小写蛇形命名。
- `priority`：可选的显式排序权重，范围按 `-20..20` 截断；只用于同一问题下的证据优先级，不改变规则内容。
- `always_include`：可选，仅在 entry 声明的业务域内始终进入候选；用于该域每次查询都必须携带的基础规则，避免滥用。
- `domains`：只填实际适用业务域。
- `tables`：只填规则直接涉及的事实表、维表或桥接表。
- `keywords`：覆盖用户词、业务词和关键物理字段。
- `notes`：每条 note 只表达一个可执行规则。

note 子文档只影响检索粒度，不改变录入格式。命中 note 后仍会回填父 entry，并用父 entry 的 `tables` 做 schema closure。

关键词匹配对 ASCII 字段名和标识符采用完整 token，对中文或中英混合短语采用子串匹配。schema closure 只负责补齐已命中证据引用的表结构，不能反过来成为业务知识的相关性信号。

### 维护流程

1. 从真实失败问题开始，看 trace、retrieval、prompt 和 SQL audit，确认失败原因。
2. 判断修改目标：表结构说明、业务知识、join pattern、样例、eval case 或 retrieval case。
3. 在 UI 保存对应 draft，处理 schema 引用和版本冲突提示。
4. 执行离线 fixture 检查：

```bash
python3 backend/domain_config_lint.py
```

5. 发布新版本并预热该版本索引。
6. 用原始问题重新跑一次，确认 retrieval 命中、SQL prompt 使用了新内容，并且 SQL 通过 validator 和执行。
7. 对真实高价值问题，补充 eval case 或 example。

## 10. Replay / Eval

- `replay`：用当前链路重跑历史 trace。
- `materialize-case`：把真实 trace 沉淀为 eval case。
- `materialize-example`：把人工确认过的 trace 沉淀为 SQL few-shot 样例。
- SQLite `evaluation_cases`：保存管理员创建或从真实 trace 沉淀的回归样本。
- `examples_template` draft：保存可进入 prompt 的人工确认样例。

## 11. 验证命令

```bash
python3 -m unittest discover -s tests
python3 backend/domain_config_lint.py
```

如果改前端：

```bash
cd frontend
npm run build
```

## 12. 目录速查

- `backend/app/api/routes`：HTTP 路由。
- `backend/app/core`：应用装配、settings、异常和取消处理。
- `backend/app/models`：请求、响应、会话、检索、trace、workspace、auth、eval 模型。
- `backend/app/repositories`：SQLite runtime、语义版本和文件向量缓存的 repository 边界。
- `backend/app/services`：QuestionContext、retrieval、prompt、LLM、SQL 校验、执行、answer、会话、审计、管理、eval 和 auth。
- `frontend/src`：工作台、管理中心和 API client。
- `tests/fixtures/`：离线回归用的发布快照样例。
- `eval/retrieval_cases.json`：检索算法的离线回归样本。
