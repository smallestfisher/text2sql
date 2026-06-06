# 后端

后端是 FastAPI 应用，负责自然语言查询主链路、SQL 安全校验、Oracle 执行、runtime 落库、工作台恢复、管理台、replay 和 eval。架构说明见 [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)，排查流程见 [../docs/DEBUG_PLAYBOOK.md](../docs/DEBUG_PLAYBOOK.md)。

## 运行

安装依赖并启动：

```bash
cp env.example .env
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --app-dir .
```

也可以用仓库脚本管理进程：

```bash
scripts/devctl.sh start backend
scripts/devctl.sh status backend
scripts/devctl.sh logs backend
scripts/devctl.sh stop backend
```

本地数据库由根目录 [../docker-compose.yml](../docker-compose.yml) 提供：

```bash
docker compose up -d oracle mysql
```

## 配置

后端优先读取仓库根目录 `.env`。

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen/Qwen3-14B"
AUTH_TOKEN_SECRET="change-me"
```

固定边界：

- 业务库固定 Oracle，不提供 MySQL 业务库路径。
- runtime 库固定 MySQL，不复用业务库。
- 业务 SQL 生成、repair、AST 解析和 validator 固定使用 Oracle 规则。
- runtime schema 使用 MySQL 表结构。

常用开关：

- `LOG_LEVEL=DEBUG`：输出主链路 `stage_io` 摘要。
- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索。
- `PREWARM_VECTOR_RETRIEVAL=true`：启动和 metadata reload 时预热向量索引。
- `VECTOR_TOP_K=8`：向量召回候选数。多表问题需要给 example、knowledge、table_schema 和 join_pattern 留出覆盖空间。
- `LLM_MAX_RETRIES`：QuestionContext 和 SQL 首轮生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair 重试次数。
- `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_ENTRIES`：进程内 LLM prompt cache。
- `LLM_CACHE_PROMPT=false`：透传 llama.cpp OpenAI-compatible `cache_prompt` 开关；不设置时不发送该私有参数。
- `DEFAULT_SQL_LIMIT` / `HIGH_RISK_SQL_LIMIT`：默认 SQL 结果限制和高风险结果限制阈值。
- `SQL_TIMEOUT_SECONDS` / `EXECUTION_MAX_ROWS`：SQL 执行超时和单次最大返回行数。
- `EXECUTION_CACHE_TTL_SECONDS` / `EXECUTION_CACHE_MAX_ENTRIES`：相同 SQL 的短期执行结果缓存。

## 启动检查

容器装配会 fail-fast 检查：

- business DB 连通性。
- runtime DB 连通性。
- runtime schema 初始化、补列和补索引。
- metadata 文件可读性和 JSON 结构。
- `sqlglot` 与 Oracle AST 校验器可用性。
- 向量检索配置满足开启条件，且预热开启时向量语料能完成同步。

LLM client 会随容器初始化，`GET /api/admin/runtime/status` 会返回 LLM health 和 metrics；`OPENAI_API_KEY` 未配置时，应用可以启动，但 QuestionContext 和 SQL 生成会在调用时失败。

任一关键依赖失败都会阻断启动。

## 数据库初始化

- Oracle 首次新 volume 初始化时执行 [../sql/oracle_business_init.sh](../sql/oracle_business_init.sh)，创建业务表。
- MySQL 首次新 volume 初始化时执行 [../sql/mysql_runtime_init.sql](../sql/mysql_runtime_init.sql)，创建 `manager`、`admin/admin123`，并执行 [../sql/runtime_store.sql](../sql/runtime_store.sql)。
- 应用启动后通过 `RuntimeStoreInitializer` 幂等检查 runtime 建库、建表、补列和补索引。

已有 volume 不会重复执行 Docker 初始化脚本。需要手动补表时：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
docker exec -i text2sql-mysql mysql -uadmin -padmin123 manager < sql/runtime_store.sql
```

## 主链路

工作台默认走 `POST /api/chat/query/stream`，后端内部顺序是：

