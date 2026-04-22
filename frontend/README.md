# Frontend

独立的 `Vite + React + TypeScript` 用户端，默认通过 `Vite` 代理转发到后端 API。

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
