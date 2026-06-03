# 前端

前端是独立的 `Vite + React + TypeScript` 工作台，默认通过 Vite 代理转发到后端 API。

## 当前界面结构

当前前端已经切到“会话工作台 + 详情侧栏 + 管理中心”结构：

- 左侧：会话列表、登录用户信息、管理员视图切换
- 中间：消息流、欢迎态快捷问题卡片、输入框
- 右侧：详情侧栏，包含 `结果 / SQL / Trace / 状态`
- 管理员额外可切到管理中心，查看 runtime 状态、日志、用户、角色、反馈和 replay
- 管理中心可以重载元数据、重建向量索引，并在最近查询日志上直接 replay
- 管理员可以在用户管理区域授予或移除 `chitchat` 角色；该角色只有在后端开启 `ENABLE_CHITCHAT_MODE=true` 时才会实际生效
- 后端管理接口支持 runtime trace 物化 eval case / example；物化 example 后，新样例会参与后端 retrieval 和 SQL prompt，受影响向量会重建并持久化
- 当前前端管理中心主要暴露状态、日志、replay、索引刷新和用户/角色管理

## 当前交互规则

### 流式提问进度

- 工作台优先调用 `POST /api/chat/query/stream`
- SSE 事件类型包括 `accepted`、`stage`、`completed`、`failed`
- 进度卡显示当前阶段、状态和 `trace_id`
- 如果流式请求没有返回业务响应，前端会按 `failed` 事件或请求错误展示失败状态

### 欢迎态快捷问题

- 快捷问题卡片只会在空会话时显示
- 一旦会话里已经有消息，界面会切回真实消息流，不再保留欢迎态快捷栏

### 结果卡

每条 assistant 消息如果带 `trace_id`，消息下方会出现结果卡，展示：

- 当前业务域
- 执行状态
- 返回行数
- 前几列和前几行预览
- `查看详情`
- `下载`

说明：

- 前端结果卡和详情侧栏默认展示用户友好的中文状态文案，例如 `无结果`、`需澄清`
- 原始后端状态枚举仍然保留在 trace / runtime log / SQL audit 等调试接口中

点击 `查看详情` 后，会把右侧详情面板切换到该消息对应的 `trace_id`。

### 详情面板与下载

- 普通登录用户也可以打开详情面板
- `SQL` 面板展示本轮生成 SQL、SQL 输入上下文和上下文摘要
- `SQL` 面板显示 SQL 校验 warning 数，warning 详情可在 trace / SQL audit 中查看
- `结果` 面板显示本轮总耗时，优先读取 trace 的 `chat_total.elapsed_ms`，再回退到 query log 或执行耗时
- 结果下载只受会话/Trace 归属校验控制，不再做额外权限裁剪

### 移动端

- 移动端通过顶部工具栏控制左侧会话栏和右侧详情栏
- 移动端使用遮罩层和侧栏打开/关闭状态承载会话栏与详情栏

## 数据加载方式

前端当前的主数据入口不是零散接口拼装，而是：

- `GET /api/chat/sessions/{session_id}/workspace`

这份 `workspace` 响应一次性返回：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- `trace_artifacts`

前端会基于这些数据：

- 渲染消息历史
- 给每条 assistant 消息挂对应结果卡
- 用 `activeTraceId` 决定右侧详情面板展示哪一次查询
- 在会话重开时恢复最近一次结果、trace 和状态

保留的 `history / state / query-logs / trace` 接口主要用于调试和后台管理，不再是主工作台首选加载路径。

## 运行

安装依赖：

```bash
npm install
```

启动开发服务器：

```bash
npm run dev
```

默认代理后端到 `http://127.0.0.1:8000`。如果后端地址不同，可在启动前设置：

```bash
VITE_API_ORIGIN=http://127.0.0.1:9000 npm run dev
```

生产构建：

```bash
npm run build
```

## 主要 API 依赖

- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/query-logs`
- `POST /api/chat/query`
- `POST /api/chat/query/stream`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`
- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
- `GET /api/admin/runtime/query-logs`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `GET /api/admin/metadata/overview`
- `POST /api/admin/metadata/reload`
- `POST /api/admin/runtime/vector/prewarm`
- `GET /api/admin/feedbacks/summary`
- `GET /api/admin/eval/summary`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `DELETE /api/admin/users/{user_id}`
- `GET /api/admin/roles`

## 当前用户侧不再强调的内容

用户工作台不再在顶部单独展示：

- `业务域`
- `辅助语义对象`
- `指标`

这些信息仍然存在于：

- 右侧详情面板中的分类/检索摘要
- 管理中心的元数据概览

这样做是为了让主界面更聚焦于“提问、结果、排查”，而不是把辅助语义概念一直暴露给普通用户。
