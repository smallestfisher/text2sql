# Frontend

## Scope

当前前端提供一版可联调的 Web 壳层，覆盖：

- 登录 / 初始化管理员
- 会话列表与聊天工作台
- 结果、SQL、Trace、State 侧栏查看
- 用户反馈提交
- 管理台运行状态、用户管理、日志汇总

不包含：

- 复杂可视化
- 示例库内容编辑器
- 企业级权限建模

## Run

安装依赖：

```bash
cd frontend
npm install
```

启动开发环境：

```bash
npm run dev
```

默认通过 Vite 代理把 `/api` 和 `/health` 转发到 `http://127.0.0.1:8000`。

如果后端地址不同，可以设置：

```bash
VITE_API_ORIGIN=http://127.0.0.1:9000 npm run dev
```

## Build

```bash
cd frontend
npm run build
```

## Notes

- Token 保存在浏览器 `localStorage`
- 前端会根据当前用户的 `can_view_sql` 和 `can_execute_sql` 做最小权限感知
- 页面重点是联调和运营视角，不做过度装饰
