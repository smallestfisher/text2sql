# 系统架构

Text2SQL 把中文业务问题转换成 Oracle SQL，执行后把结果、SQL、检索证据和审计记录返回给工作台。后端采用 LLM-first 架构：代码负责组织上下文、检索可信资产、约束 SQL 安全边界、执行查询和沉淀运行证据。

## 架构边界

- 业务数据库：Oracle，由 `BUSINESS_DATABASE_URL` 配置，只执行只读业务 SQL。
- 运行数据库：MySQL，由 `RUNTIME_DATABASE_URL` 配置，保存用户、会话、消息、状态、trace、query log、SQL audit、feedback、eval 和向量语料。
- 语义资产：仓库内 `semantic/`、`examples/`、`eval/` 文件，作为检索、prompt、管理台和评测的共同来源。
- SQL 方言：业务 SQL 固定为 Oracle；生成、修复、AST 解析和 validator 都按 Oracle 规则运行。

## 主链路

```text
用户问题
  -> 读取会话状态
  -> QuestionContext 生成完整问题和语义摘要
  -> 轻量分类与内部 plan shell
  -> Retrieval 检索表结构、业务知识、样例和 join pattern
  -> ContextSummary 汇总进入 SQL 生成的证据
  -> SQL Prompt 组装真实表字段、业务规则和 few-shot
  -> LLM 生成 Oracle SQL
  -> SQL Validator 校验并按需 repair
  -> Oracle 执行
  -> AnswerBuilder 生成用户响应
  -> MySQL 落库 trace、query log、SQL audit、消息和会话状态
  -> Workspace 聚合给前端恢复
```

前端主入口是 `POST /api/chat/query/stream`，非流式入口是 `POST /api/chat/query`。调试通过 trace、workspace、runtime query log 和 replay 接口完成，不再提供独立的 `/api/query/*` 单步调试 API。

## 核心对象

### QuestionContext

`QuestionContext` 是自然语言理解入口，只负责判断当前问题能否进入 SQL 生成，并把追问改写成完整问题。

关键字段：

- `original_question`：用户原始输入。
- `effective_question`：可独立理解的完整问题。
- `context_relation`：`new`、`follow_up` 或 `ambiguous`。
- `decision`：`answerable`、`clarification_needed` 或 `invalid`。
- `semantic_brief`：供检索和 SQL prompt 使用的语义摘要。
- `subject_domain`：业务域提示。
- `clarification_question`：需要补充信息时返回给用户的问题。

QuestionContext prompt 会带入最近会话、待澄清上下文、相关业务知识摘录和候选表字段，但不生成 SQL。

### ContextSummary

`ContextSummary` 是检索后的上下文摘要，用来告诉前端和 SQL prompt 当前问题最终使用了哪些必要信息。它承载业务域、语义摘要、候选表、检索域、检索指标、limit 和澄清状态。

`SqlGenerationContext` 是 SQL prompt 的内部输入载体，只打包问题分类、语义摘要、检索选出的表、澄清状态和会话必要信息。它不作为结构化业务规则引擎，也不承载场景化硬编码约束；业务口径、字段关系、join 方式和示例应来自检索证据与语义资产。

### RetrievalContext

`RetrievalContext` 汇总进入 SQL prompt 的证据。检索文本来自 `effective_question`、`semantic_brief` 和会话摘要。

检索来源：

- `semantic/tables.json`：真实表、字段、关系、时间字段格式。
- `semantic/business_knowledge.json`：业务规则、公式、默认口径和禁忌。
- `examples/nl2sql_examples.template.json`：人工确认过的 NL2SQL few-shot。
- `semantic/join_patterns.json`：稳定 join 方式。
- 向量语料：启用后使用同一批资产生成并持久化到 runtime MySQL。

trace 和 runtime log 会记录命中来源、分数、通道和 matched features。

### Trace 与 Runtime Log

每次查询都会生成 `trace_id`。Trace 记录阶段状态和关键元数据；query log、retrieval log 和 SQL audit 用同一个 `trace_id` 关联。

主要阶段：

- `load_session`
- `question_context`
- `classification`
- `retrieve`
- `sql_context_tables`
- `build_sql_prompt`
- `generate_sql`
- `validate_sql`
- `execute_sql`
- `answer`
- `persist`

### SessionState

`SessionState` 保存追问和工作台恢复需要的上下文：

- `conversation_summary`
- `recent_turns`
- `last_effective_question`
- `last_semantic_brief`
- `last_context_summary`
- `last_sql`
- `pending_clarification`

当系统需要澄清时会写入 `pending_clarification`；后续可回答问题成功后清理该状态。

## SQL Prompt

SQL prompt 由 `PromptBuilder` 生成，包含：

- 当前问题和 `semantic_brief`。
- `context_summary` 和 `evidence_context`。
- retrieval 命中的业务知识、样例和 join pattern。
- 真实物理表和字段说明。
- Oracle SQL 生成约束。

核心约束：

- 只输出一条 `SELECT` 或 `WITH ... SELECT`。
- 只使用已知真实表和真实字段。
- 使用 Oracle 语法。
- 默认包含 `FETCH FIRST ... ROWS ONLY`。
- 禁止 `SELECT *`。
- 只返回 SQL，不返回 markdown 或解释。

## SQL 治理

`SqlValidator` 是执行前的硬边界，负责检查：

- 单语句。
- 只读查询。
- 禁止危险关键字和不安全结构。
- 表和字段必须存在于语义资产中。
- Oracle 语法边界和结果限制。
- 宽表扫描、超大结果等风险标记。

SQL repair 只处理 validator 或执行错误反馈出来的问题。业务正确性主要依赖语义资产、检索命中、prompt 质量和样例质量。

## 执行与响应

SQL 通过校验后由 `SqlExecutor` 在 Oracle 上执行，受超时、最大行数和只读连接约束。`AnswerBuilder` 把执行结果映射成前端状态，例如 `ok`、`empty_result`、`clarification_needed`、`invalid`、`error`、`chat`。

`ChatResponse` 返回：

- `question_context`
- `classification`
- `context_summary`
- `retrieval`
- `trace`
- `answer`
- `sql`
- `context_validation`
- `sql_validation`
- `execution`
- `next_session_state`

## 工作台恢复

前端通过 `GET /api/chat/sessions/{session_id}/workspace` 恢复会话。该接口聚合：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- `trace_artifacts`

消息流、结果卡、SQL 面板、Trace 面板和状态面板都基于这份 workspace 数据渲染。

## 运行与管理

- 启动时会检查业务库、runtime 库、runtime schema、metadata、`sqlglot`、LLM 和向量检索配置。
- `POST /api/admin/metadata/reload` 会重新装配容器、加载语义资产并刷新检索语料。
- `POST /api/admin/runtime/vector/prewarm` 会同步构建向量索引。
- `POST /api/admin/runtime/query-logs/{trace_id}/replay` 用当前链路重放真实问题。
- `materialize-case` 把真实 trace 沉淀为 eval case。
- `materialize-example` 把人工确认过的 trace 沉淀为 few-shot 样例。
