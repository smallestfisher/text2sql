# Text2SQL

面向业务分析问题的 LLM-first Text2SQL 工程。当前主链路已经从“本地规则/模板拼 SQL”切换为“LLM 基于真实表结构和业务知识直接生成 SQL，再由校验器、权限层和执行器治理”。

## 当前状态

- `tables.json` 是真实数据库表和字段描述的主来源
- `business_knowledge.json` 是主业务知识来源
- SQL、分类、相关性判断 prompt 目前统一以中文自然语言指令为主
- PromptBuilder 只选择当前问题相关的 schema、业务知识和 few-shot，避免 token 膨胀
- 前端会话恢复的主入口是 `GET /api/chat/sessions/{session_id}/workspace`
- 不要求真实数据库预建额外分析对象；复杂横表逻辑由 LLM 在 SQL 中展开并由校验器治理

## 快速启动

### Backend

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --app-dir .
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

默认前端会代理到 `http://127.0.0.1:8000`。如需改后端地址，可在启动前设置 `VITE_API_ORIGIN`。

### Runtime 库

- 业务查询库优先读取 `BUSINESS_DATABASE_URL`
- 运行时库优先读取 `RUNTIME_DATABASE_URL`
- 未配置 `RUNTIME_DATABASE_URL` 时，会基于业务库连接派生并默认使用 `manager` 数据库
- 首次启动会尝试自动建库、建表和补增量列

如果你复用了旧的 runtime 库，登录时报：

```text
Unknown column 'can_download_results' in 'field list'
```

说明运行时表结构没升级到最新版本。优先用有 `ALTER TABLE` 权限的账号重启服务；如果运行账号没有变更表结构权限，就手动执行 [sql/runtime_store.sql](sql/runtime_store.sql) 并补齐 `RuntimeStoreInitializer` 里定义的增量列。

## 文档导航

- [TEXT2SQL_ARCHITECTURE.md](TEXT2SQL_ARCHITECTURE.md)：LLM-first 架构、职责边界、配置规则边界和 demand 横表原则
- [DEBUG_PLAYBOOK.md](DEBUG_PLAYBOOK.md)：单题调试、真实联调、样本沉淀和离线回归入口
- [backend/README.md](backend/README.md)：后端运行方式、配置、API、runtime 库和回归说明
- [frontend/README.md](frontend/README.md)：前端工作台、详情侧栏、权限和数据加载方式

## 一句话原则

遇到准确率问题时，优先修 `tables.json`、`business_knowledge.json`、few-shot、prompt 上下文和 validator；不要把系统重新拉回“大量场景规则 + 本地 SQL 模板”的旧路径。
