# Text2SQL

<p align="center">
  <b>中文业务问题 → 只读 Oracle SQL → 答案</b><br/>
  Semantic assets + LLM generation + validated execution
</p>

Text2SQL 是面向中文业务问题的 Oracle 查询工作台。用户用自然语言提问，后端从会话绑定的已发布语义版本中加载真实表结构、业务知识、样例和 join pattern，生成 Oracle SQL，经过校验、执行和审计后返回结果。

业务事实由使用者在管理中心配置并发布，不写在 Python、React 或仓库级运行时 JSON 中。一个实例只连接一个 Oracle 业务数据库；本地 SQLite 只保存产品状态、语义版本和运行证据，embedding 保存为可重建文件缓存。

## 配置与查询闭环

```text
Oracle Schema 同步
  -> 物理目录
  -> 四类语义草稿（表结构 / 业务知识 / Join Pattern / 问题示例）
  -> 发布不可变版本
  -> 新会话绑定该版本
  -> 检索 / Prompt / Validator 全程使用同一版本
```

生产查询没有 `semantic/*.json` 或 `examples/*.json` fallback，也不会根据内置表名猜业务域。业务域、表名、字段语义、时间语义和规则均来自发布版本。`tests/fixtures/` 和 `eval/retrieval_cases.json` 中的模拟业务名称只用于离线开发验证，不进入生产镜像；管理员评测 case 存在 SQLite 运行库中。


## 语义资产与业务数据

语义资产是「如何查询」的说明书（表结构、业务知识、关联路径、样例），不是业务明细的副本；真实行数据只在 Oracle 业务库中，引擎生成只读 SQL 后去那里取数。

<p align="center">
  <img src="docs/semantic-assets-vs-business-data.png" alt="语义资产与业务数据：Playbook vs 真实数据" width="100%" />
</p>

## 快速启动

准备环境文件。查询需要可用的 LLM 配置和 `AUTH_TOKEN_SECRET`；启用向量检索时再配置 `VECTOR_API_KEY`：

```bash
cp env.example .env
docker compose up -d --build
```

启动后访问：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`

`docker-compose.yml` 会启动：

- `text2sql-frontend`：Nginx 托管前端静态文件，并反代 `/api`、`/health`。
- `text2sql-backend`：FastAPI 后端。
- `text2sql-oracle`：业务库，默认用户 `admin/admin123`，服务名 `FREEPDB1`。
- `runtime-data`：后端挂载的数据卷，保存 SQLite runtime 库和可重建向量缓存。

不要把 `docker compose down -v` 当作常规重启命令；它会删除 Oracle 和 runtime 数据卷。

## 本机开发

```bash
cp env.example .env
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
docker compose up -d oracle
scripts/devctl.sh start
scripts/devctl.sh status
```

也可以只操作某个服务：

```bash
scripts/devctl.sh restart backend
scripts/devctl.sh logs frontend
```

## 关键配置

`.env` 至少确认这些值：

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="sqlite:///./runtime/runtime.db"
VECTOR_CACHE_DIR="./runtime/vector-cache"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen/Qwen3-14B"
VECTOR_API_KEY="your_embedding_api_key"
AUTH_TOKEN_SECRET="change-me"
```

常用开关：

- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索。
- `VECTOR_CACHE_DIR`：可删除并重建的向量缓存目录，不进入 runtime 数据库。
- `VECTOR_API_KEY`：启用向量检索时必须可用；如果向量服务和主 LLM 共用同一个 key，可以填同一个值。
- `PREWARM_VECTOR_RETRIEVAL=true`：发布语义版本时预热该版本的向量索引。
- embedding 未配置或临时不可用时，查询降级到 BM25，并在运行状态和 trace 中记录原因。
- `LLM_MAX_RETRIES`：QuestionContext 和 SQL 首轮生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair 重试次数。
- `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_ENTRIES`：进程内 LLM prompt cache。
- `DEFAULT_SQL_LIMIT` / `HIGH_RISK_SQL_LIMIT`：SQL 结果限制和风险标记阈值。
- `SQL_TIMEOUT_SECONDS` / `EXECUTION_MAX_ROWS`：SQL 执行超时和单次最大返回行数。

## 数据初始化

首次启动时会自动初始化：

- Oracle 容器创建本地开发用户，但不预置任何业务表或模拟业务模型。
- 后端创建 SQLite runtime 文件并重复安全地执行 [sql/runtime_store.sql](sql/runtime_store.sql)。

空 runtime 不会导入仓库样例。请将 `BUSINESS_DATABASE_URL` 指向用户自己的 Oracle 数据库，或自行向本地 Oracle 导入待查询 schema。服务启动后先在管理中心执行 Schema 同步，完善四类草稿，再显式发布第一个语义版本；发布前不能创建可查询会话。

## 文档

- [docs/DEPLOYMENT_AND_USAGE.md](docs/DEPLOYMENT_AND_USAGE.md)：部署、首次初始化、语义发布、查询、评测、备份和故障排查流程。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：当前架构和必须遵守的产品、部署与数据边界。
- [docs/TODO.md](docs/TODO.md)：尚未完成的增强项和验证缺口。
- [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)：当前实现的运行、API、调试、语义资产维护和验证指南。
- [docs/semantic-assets-vs-business-data.png](docs/semantic-assets-vs-business-data.png)：语义资产与业务数据关系图。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests
python3 backend/domain_config_lint.py
cd frontend && npm run build
```
