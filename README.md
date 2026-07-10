# Text2SQL

<p align="center">
  <img src="docs/architecture-overview.png" alt="Text2SQL 系统架构：中文业务问题 → 只读 Oracle SQL → 答案" width="100%" />
</p>

<p align="center">
  <b>中文业务问题 → 只读 Oracle SQL → 答案</b><br/>
  Semantic assets + LLM generation + validated execution
</p>

Text2SQL 是面向中文业务问题的 Oracle 查询工作台。用户用自然语言提问，后端基于真实表结构、业务知识、样例、join pattern 和检索证据生成 Oracle SQL，经过校验、执行和审计后返回结果，并把运行证据保存到 MySQL runtime 库。

业务事实沉淀在语义资产中（而非代码硬编码）；系统只做加载、检索、组装、安全校验、执行与审计。同一时刻对接单一业务场景。


## 语义资产与业务数据

语义资产是「如何查询」的说明书（表结构、业务知识、关联路径、样例），不是业务明细的副本；真实行数据只在 Oracle 业务库中，引擎生成只读 SQL 后去那里取数。

<p align="center">
  <img src="docs/semantic-assets-vs-business-data.png" alt="语义资产与业务数据：Playbook vs 真实数据" width="100%" />
</p>

## 快速启动

准备环境文件。默认开启向量检索时，需要填写 `OPENAI_API_KEY`、`VECTOR_API_KEY` 和 `AUTH_TOKEN_SECRET`：

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
- `text2sql-mysql`：runtime 库，默认库 `manager`，用户 `admin/admin123`。

不要把 `docker compose down -v` 当作常规重启命令；它会删除 Oracle 和 MySQL 数据卷。

## 本机开发

```bash
cp env.example .env
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
docker compose up -d oracle mysql
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
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
LLM_MODEL="Qwen/Qwen3-14B"
VECTOR_API_KEY="your_embedding_api_key"
AUTH_TOKEN_SECRET="change-me"
```

常用开关：

- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索。
- `VECTOR_API_KEY`：启用向量检索时必须可用；如果向量服务和主 LLM 共用同一个 key，可以填同一个值。
- `PREWARM_VECTOR_RETRIEVAL=true`：启动和 metadata reload 时预热向量索引。
- `LLM_MAX_RETRIES`：QuestionContext 和 SQL 首轮生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair 重试次数。
- `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_ENTRIES`：进程内 LLM prompt cache。
- `DEFAULT_SQL_LIMIT` / `HIGH_RISK_SQL_LIMIT`：SQL 结果限制和风险标记阈值。
- `SQL_TIMEOUT_SECONDS` / `EXECUTION_MAX_ROWS`：SQL 执行超时和单次最大返回行数。

## 数据初始化

新数据卷首次启动时会自动初始化：

- Oracle 执行 [sql/oracle_business_init.sh](sql/oracle_business_init.sh)，创建业务表。
- MySQL 执行 [sql/mysql_runtime_init.sql](sql/mysql_runtime_init.sql)，创建 runtime 库和表。

已有数据卷不会重复执行 Docker 初始化脚本。需要手动补表时执行：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
docker exec -i text2sql-mysql mysql -uadmin -padmin123 manager < sql/runtime_store.sql
```

测试数据可从 `test_data.xlsx` 生成 Oracle insert 脚本：

```bash
python3 scripts/import_test_data_to_oracle.py
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_test_data.sql
```

## 文档

- [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)：唯一主文档。架构、运行、API、调试、语义资产维护和验证入口。
- [docs/architecture-overview.png](docs/architecture-overview.png)：系统架构总览图（README 页头同图）。
- [docs/semantic-assets-vs-business-data.png](docs/semantic-assets-vs-business-data.png)：语义资产与业务数据关系图。
- [docs/RETRIEVAL_ACCURACY_REVIEW.md](docs/RETRIEVAL_ACCURACY_REVIEW.md)：检索准确率与运行效率评审，含已处理项的实施记录。
- [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md)：结构性重构评审快照（类职责、分层、技术债务）。
- [docs/TODO.md](docs/TODO.md)：尚未实现但已经形成方向约束的工程待办（当前为 PromptBuilder 拆分）。

## 验证

```bash
python3 -m unittest discover -s tests
python3 backend/domain_config_lint.py
```
