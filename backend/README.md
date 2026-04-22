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

- 优先读取仓库根目录的 `.env`
- 如果不存在，则读取仓库根目录的 `env`
- 当前已兼容 `DB_URI`、`OPENAI_API_BASE`、`LLM_MODEL`

运行时存储：

- 默认使用 `RUNTIME_STORAGE_MODE=file`
- 会话、审计、反馈、评测 run、认证用户会落到仓库根目录 `runtime_data/`

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
- 提供评测 case / replay run 骨架

## API

- `GET /health`
- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`
- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
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
- `GET /api/admin/runtime/status`
- `POST /api/admin/database/bootstrap-semantic-views`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `GET /api/admin/eval/runs`
- `POST /api/admin/eval/run`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}`
- `GET /api/chat/history/{session_id}`
- `GET /api/chat/state/{session_id}`
- `POST /api/chat/feedback`
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

- 企业 SSO / OIDC
- 向量检索 / BM25 / pgvector
- 数据库级持久化元数据表
- 更强的 AST 级 SQL 解析库
- 稳定可达的数据库网络环境

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
