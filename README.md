# Text2SQL

面向业务分析问题的 LLM-first Text2SQL 工程。当前主链路已经从“本地规则/模板拼 SQL”切换为“LLM 基于真实表结构和业务知识直接生成 SQL，再由校验器和执行器治理”。如果你是第一次进入仓库，先看本文件，再看 `TEXT2SQL_ARCHITECTURE.md` 和 `DEBUG_PLAYBOOK.md`。

## 当前状态

- `semantic/tables.json` 是真实数据库表和字段描述的主来源
- `semantic/business_knowledge.json` 是主业务知识来源
- `semantic/join_patterns.json` 是稳定多表关联经验的主来源
- `semantic/domain_config.json` 仍然保留，但现在只是语义配置的 manifest 入口；真实内容按职责拆在 `semantic/domain_config/`
- `semantic/domain_config/base/prompt_assets.json` 是分类、相关性、intent、SQL 生成的静态 prompt 资产入口，`PromptBuilder` 不再在代码里硬编码大段业务提示
- `semantic/domain_config/query_profiles/*.json` 现在除了字段白名单外，也承载 source 互斥、support table 补全、post-process 这类运行时约束
- `semantic/domain_config/metrics/*.json` 承载指标定义、物理表达式和口径约束；例如 `plan_actual` 指标可通过 `act_type_scope` 声明只适用于 `投入` 或 `产出`，Normalizer 会按配置过滤 LLM intent，避免在 Python 里堆业务指标名
- SQL、分类、相关性判断 prompt 目前统一以中文自然语言指令为主
- PromptBuilder 只选择当前问题相关的 schema、业务知识和少量真实 few-shot，避免 token 膨胀
- `examples/nl2sql_examples.template.json` 保留真实 few-shot 资产；命中后会以 `retrieved_examples` 形式进入 SQL prompt
- 命中的 `join_pattern` 会以 `join_patterns` 形式进入 SQL prompt
- 对 `oms_inventory` 的常规库存问题，如果用户只说“OMS库存/库存”而没有显式指定 `glass`、`panel` 或具体库龄段，当前默认同时返回 `glass_qty` 和 `panel_qty` 两套口径；只有明确问库龄时才应使用 `ONE_AGE_panel_qty` 到 `EUGHT_AGE_panel_qty`
- 前端会话恢复的主入口是 `GET /api/chat/sessions/{session_id}/workspace`
- 前端提问默认走 `POST /api/chat/query/stream`，通过 SSE 主动推送阶段进度和最终结果，不再靠轮询猜状态
- 如果客户端在 SSE 过程中断开，后端会通过取消 token 尽快停止后续阶段，而不是继续完整跑完整条链路
- 复杂横表逻辑由 LLM 在 SQL 中展开并由校验器治理
- 当前检索方向已经明确为 `hybrid retrieval`：关键词 / 向量 / 结构化重排联合召回，而不是继续扩张规则门控
- 当前默认就启用向量检索；仍然保留 `ENABLE_VECTOR_RETRIEVAL` 开关用于环境级控制
- 当前默认向量模型为 `siliconflow + Qwen/Qwen3-Embedding-8B`，默认维度 `1024`
- retrieval corpus 的 embedding 会持久化到 runtime 库的 `vector_corpus_documents` 表；默认会在启动和 reload 时同步预热，失败就直接报错，不再静默降级
- 当前工程的业务数据库固定为 Oracle，使用 `oracle+oracledb://...` 连接串；SQL 生成和校验固定使用 `Oracle SQL`、`FETCH FIRST n ROWS ONLY` 和 Oracle 日期函数约束
- runtime 数据库固定为 MySQL，用于保存会话、审计、eval 和 retrieval corpus 数据
- 容器启动时会显式校验 business DB 连通性和只读超时设置、runtime DB 连通性、metadata 文件可读性，以及 `sqlglot` 依赖；任一失败都会直接阻断启动
- `ENABLE_CHITCHAT_MODE=true` 且当前用户拥有 `chitchat` 权限时，问候/闲聊/无关问题不再直接丢弃，而是返回终止型闲聊回复；默认 `false`
- LLM 不可用、调用失败或返回非法结构时，请求会显式失败，不再静默降级为 `stub/skipped`
- LLM 结构化输出如果字段格式非法，例如 `metrics/filters/context_delta/time_context` 形状不对，请求会直接失败，不再自动忽略坏字段继续执行
- `workspace` 恢复链路现在要求 `query_log`、`trace`、`sql_audit` 和可恢复 response 都齐全；缺件会直接报错，不再返回残缺工作台数据
- SQL 生成重试和 SQL repair 重试现在已经分开配置：`LLM_MAX_RETRIES` 控制首轮生成，`SQL_REPAIR_MAX_RETRIES` 控制通用 repair fallback

## 快速启动

### 一键启停

```bash
scripts/devctl.sh start
scripts/devctl.sh status
scripts/devctl.sh stop
```

脚本默认同时启动后端 `127.0.0.1:8000` 和前端 `127.0.0.1:5173`，pid 和日志写入 `.runtime/`。也可以只操作单个服务：

