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
- `ENABLE_CHITCHAT_MODE=false`：控制闲聊能力。
- `LLM_MAX_RETRIES`：QuestionContext 和 SQL 首轮生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair 重试次数。
- `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_ENTRIES`：进程内 LLM prompt cache。
- `SQL_TIMEOUT_SECONDS`、`EXECUTION_MAX_ROWS`：SQL 执行超时和最大返回行数。

## 启动检查

容器装配会 fail-fast 检查：

- business DB 连通性。
- runtime DB 连通性。
- runtime schema 初始化、补列和补索引。
- metadata 文件可读性和 JSON 结构。
- `sqlglot` 与 Oracle AST 校验器可用性。
- LLM 和向量检索配置满足开启条件。

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
3. 构建轻量 `classification` 和 `QueryPlan` 响应载体。
4. `RetrievalService` 检索表结构、业务知识、样例、join pattern 和向量命中。
5. `PromptBuilder` 组装 Oracle SQL prompt。
6. `LLMClient` 生成 SQL，必要时按 validator 或执行错误 repair。
7. `SqlValidator` 校验只读、单语句、真实表字段、Oracle 边界和结果限制。
8. `SqlExecutor` 执行 Oracle 查询。
9. `AnswerBuilder` 构造响应。
10. 保存 trace、query log、retrieval log、SQL audit、消息和下一轮 session state。

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
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`
- `POST /api/chat/feedback`

Semantic：

- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`

单步调试：

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/sql`
- `POST /api/query/execute`

Admin Runtime：

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
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

Admin Metadata / Examples / Eval / Users：

- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`
- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `PUT /api/admin/examples/{example_id}`
- `POST /api/admin/examples/bulk`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `POST /api/admin/eval/run`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `DELETE /api/admin/users/{user_id}`
- `GET /api/admin/roles`

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
