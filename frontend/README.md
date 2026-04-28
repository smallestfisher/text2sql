# Frontend

独立的 `Vite + React + TypeScript` 工作台，默认通过 `Vite` 代理转发到后端 API。

## 当前界面结构

当前前端已经切到“会话工作台 + 详情侧栏 + 管理中心”结构：

- 左侧：会话列表、登录用户信息、管理员视图切换
- 中间：消息流、欢迎态快捷问题卡片、输入框
- 右侧：详情侧栏，包含 `结果 / SQL / Trace / 状态`
- 管理员额外可切到管理中心，查看 runtime 状态、日志、用户、反馈和 replay
- 管理员通过 runtime trace 物化 example 后，新样例当前可立即参与后端 retrieval / SQL prompt；通常不需要重启服务

## 当前交互规则

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
- `SQL` 面板始终展示本轮生成 SQL 和 Query Plan
- 结果下载只受会话/Trace 归属校验控制，不再做额外权限裁剪

### 移动端

- 移动端通过顶部工具栏控制左侧会话栏和右侧详情栏
- 当前代码已经处理了移动端遮罩层和侧栏打开/关闭状态，不再依赖旧的弹窗式详情实现

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

## Run

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

- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/sessions/{session_id}/workspace`
- `POST /api/chat/query/stream`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`
- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/query-logs`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`

## 当前用户侧不再强调的内容

用户工作台不再在顶部单独展示：

- `业务域`
- `辅助语义对象`
- `指标`

这些信息仍然存在于：

- 右侧详情面板中的分类/检索摘要
- 管理中心的元数据概览

这样做是为了让主界面更聚焦于“提问、结果、排查”，而不是把辅助语义概念一直暴露给普通用户。
