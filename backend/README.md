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

当前后端实现的是“整体架构骨架 + R0/R1/R2/R3/R4/R5/R6 第一版能力”：

- 加载语义层配置
- 语义解析
- 问题分类
- 根据问题生成最小 Query Plan
- 对 Query Plan 做结构化校验
- 生成草案 SQL
- 对 SQL 做基础只读校验
- 生成下一轮 `session_state`
- 注入基础数据权限过滤
- 提供只读执行器和 SQL 治理骨架
- 提供会话仓库与历史接口，并支持文件持久化
- 提供结构化检索 explain、示例库校验与管理接口
- 提供编排器、审计追踪和路由分层
- 提供 LLM prompt builder 和 OpenAI-compatible LLM client
- 提供 DB connector、answer builder、middleware、settings、异常处理
- 提供 token 登录、bootstrap-admin、用户与角色骨架
- 提供基础登录鉴权、管理员接口控制、用户会话归属与反馈管理
- 提供评测 case / replay run 骨架

当前阶段说明：

- 当前主要以测试数据、测试问法和待收敛规则为主，不以最终生产口径为假设前提
- 当前更强调“架构骨架、语义对象、结构化链路、可调试性”先搭起来，而不是过早做重型落库或性能优化
- 语义视图当前以 `draft / logical_scaffold` 方式存在，既进入 retrieval / planner 链路，也保留 SQL 草案，但不要求已经是最终数据库对象
- 语义视图脚手架说明见 [SEMANTIC_VIEW_SCAFFOLD_PLAN.md](/home/y/llm/new/SEMANTIC_VIEW_SCAFFOLD_PLAN.md)

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
- `POST /api/admin/database/bootstrap-semantic-views`
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

当前还未接入：

- 稳定可达的数据库网络环境
- 真实向量库与更完整的向量索引基础设施

当前已补一版前端工作台，见 [frontend/README.md](/home/y/llm/new/frontend/README.md)：

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
  - 文件持久化仓库、metadata 仓库
- `app/services`
  - 语义解析、分类、规划、编译、策略、权限、执行、会话、审计、prompt、llm、answer、metadata、evaluation、auth
