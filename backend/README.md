# Backend

## Run

安装依赖：

```bash
pip install -r backend/requirements.txt
```

启动服务：

```bash
uvicorn backend.app.main:app --reload --app-dir .
```

## 配置读取

- 终端日志默认输出到 stdout
- 日志级别通过 `LOG_LEVEL` 控制，默认 `INFO`
- 每条日志会附带 `request_id` 和 `trace_id`
- 优先读取仓库根目录 `.env`
- 如果 `.env` 不存在，则回退读取仓库根目录 `env`
- 业务查询库优先读取 `BUSINESS_DATABASE_URL`
- 运行时库优先读取 `RUNTIME_DATABASE_URL`
- 未显式配置 `RUNTIME_DATABASE_URL` 时，会基于业务库连接自动派生并使用 `manager` 数据库
- 可通过 `RUNTIME_DATABASE_NAME` 修改默认运行时数据库名
- 业务库兼容旧字段 `DATABASE_URL` / `DB_URI`

## Current Scope

当前后端实现的是“LLM-first 的 Text2SQL 主链路 + SQL 治理 + runtime 会话工作台支撑”：

- 加载真实表结构描述、结构化业务知识和辅助语义配置
- 进行语义解析、问题分类和 relevance guard
- 生成 Query Plan 作为 LLM SQL 生成约束
- 由 LLM 直接基于真实表和业务知识生成 MySQL SQL
- PromptBuilder 只选择当前 Query Plan 相关表结构、知识块和场景 few-shot，避免 prompt 膨胀
- SQL 校验器做只读、安全、表字段范围、权限、时间/版本、LIMIT 和风险治理
- SQL 校验或执行失败时，触发一次 LLM SQL repair
- 生成下一轮 `session_state`
- 注入基础数据权限过滤
- 提供会话仓库、workspace 聚合接口、trace 恢复和 response snapshot
- 提供查询日志、SQL 审计、反馈、replay、eval case 和管理接口

当前阶段的明确边界：

- 不再要求数据库预建额外分析对象
- `business_knowledge.json` 只参与 prompt 上下文选择，不参与本地 SQL 拼接
- 对横表和复杂口径，优先通过 prompt / few-shot / repair loop 驱动 LLM 生成 SQL，再由 validator 治理
- `GET /api/chat/sessions/{session_id}/workspace` 是前端会话恢复的主入口

## Runtime 存储与升级

运行时数据默认落到运行时数据库，包括：

- 用户、角色和权限
- 会话、消息和状态快照
- query log、trace、SQL audit、feedback
- evaluation runs

运行时表定义见 [sql/runtime_store.sql](../sql/runtime_store.sql)。

首次启动时，服务会尝试：

- 建库
- 建表
- 补增量列
- 补常用索引

### 老 runtime 库升级提示

如果你复用的是旧 runtime 库，且运行账号没有 `ALTER TABLE` 权限，启动后可能不会自动补齐新列。常见症状是登录时报：

```text
Unknown column 'can_download_results' in 'field list'
```

这说明 `users` 表还是旧结构。处理方式：

1. 优先用有 `CREATE/ALTER` 权限的账号重启服务，让 `RuntimeStoreInitializer` 自动补表结构。
2. 如果运行账号不允许改表，就手动执行 [sql/runtime_store.sql](../sql/runtime_store.sql)，并补齐当前增量列：
   - `users.can_download_results`
   - `query_logs.plan_risk_level`
   - `query_logs.plan_risk_flags_json`
   - `query_logs.sql_risk_level`
   - `query_logs.sql_risk_flags_json`
   - `sql_audit_logs.plan_risk_level`
   - `sql_audit_logs.plan_risk_flags_json`
   - `sql_audit_logs.sql_risk_level`
   - `sql_audit_logs.sql_risk_flags_json`

## API

### Health

- `GET /health`

### Semantic

- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`

### Auth

- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`
- `POST /api/auth/stub-login`

### Admin Metadata

- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `GET /api/admin/metadata/documents/{name}`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`

### Admin Examples / Trace / Feedback

- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `PUT /api/admin/examples/{example_id}`
- `POST /api/admin/examples/bulk`
- `GET /api/admin/traces`
- `GET /api/admin/traces/{trace_id}`
- `GET /api/admin/feedbacks`
- `GET /api/admin/feedbacks/summary`

### Admin Runtime

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
- `GET /api/admin/runtime/sessions/{session_id}/history`
- `GET /api/admin/runtime/sessions/{session_id}/snapshots`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/risk-summary`
- `POST /api/admin/runtime/retention/purge`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`

说明：

- `examples/nl2sql_examples.template.json` 现在默认可以为空。
- 在线样例只应从真实调试链路通过 `materialize-example` 物化进入，不再手写假设样例。
- `eval/evaluation_cases.json` 也只应保留真实问题或真实 trace 物化出的回归样本，不再维护假设 case。

### Admin Users / Roles

- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `DELETE /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}/data-scope`
- `PUT /api/admin/users/{user_id}/field-visibility`
- `GET /api/admin/roles`
- `PUT /api/admin/roles/{role_name}`

### Admin Eval

- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `POST /api/admin/eval/cases/{case_id}/replay`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`
- `POST /api/admin/eval/run`

### Chat / Sessions

- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}`
- `PUT /api/chat/sessions/{session_id}/status`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/history/{session_id}`
- `GET /api/chat/state/{session_id}`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/snapshots/{session_id}`
- `POST /api/chat/query`
- `POST /api/chat/feedback`
- `GET /api/chat/feedbacks`
- `GET /api/chat/feedbacks/summary`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`

### Query

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`

## Example

```bash
curl -X POST http://127.0.0.1:8000/api/query/classify \
  -H "Content-Type: application/json" \
  -d '{"question":"查询2026年4月CELL工厂计划投入量"}'
```

## 当前调试主路径

推荐按这条路径调问题：

1. 在前端工作台或 `POST /api/chat/query` 复现问题
2. 用 `GET /api/chat/sessions/{session_id}/workspace` 看消息、状态、`latest_response` 和 `trace_artifacts`
3. 看 `GET /api/chat/traces/{trace_id}`、`/sql-audit`、`/retrieval`
4. 对历史问题优先走 `POST /api/admin/runtime/query-logs/{trace_id}/replay`
5. 有代表性的失败样本再物化为 eval case 或 example

## Offline Regression

在没有运行时 MySQL、业务库、真实 LLM 或真实执行环境的情况下，可以直接跑离线回归，当前主要覆盖 `classification / query_plan / permission_filter` 这几层：

```bash
.venv/bin/python backend/offline_regression.py --failures-only
```

只跑指定 case：

```bash
.venv/bin/python backend/offline_regression.py \
  --case-id eval_plan_actual_follow_up_001 \
  --case-id eval_demand_follow_up_001
```

语义层配置 lint：

```bash
.venv/bin/python backend/domain_config_lint.py
```

输出 JSON：

```bash
.venv/bin/python backend/offline_regression.py --json
```

把完整报告写到文件：

```bash
.venv/bin/python backend/offline_regression.py --output tmp/offline-regression.json
```

把摘要和失败项分别落盘：

```bash
.venv/bin/python backend/offline_regression.py --report-dir tmp/offline-regression
```

说明：

- 离线回归不会连接数据库，也不会写 runtime 审计表
- 当前会复用 `eval/evaluation_cases.json`
- 当前主要用于收敛分类、规划和权限注入；LLM-first SQL 生成与 SQL 校验需要在 live 或 replay 链路验证
- 控制台输出会包含 `question_type / scenario / failure_types` 的聚合统计
- `--report-dir` 会输出 `summary.json` 和 `failures.json`
- `.github/workflows/offline-regression.yml` 会执行 JSON 校验、semantic lint、`compileall` 和离线回归

## 相关阅读

- [TEXT2SQL_ARCHITECTURE.md](../TEXT2SQL_ARCHITECTURE.md)
- [frontend/README.md](../frontend/README.md)
- [REAL_DATA_TUNING_PLAYBOOK.md](../REAL_DATA_TUNING_PLAYBOOK.md)
- [REAL_SCENARIO_DEBUG_GUIDE.md](../REAL_SCENARIO_DEBUG_GUIDE.md)

## Structure

- `app/api/routes`：HTTP 路由层
- `app/core`：应用装配、settings、异常处理
- `app/models`：请求、响应、会话、检索、追踪、workspace 模型
- `app/repositories`：运行时数据库仓库、metadata 仓库
- `app/services`：语义解析、分类、规划、权限、执行、会话、审计、prompt、LLM、answer、evaluation、auth