1. 读取 session state。
2. 生成 `QuestionContext`，得到 `effective_question` 和 `semantic_brief`。
3. 构建轻量 `classification` 和 `SqlGenerationContext` 输入载体。
4. `terminal_gate` 处理明显 `invalid` 或需要澄清的问题。
5. `RetrievalService` 检索表结构、业务知识、样例、join pattern 和向量命中。
6. 根据 retrieval 命中补齐 SQL 上下文表，并做上下文记录。
7. `terminal_gate` 再次处理 retrieval 后仍需终止的问题。
8. `PromptBuilder` 组装 Oracle SQL prompt。
9. `LLMClient` 生成 SQL，必要时按 validator 或执行错误 repair。
10. `SqlValidator` 校验只读、单语句、真实表字段、Oracle 边界和结果限制，并产出 warning、risk level、risk flags。
11. `SqlExecutor` 执行 Oracle 查询。
12. `AnswerBuilder` 构造响应。
13. 保存 response snapshot、trace、query log、retrieval log、SQL audit、消息和下一轮 session state。

如果 `QuestionContext` 或 retrieval 后的 SQL 上下文判定为 `invalid` / `clarification_needed`，链路会通过 `terminal_gate` 提前结束，并保存可用 trace、消息和 session state，不进入 SQL 生成和执行。

## 认证与权限

首次部署通过 `POST /api/auth/bootstrap-admin` 创建第一个管理员。之后前端使用 `POST /api/auth/login` 获取 bearer token。

- `admin`：可以访问 `/api/admin/*`，管理用户、角色、metadata、runtime log、replay 和 eval。
- `viewer`：基础查询用户，可使用标准工作台查询和查看自己会话内的 trace / SQL audit / 导出结果。

自定义角色可以保存到 runtime 库，但当前后端权限判断只内置使用 `admin` 和 `viewer`。

## SQL 治理

`SqlValidator` 的 errors 是执行前硬阻断，warnings 是治理信号，不会单独阻断执行。当前 warnings 和 risk flags 会进入 `sql_validation`、trace、runtime query log 和 SQL audit。

质量警告覆盖这些常见问题：

- 比率或除法没有用 `NULLIF` 或 `CASE WHEN` 保护零分母。
- 多表聚合没有用 CTE 或派生表显式锁定聚合粒度。
- `ORDER BY 1` 这类位置排序没有使用显式输出别名或表达式。

管理员可以用 `GET /api/admin/runtime/query-logs` 的 `sql_risk_level`、`subject_domain`、`risk_flag` 查询参数筛选日志，也可以用 `GET /api/admin/runtime/query-logs/risk-summary` 看最近查询的风险分布。

## API 分组

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
- `GET /api/chat/sessions/{session_id}`
- `PUT /api/chat/sessions/{session_id}/status`
- `GET /api/chat/sessions/{session_id}/workspace`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/history/{session_id}`
- `GET /api/chat/state/{session_id}`
- `GET /api/chat/snapshots/{session_id}`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`
- `POST /api/chat/feedback`
- `GET /api/chat/feedbacks`
- `GET /api/chat/feedbacks/summary`

Semantic：

- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`

Admin Runtime：

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
- `GET /api/admin/runtime/sessions/{session_id}/history`
- `GET /api/admin/runtime/sessions/{session_id}/snapshots`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/risk-summary`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `POST /api/admin/runtime/vector/prewarm`
- `POST /api/admin/runtime/retention/purge`

Admin Metadata / Examples / Trace / Feedback / Eval / Users / Roles：

- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `GET /api/admin/metadata/documents/{name}`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`
- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `PUT /api/admin/examples/{example_id}`
- `POST /api/admin/examples/bulk`
- `GET /api/admin/traces`
- `GET /api/admin/traces/{trace_id}`
- `GET /api/admin/feedbacks`
- `GET /api/admin/feedbacks/summary`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `POST /api/admin/eval/cases/{case_id}/replay`
- `POST /api/admin/eval/run`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`
- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `DELETE /api/admin/users/{user_id}`
- `GET /api/admin/roles`
- `PUT /api/admin/roles/{role_name}`

## Replay / Eval

- `replay`：用当前链路重跑历史 trace。
- `materialize-case`：把真实 trace 沉淀为 eval case。
- `materialize-example`：把人工确认过的 trace 沉淀为 SQL few-shot 样例。
- `eval/evaluation_cases.json`：保存回归样本。
- `examples/nl2sql_examples.template.json`：保存可进入 prompt 的人工确认样例。

语义配置检查：

```bash
python3 backend/domain_config_lint.py
```

## 目录结构

- `app/api/routes`：HTTP 路由。
- `app/core`：应用装配、settings、异常和取消处理。
- `app/models`：请求、响应、会话、检索、trace、workspace、auth、eval 模型。
- `app/repositories`：runtime 数据库仓库和 metadata 仓库。
- `app/services`：QuestionContext、retrieval、prompt、LLM、SQL 校验、执行、answer、会话、审计、管理、eval 和 auth。