```bash
scripts/devctl.sh restart backend
scripts/devctl.sh logs frontend
```

端口可通过 `BACKEND_PORT`、`FRONTEND_PORT` 覆盖。

### Backend

```bash
cp env.example .env
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --app-dir .
```

更完整的后端运行、配置和 API 说明见 `backend/README.md`。

### 本地数据库

当前工程本地数据库由仓库根目录的 Compose 文件统一启动：Oracle 作为业务库，MySQL 作为 runtime 库。

```bash
docker compose up -d
```

Oracle 容器默认创建 `admin/admin123` 业务用户，PDB 服务名为 `FREEPDB1`，并在新 volume 首次初始化时自动执行 [sql/oracle_business_schema.sql](sql/oracle_business_schema.sql) 创建业务表。MySQL 容器默认创建 `manager` runtime 库和 `admin/admin123` 用户。对应连接串：

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
```

如果只需要单独启动 Oracle 业务库，仍可使用 `docker-compose.oracle.yml`：

```bash
docker compose -f docker-compose.oracle.yml up -d
```

业务表结构定义见 [sql/oracle_business_schema.sql](sql/oracle_business_schema.sql)。如果 Oracle volume 已经存在，容器初始化脚本不会重复执行；需要补建业务表时可手动执行：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
```

生产样例测试数据可从 `test_data.xlsx` 生成 Oracle insert 脚本：

```bash
python3 scripts/import_test_data_to_oracle.py
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_test_data.sql
```

生成脚本会把 Excel sheet 映射到业务表，并按 Oracle 业务 schema 归一化日期和数值字段。

### Frontend

```bash
cd frontend
npm install
npm run dev
```

前端工作台和详情面板说明见 `frontend/README.md`。

默认前端会代理到 `http://127.0.0.1:8000`。如需改后端地址，可在启动前设置 `VITE_API_ORIGIN`。

### Runtime 库

- 业务查询库读取 `BUSINESS_DATABASE_URL`
- 运行时库读取 `RUNTIME_DATABASE_URL`
- 业务查询库固定为 Oracle，不提供 MySQL 业务库路径
- runtime 库固定为 MySQL，必须显式配置独立的 `RUNTIME_DATABASE_URL`
- 本地 MySQL runtime 容器会通过 [sql/mysql_runtime_init.sql](sql/mysql_runtime_init.sql) 创建 `manager` 库、`admin` 用户，并执行 [sql/runtime_store.sql](sql/runtime_store.sql) 初始化 runtime 表
- 应用首次启动仍会幂等检查建库、建表、补增量列和补索引
- runtime 库除了会话、审计和 eval 数据外，现在也承载 retrieval corpus 的持久化向量表 `vector_corpus_documents`
- 如果 runtime schema 初始化失败，服务会直接启动失败，不会再带着半可用 runtime 继续运行

如果你复用了旧的 runtime 库，启动或登录时如果报：

```text
Unknown column '...'
```

说明运行时表结构没升级到最新版本。优先用有 `ALTER TABLE` 权限的账号重启服务；如果运行账号没有变更表结构权限，就手动执行 [sql/runtime_store.sql](sql/runtime_store.sql) 并补齐 `RuntimeStoreInitializer` 里定义的增量列。常见缺失列包括 `query_logs.plan_risk_level`、`query_logs.sql_risk_level`、`sql_audit_logs.plan_risk_level`、`sql_audit_logs.sql_risk_level`。

如果你是在旧 runtime 库上升级到当前版本，还需要确认 `vector_corpus_documents` 表和它的索引已经存在；向量检索初始化现在依赖这张表。

## 文档导航

### 当前事实文档

- [TEXT2SQL_ARCHITECTURE.md](TEXT2SQL_ARCHITECTURE.md)：LLM-first 架构、端到端运行流程、前后端模块职责、runtime 闭环和配置边界
- [DEBUG_PLAYBOOK.md](DEBUG_PLAYBOOK.md)：单题调试、真实联调、样本沉淀和 eval / replay 入口
- [backend/README.md](backend/README.md)：后端运行方式、配置、API、runtime 库、eval / replay 和 example 资产说明
- [frontend/README.md](frontend/README.md)：前端工作台、详情侧栏和数据加载方式

### 历史 / 阶段性文档

- [LLM_INTENT_MIGRATION_PLAN.md](LLM_INTENT_MIGRATION_PLAN.md)：parser / classifier / planner 迁移到 LLM 主理解链路的收官说明，不作为当前实现主说明
- [NEXT_STAGE_EXECUTION_PLAN.md](NEXT_STAGE_EXECUTION_PLAN.md)：retrieval-first 阶段计划备忘，保留阶段判断和资产补齐思路，不作为当前状态说明

## 一句话原则

遇到准确率问题时，优先修 `semantic/tables.json`、`semantic/business_knowledge.json`、example / few-shot 资产、retrieval、prompt 上下文和 validator；不要把系统重新拉回“大量场景规则 + 本地 SQL 模板”的旧路径。
