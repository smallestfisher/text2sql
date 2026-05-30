# Text2SQL

Text2SQL 是一个面向中文业务问题的 Oracle 查询工作台。用户用自然语言提问，后端基于真实表结构、业务知识、样例、join pattern 和检索上下文生成 Oracle SQL，经过校验、执行、审计后返回结果，并把运行证据保存到 MySQL runtime 库。

## 当前边界

- 业务库固定为 Oracle，连接串来自 `BUSINESS_DATABASE_URL`。
- runtime 库固定为 MySQL，连接串来自 `RUNTIME_DATABASE_URL`。
- 业务 SQL 生成、repair、AST 解析和校验都按 Oracle 规则运行。
- `semantic/`、`examples/`、`eval/` 是语义资产、检索语料、管理台编辑和评测的共同来源。
- 后端启动时会检查数据库、runtime schema、metadata、`sqlglot`、LLM 和向量检索配置；关键依赖失败会阻断启动。
- 准确率修复优先沉淀到表结构说明、业务知识、样例、join pattern、retrieval、prompt 和 validator。

## Docker 启动

准备环境文件，至少填写 `OPENAI_API_KEY` 和 `AUTH_TOKEN_SECRET`。

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

容器内默认连接串使用 Docker 服务名：

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@oracle:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@mysql:3306/manager"
```

如果只改应用代码，按影响范围重建镜像：

```bash
docker compose up -d --build backend frontend
```

不要把 `docker compose down -v` 当作常规重启命令；它会删除 Oracle 和 MySQL 数据卷。

## 本机开发

安装依赖：

```bash
cp env.example .env
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

只启动数据库：

```bash
docker compose up -d oracle mysql
```

启动应用：

```bash
scripts/devctl.sh start
scripts/devctl.sh status
```

也可以只操作某个服务：

```bash
scripts/devctl.sh restart backend
scripts/devctl.sh logs frontend
```

新数据卷首次启动时会自动初始化：

- Oracle 执行 [sql/oracle_business_init.sh](sql/oracle_business_init.sh)，创建业务表。
- MySQL 执行 [sql/mysql_runtime_init.sql](sql/mysql_runtime_init.sql)，创建 runtime 库和表。

已有数据卷不会重复执行 Docker 初始化脚本。需要手动补表时执行：

```bash
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_business_schema.sql
docker exec -i text2sql-mysql mysql -uadmin -padmin123 manager < sql/runtime_store.sql
```

## 关键配置

`.env` 至少确认这些值：

```env
BUSINESS_DATABASE_URL="oracle+oracledb://admin:admin123@127.0.0.1:1521/?service_name=FREEPDB1"
RUNTIME_DATABASE_URL="mysql+pymysql://admin:admin123@127.0.0.1:3306/manager"
OPENAI_API_KEY="your_llm_api_key"
OPENAI_API_BASE="https://api.siliconflow.cn/v1"
AUTH_TOKEN_SECRET="change-me"
```

常用开关：

- `ENABLE_VECTOR_RETRIEVAL=true`：启用向量检索。
- `PREWARM_VECTOR_RETRIEVAL=true`：启动和 metadata reload 时预热向量索引。
- `ENABLE_CHITCHAT_MODE=false`：控制闲聊能力。
- `LLM_MAX_RETRIES`：QuestionContext 和 SQL 首轮生成重试次数。
- `SQL_REPAIR_MAX_RETRIES`：SQL repair 重试次数。
- `LLM_CACHE_TTL_SECONDS` / `LLM_CACHE_MAX_ENTRIES`：进程内 LLM prompt cache。

## 业务数据

业务表结构在 [sql/oracle_business_schema.sql](sql/oracle_business_schema.sql)。测试数据可从 `test_data.xlsx` 生成 Oracle insert 脚本：

```bash
python3 scripts/import_test_data_to_oracle.py
docker exec -i text2sql-oracle sqlplus -L admin/admin123@//localhost:1521/FREEPDB1 @/dev/stdin < sql/oracle_test_data.sql
```

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：当前架构、主链路、核心对象、SQL 治理和 runtime 落库。
- [docs/DEBUG_PLAYBOOK.md](docs/DEBUG_PLAYBOOK.md)：真实问题答错时的分层排查路径。
- [docs/CONTENT_GUIDELINES.md](docs/CONTENT_GUIDELINES.md)：样例和业务知识库编写规范。
- [backend/README.md](backend/README.md)：后端运行、配置、API 和目录结构。
- [frontend/README.md](frontend/README.md)：前端工作台结构和数据入口。
