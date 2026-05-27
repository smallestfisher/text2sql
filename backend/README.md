# Backend

FastAPI 后端，负责 Text2SQL 主链路、runtime 落库、管理接口、eval/replay 和前端工作台支撑。架构细节见 [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)，调试流程见 [../docs/DEBUG_PLAYBOOK.md](../docs/DEBUG_PLAYBOOK.md)。

## 运行

```bash
cp env.example .env
pip install -r backend/requirements.txt
docker compose up -d
uvicorn backend.app.main:app --reload --app-dir .
```

也可以用仓库脚本管理后台进程：

```bash
scripts/devctl.sh start backend
scripts/devctl.sh status backend
scripts/devctl.sh logs backend
scripts/devctl.sh stop backend
```

## 关键配置

后端优先读取仓库根目录 `.env`。

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen/Qwen3-14B"
```

固定边界：

- 业务库固定 Oracle，不提供 MySQL 业务库路径。
- runtime 库固定 MySQL，不复用业务库。
- 业务 SQL 生成、repair、AST 解析和 validator 固定使用 Oracle 规则。
- runtime schema 使用 MySQL 表结构。

常用开关：

- `LOG_LEVEL=DEBUG`：输出主链路 `stage_io` 摘要。
- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索。
- `PREWARM_VECTOR_RETRIEVAL=true`：启动和 metadata reload 时同步预热向量索引。
- `ENABLE_CHITCHAT_MODE=false`：控制闲聊回复能力。
- `LLM_MAX_RETRIES`：首轮分类、intent、SQL 生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair fallback 独立重试次数。

## 数据库初始化

本地数据库由根目录 [../docker-compose.yml](../docker-compose.yml) 提供。

- Oracle 首次新 volume 初始化时执行 [../sql/oracle_business_init.sh](../sql/oracle_business_init.sh)，创建业务表。
- MySQL 首次新 volume 初始化时执行 [../sql/mysql_runtime_init.sql](../sql/mysql_runtime_init.sql)，创建 `manager`、`admin/admin123`，并执行 [../sql/runtime_store.sql](../sql/runtime_store.sql)。
- 应用启动后仍会通过 `RuntimeStoreInitializer` 幂等检查 runtime 建库、建表、补列和补索引。

已有 volume 不会重复执行 Docker 初始化脚本。需要手动补表时：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
docker exec -i text2sql-mysql mysql -uadmin -padmin123 manager < sql/runtime_store.sql
```

## 启动检查

容器装配会 fail-fast 检查：

- business DB 连通性和只读超时设置。
- runtime DB 连通性。
- runtime schema 初始化。
- metadata 文件可读性和 JSON 结构。
- `sqlglot` 可用性。
- LLM / 向量检索配置满足开启条件。

任一失败都会阻断启动，不再半可用运行。

## API 分组

Health:

- `GET /health`

Auth:

- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`

Chat / Workspace:

- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/sessions/{session_id}/workspace`
- `POST /api/chat/query`
- `POST /api/chat/query/stream`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`

Query Debug:

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`

Admin Runtime:

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `POST /api/admin/runtime/vector/prewarm`

Admin Metadata / Examples / Eval / Users:

- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`
- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`
- `GET /api/admin/roles`

完整排查路径见 [../docs/DEBUG_PLAYBOOK.md](../docs/DEBUG_PLAYBOOK.md)。

## Eval / Replay

- `replay` 用于对历史 trace 重新跑当前链路。
- `materialize-case` 用于把真实 trace 沉淀为 eval case。
- `materialize-example` 用于把人工确认过的真实 trace 沉淀为 SQL prompt few-shot。
- `eval/evaluation_cases.json` 只保留真实问题或真实 trace 物化出的 case。
- `examples/nl2sql_examples.template.json` 只保留真实、人工确认过的样例。

语义配置 lint：

```bash
python3 backend/domain_config_lint.py
```

## 目录结构

- `app/api/routes`：HTTP 路由层。
- `app/core`：应用装配、settings、异常处理。
- `app/models`：请求、响应、会话、检索、追踪、workspace 模型。
- `app/repositories`：runtime 数据库仓库和 metadata 仓库。
- `app/services`：语义解析、分类、规划、执行、会话、审计、prompt、LLM、answer、evaluation、auth。
