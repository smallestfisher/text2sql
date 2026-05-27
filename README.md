# Text2SQL

LLM-first Text2SQL 工程：用户用自然语言提问，系统基于真实表结构、语义配置、业务知识和检索上下文生成 Oracle SQL，再经过校验、执行、审计和工作台展示。

## 当前事实

- 业务数据库固定为 Oracle，连接串来自 `BUSINESS_DATABASE_URL`。
- runtime 数据库固定为 MySQL，连接串来自 `RUNTIME_DATABASE_URL`。
- 本地 `docker-compose.yml` 同时提供 Oracle 业务库和 MySQL runtime 库。
- Oracle SQL 生成、repair、`sqlglot` 解析和 validator 都固定使用 Oracle 规则。
- MySQL runtime 保存用户、会话、trace、query log、SQL audit、feedback、eval 和 retrieval corpus。
- LLM、`sqlglot`、业务库、runtime 库、metadata 文件都是启动时 fail-fast 依赖。
- 准确率问题优先修语义资产、业务知识、retrieval、prompt 和 validator，不回退到本地 SQL 模板分支。

## 快速启动

1. 准备环境文件和依赖。

```bash
cp env.example .env
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

2. 启动本地数据库。

```bash
docker compose up -d
```

`docker-compose.yml` 会启动：

- `text2sql-oracle`：业务库，默认用户 `admin/admin123`，服务名 `FREEPDB1`。
- `text2sql-mysql`：runtime 库，默认库 `manager`，用户 `admin/admin123`。

新 volume 首次启动时会自动初始化：

- Oracle 执行 [sql/oracle_business_init.sh](sql/oracle_business_init.sh)，创建业务表。
- MySQL 执行 [sql/mysql_runtime_init.sql](sql/mysql_runtime_init.sql)，创建 runtime 库、用户和表。

如果已经有旧 volume，Docker 初始化脚本不会重复执行；需要手动补表时执行：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
docker exec -i text2sql-mysql mysql -uadmin -padmin123 manager < sql/runtime_store.sql
```

3. 启动应用。

```bash
scripts/devctl.sh start
scripts/devctl.sh status
```

脚本默认启动：

- Backend: `http://127.0.0.1:8000`
- Frontend: `http://127.0.0.1:5173`

也可以单独启动服务：

```bash
scripts/devctl.sh restart backend
scripts/devctl.sh logs frontend
```

## 核心配置

`.env` 至少需要确认这些值：

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
```

常用开关：

- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索，默认开启。
- `PREWARM_VECTOR_RETRIEVAL=true`：启动和 metadata reload 时同步预热向量索引。
- `ENABLE_CHITCHAT_MODE=false`：默认关闭闲聊回复。
- `LLM_MAX_RETRIES`：首轮分类、intent、SQL 生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair fallback 独立重试次数。

完整后端配置见 [backend/README.md](backend/README.md)。

## 业务数据

业务表结构在 [sql/oracle_business_schema.sql](sql/oracle_business_schema.sql)。测试数据可从 `test_data.xlsx` 生成 Oracle insert 脚本：

```bash
python3 scripts/import_test_data_to_oracle.py
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_test_data.sql
```

## 文档地图

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：端到端架构、核心对象、查询链路、retrieval、SQL 治理、runtime 落库。
- [docs/DEBUG_PLAYBOOK.md](docs/DEBUG_PLAYBOOK.md)：单题排查、trace/replay/materialize/eval 的使用路径。
- [backend/README.md](backend/README.md)：后端运行、配置、API 分组、runtime 存储。
- [frontend/README.md](frontend/README.md)：前端工作台、数据加载方式、主要 API 依赖。

## 维护原则

- 事实型说明只放在当前文档；阶段性计划过期后直接删除。
- 根 README 只保留启动、配置和导航，不承载架构细节。
- API 和后端运行细节放 `backend/README.md`。
- 准确率问题优先修 `semantic/`、`examples/`、retrieval、prompt 和 validator。
