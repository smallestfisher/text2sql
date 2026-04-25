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

配置读取：

- 终端日志默认输出到 stdout
- 日志级别通过 `LOG_LEVEL` 控制，默认 `INFO`
- 每条日志会附带 `request_id` 与 `trace_id`，便于串联请求链路与一次 chat 编排


- 优先读取仓库根目录的 `.env`
- 如果不存在，则读取仓库根目录的 `env`
- 业务查询库优先读取 `BUSINESS_DATABASE_URL`
- 运行时库优先读取 `RUNTIME_DATABASE_URL`
- 未显式配置 `RUNTIME_DATABASE_URL` 时，会基于业务库连接自动派生并使用 `manager` 数据库
- 可通过 `RUNTIME_DATABASE_NAME` 修改默认运行时数据库名
- 业务库仍兼容旧字段 `DATABASE_URL` / `DB_URI`

运行时存储：

- 登录、用户、会话、反馈、审计、评测 run 默认落到运行时数据库
- 运行时表定义见 `sql/runtime_store.sql`
- 启动时会自动执行“建库 + 建表”；默认会在同一 MySQL 实例上创建 `manager` 数据库

## Current Scope

当前后端实现的是“LLM-first 的 Text2SQL 主链路 + SQL 治理与执行骨架”：

- 加载真实表结构描述、业务说明和辅助语义配置
- 语义解析与问题分类
- 根据问题生成 Query Plan 作为 LLM SQL 生成约束
- 由 LLM 直接基于真实表生成 MySQL SQL
- PromptBuilder 只选择当前 Query Plan 相关表结构、业务说明片段和场景 few-shot，避免全量 prompt 膨胀
- SQL 校验器做只读、安全、表字段范围、LIMIT 与风险治理
- SQL 校验或执行失败时，触发一次 LLM SQL repair
- 生成下一轮 `session_state`
- 注入基础数据权限过滤
- 提供只读执行器和 SQL 治理骨架
- 提供会话仓库与历史接口
- 提供结构化检索、示例库和管理接口
- 提供编排器、审计追踪和路由分层
- 提供 LLM prompt builder 和 OpenAI-compatible LLM client
- 提供 DB connector、answer builder、middleware、settings、异常处理
- 提供 token 登录、bootstrap-admin、用户与角色骨架
- 提供基础登录鉴权、管理员接口控制、用户会话归属与反馈管理
- 提供评测 case / replay run 骨架

当前阶段说明：

- 当前主要以真实表结构、真实业务说明和 LLM 生成 SQL 为主，不再要求数据库中预先落库 semantic view
- 语义层现在主要承担解析、检索和约束提示作用，而不是主 SQL 模板来源
- `readme.txt` 会被切片并按当前表、指标、字段选择相关片段，不会整份无条件进入 SQL prompt
- 对横表、复杂口径等问题，优先通过 prompt / few-shot / repair loop 驱动 LLM 生成可执行 SQL，再由校验器治理
- 已移除 semantic view bootstrap 管理接口和 SQL 草案文件，避免真实数据库被辅助视图污染

## API

- `GET /health`
- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`
- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`
- `POST /api/auth/stub-login`
- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `GET /api/admin/metadata/documents/{name}`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`
- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `PUT /api/admin/examples/{example_id}`
- `GET /api/admin/traces`
- `GET /api/admin/traces/{trace_id}`
- `GET /api/admin/feedbacks`
- `GET /api/admin/feedbacks/summary`
- `GET /api/admin/runtime/status`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}/data-scope`
- `PUT /api/admin/users/{user_id}/field-visibility`
- `GET /api/admin/roles`
- `PUT /api/admin/roles/{role_name}`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`
- `POST /api/admin/eval/run`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}`
- `PUT /api/chat/sessions/{session_id}/status`
- `GET /api/chat/history/{session_id}`
- `GET /api/chat/snapshots/{session_id}`
- `GET /api/chat/state/{session_id}`
- `POST /api/chat/feedback`
- `GET /api/chat/feedbacks`
- `GET /api/chat/feedbacks/summary`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`
- `POST /api/chat/query`

## Example

```bash
curl -X POST http://127.0.0.1:8000/api/query/classify \
  -H "Content-Type: application/json" \
  -d '{"question":"查询2026年4月CELL工厂计划投入量"}'
```

## Offline Regression

在没有运行时 MySQL、业务库、真实 LLM 或真实执行环境的情况下，可以直接跑离线回归，
覆盖 `classification / query_plan / permission_filter` 这几层：

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
.venv/bin/python backend/semantic_lint.py
```

输出 JSON：

```bash
.venv/bin/python backend/offline_regression.py --json
```

把完整报告写到文件：

```bash
.venv/bin/python backend/offline_regression.py   --output tmp/offline-regression.json
```

把摘要和失败项分别落盘：

```bash
.venv/bin/python backend/offline_regression.py   --report-dir tmp/offline-regression
```

说明：

- 离线回归不会连接数据库，也不会写 runtime 审计表
- 当前会复用 `eval/evaluation_cases.json`
- 当前主要用于收敛分类、规划和权限注入；LLM-first SQL 生成与 SQL 校验需要在 live/replay 链路验证
- 控制台输出现在会包含 `question_type / scenario / failure_types` 的聚合统计
- `--report-dir` 会输出 `summary.json` 和 `failures.json`，方便在本地或 CI 比较回归结果
- `backend/semantic_lint.py` 仍会检查 domain / semantic_view / query_profile / extractor 的关键一致性，但 semantic view 已不再是主执行依赖
- 仓库已补 `.github/workflows/offline-regression.yml`
- `push` / `pull_request` 时会自动执行 JSON 校验、semantic lint、`compileall`、离线回归，并上传回归 artifact
- 这条流水线不依赖 MySQL 或业务数据连接，适合做规则层回归门禁

当前还未接入：

- 稳定可达的数据库网络环境
- 真实向量库与更完整的向量索引基础设施

进入真实数据与真实问题联调前，建议先阅读：

- [REAL_DATA_TUNING_PLAYBOOK.md](/home/yang/code/text2sql/REAL_DATA_TUNING_PLAYBOOK.md)
- [REAL_SCENARIO_DEBUG_GUIDE.md](/home/yang/code/text2sql/REAL_SCENARIO_DEBUG_GUIDE.md)
- [OFFLINE_OPTIMIZATION_PLAN.md](/home/yang/code/text2sql/OFFLINE_OPTIMIZATION_PLAN.md)

当前已补一版前端工作台，见 [frontend/README.md](/home/yang/code/text2sql/frontend/README.md)：

- 登录 / 初始化管理员
- 聊天工作台
- SQL / Trace / State 侧栏
- 管理台基础页面

当前权限范围：

- 以登录鉴权和基础管理员控制为主
- 不以复杂组织树/RBAC/ABAC 为当前后端目标

## Structure

- `app/api/routes`
  - HTTP 路由层
- `app/core`
  - 应用装配、settings、异常处理
- `app/models`
  - 请求、响应、会话、检索、追踪、answer 模型
- `app/repositories`
  - 运行时数据库仓库、metadata 仓库
- `app/services`
  - 语义解析、分类、规划、权限、执行、会话、审计、prompt、llm、answer、metadata、evaluation、auth
