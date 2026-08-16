# 部署与使用

本文按当前代码说明 Text2SQL 的部署、首次初始化、语义配置、日常查询和维护流程。当前系统的事实边界是：Oracle 保存真实业务数据，SQLite 保存产品运行状态和已发布语义版本，向量索引是可重建的文件缓存。

## 1. 部署前准备

### 1.1 依赖

单机 Docker 部署需要：

- Docker Engine 和 Docker Compose v2。
- 至少可用的 `8000`、`5173` 端口；如果使用 Compose 内置 Oracle，还需要 `1521`。
- 一个可访问的 OpenAI-compatible LLM endpoint。
- 一个可被后端访问的 Oracle 数据库和 schema。

本机开发还需要 Python 3.11、Node.js 20 和 npm。

### 1.2 业务数据库要求

后端只生成和执行只读 Oracle SQL。生产环境应使用权限受限的 Oracle 账号，只授予目标 schema 的 `SELECT` 和读取元数据所需权限，不要直接使用 DBA 账号。

仓库不再提供业务表、模拟数据或默认语义配置。Compose 内置的 Oracle 只创建空的应用用户；真实业务表必须由部署方提供。

## 2. Compose 部署

### 2.1 创建环境文件

```bash
cp env.example .env
```

本地直接运行后端时，`.env` 使用 `BUSINESS_DATABASE_URL`。Compose 容器内运行后端时，Compose 优先读取 `DOCKER_BUSINESS_DATABASE_URL`，例如连接同一 Compose 网络中的 Oracle：

```env
DOCKER_BUSINESS_DATABASE_URL=oracle+oracledb://admin:admin123@oracle:1521/?service_name=FREEPDB1
```

如果连接外部 Oracle，将它改为容器能够访问的地址，例如：

```env
DOCKER_BUSINESS_DATABASE_URL=oracle+oracledb://readonly:password@oracle.example.internal:1521/?service_name=BUSINESS
BUSINESS_DATABASE_SCHEMAS=APP,REPORT
```

Compose 会优先读取 `DOCKER_BUSINESS_DATABASE_SCHEMAS`，没有该变量时回退到 `BUSINESS_DATABASE_SCHEMAS`。当前 Compose 文件仍定义了一个内置 `oracle` 服务作为本地部署依赖；使用外部 Oracle 时，后端会连接 `DOCKER_BUSINESS_DATABASE_URL`，但建议通过 Compose override 移除内置 Oracle 服务和 backend 的 `depends_on.oracle`，避免启动无用数据库。

Compose 默认将 runtime SQLite 和向量缓存放在 `runtime-data` 卷的 `/app/data` 下。不要把本机路径 `RUNTIME_DATABASE_URL=sqlite:///./runtime/runtime.db` 或 `VECTOR_CACHE_DIR=./runtime/vector-cache` 直接当作容器路径；需要覆盖时分别使用 `DOCKER_RUNTIME_DATABASE_URL` 和 `DOCKER_VECTOR_CACHE_DIR`。

生产环境至少替换以下配置：

```env
OPENAI_API_KEY=真实的模型服务密钥
OPENAI_API_BASE=https://你的兼容接口/v1
LLM_MODEL=实际模型名称
AUTH_TOKEN_SECRET=长度足够且随机的签名密钥
ENABLE_DOCS=false
```

启用向量检索时，再配置：

```env
VECTOR_API_KEY=真实的 embedding 服务密钥
VECTOR_API_BASE=https://你的向量接口/v1
VECTOR_MODEL=实际 embedding 模型
VECTOR_DIMENSIONS=与模型输出一致的维度
```

向量服务不可用时系统会降级到 BM25，不会从旧文件或其他语义版本加载数据。

### 2.2 启动和检查

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f backend
```

访问：

- 工作台：`http://服务器地址:5173`
- 后端健康检查：`http://服务器地址:8000/health`
- OpenAPI 文档：`http://服务器地址:8000/docs`（`ENABLE_DOCS=true` 时可用）

首次启动会自动创建 SQLite runtime 数据库并执行 [sql/runtime_store.sql](../sql/runtime_store.sql)。Oracle 容器可能需要几分钟才会通过健康检查。

日常重启使用：

```bash
docker compose restart backend frontend
```

修改 `.env` 后需要重新创建容器使环境变量生效：

```bash
docker compose up -d --force-recreate backend frontend
```

不要把下面的命令当作普通重启：

```bash
docker compose down -v
```

`-v` 会删除 Compose 管理的 Oracle 和 runtime 数据卷。

## 3. 首次使用

### 3.1 创建管理员

第一次打开工作台时，系统发现 runtime 中没有用户，会显示“创建管理员账户”。设置用户名和密码后登录。这个操作对应：

```http
POST /api/auth/bootstrap-admin
Content-Type: application/json

{"username":"admin","password":"请替换为强密码"}
```

管理员创建后，bootstrap 接口会被禁止再次使用。后续用户通过登录页登录；管理员在管理中心的用户管理中创建 viewer 或 admin 用户。

### 3.2 配置业务数据库

登录管理员账号后，打开“语义工作台”：

1. 在“当前数据库”确认连接状态、schema 范围和当前发布版本。
2. 点击“同步 Schema”，从 Oracle 读取允许范围内的表、字段、主键、外键、视图和列类型。
3. 检查同步结果和警告。同步只更新物理 catalog，不会直接改变线上查询。

如果数据库状态为 disconnected 或同步失败，先检查容器到 Oracle 的网络、账号、服务名和 schema 范围，再重试。

## 4. 创建并发布语义版本

### 4.1 编辑四类草稿

Schema 同步成功后，管理中心会提供四类草稿：

- `tables_metadata`：表、字段、关系、时间字段格式和 `semantic_names`。
- `business_knowledge`：指标口径、业务规则、默认过滤和禁止事项。
- `join_patterns`：稳定的表关联路径和关联条件。
- `examples_template`：人工确认过的问题和 Oracle SQL 示例。

所有业务域、指标、字段语义和样例都由当前使用者填写。前端没有固定业务域列表，也不会根据表名推断业务域。

编辑时注意：

- 表名和字段名必须与 Oracle 实际对象一致。
- `time_fields` 的 `format` 描述物理存储格式；字符串月份/日期不要按 Oracle `DATE` 处理。
- 示例 SQL 必须是只读的 Oracle `SELECT` 或 `WITH ... SELECT`，并使用真实表字段。
- 草稿保存不会改变当前生效版本。

### 4.2 发布

点击“发布新版本”后，系统会依次校验：

1. 草稿结构和表引用。
2. 示例 SQL 的只读性、表引用和 Oracle 语法约束。
3. 当前 catalog hash 与语义草稿的一致性。
4. 该版本的检索语料和向量索引准备情况。

校验成功后生成不可变 release snapshot 并切换 active release。新会话绑定新的 release；已有会话继续使用创建时绑定的版本。发布失败不会改变当前 active release。

在“发布历史”中可以查看版本号、发布人、状态、错误和激活时间。

## 5. 日常查询

1. 用 viewer 或 admin 账号登录工作台。
2. 新建会话，确认页面显示当前发布版本。
3. 用自然语言描述指标、时间范围、维度、筛选和排序。
4. 查看答案、SQL、执行状态和风险提示。
5. 需要排查时，在详情面板查看 Trace、检索证据、Prompt 摘要和 SQL Audit。

每次查询都使用会话绑定的完整 release，不会读取仓库里的语义 JSON，也不会自动切换到其他版本。

### 查询结果不准确时

按以下顺序排查：

1. 查看 QuestionContext 是否正确抽取指标、维度、时间和过滤条件。
2. 查看 Retrieval 是否命中对应的表结构、业务知识、join pattern 和示例。
3. 查看 SQL Prompt 的 selected sources 和时间解析结果。
4. 查看 SQL Validator 和 Oracle 执行错误。
5. 修正对应 draft，重新发布版本，再用原问题重跑。

不要在 Python 或 React 中加入针对某个表名、指标名或业务域的特殊分支。

## 6. 评测和反馈

### 6.1 从真实查询沉淀评测 case

管理员可以在运行日志中打开某条 trace，执行“沉淀为评测 case”。评测 case 保存到 SQLite 的 `evaluation_cases` 表，不再写入仓库 JSON。

也可以调用接口：

```http
POST /api/admin/runtime/query-logs/{trace_id}/materialize-case
Authorization: Bearer <管理员 token>
Content-Type: application/json

{
  "case_id": "case_from_trace_001",
  "scenario": "真实问题回归",
  "coverage_tags": ["metric", "time_filter"],
  "include_prior_context": true,
  "reuse_original_user": true
}
```

### 6.2 运行评测

管理接口：

- `GET /api/admin/eval/cases`：查看 SQLite 中的评测 case。
- `POST /api/admin/eval/cases/{case_id}/replay`：重放单个 case。
- `POST /api/admin/eval/run`：运行一组或全部 case。
- `GET /api/admin/eval/runs`：查看评测运行记录。
- `GET /api/admin/eval/summary`：查看通过率和按维度统计。

仓库中的 `eval/retrieval_cases.json` 只用于离线检索测试和分数探针，不会进入生产镜像，也不会被普通查询链路读取。

## 7. 运维与备份

### 7.1 日志和状态

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 frontend
curl http://127.0.0.1:8000/health
```

管理员工作台还可以查看 runtime 状态、查询日志、SQL audit、反馈、向量预热和 retention purge。

### 7.2 备份

至少备份：

- Oracle 业务数据库：由数据库管理员按现有 Oracle 备份策略执行。
- Compose 的 `runtime-data` 卷：包含用户、会话、发布版本、日志和评测 case。
- `VECTOR_CACHE_DIR` 可以不备份，它是按 release 重建的缓存。

先查看实际卷名：

```bash
docker volume ls | grep runtime-data
docker volume inspect <runtime-data-volume>
```

停止写入后备份 runtime 卷示例：

```bash
docker compose stop backend frontend
mkdir -p backups
docker run --rm \
  -v <runtime-data-volume>:/data:ro \
  -v "$PWD/backups":/backup \
  alpine tar czf /backup/runtime-data-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .
docker compose start backend frontend
```

当前工程不为旧 runtime schema 提供兼容迁移。升级前应先备份；如果运行库来自早期未上线版本且结构不兼容，应在确认无需保留其中数据后重建 runtime 卷，再从当前数据库重新同步和发布。

### 7.3 升级

```bash
git pull
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 backend
```

升级后检查 `/health`、管理中心的数据库状态、active release 和新建会话。不要在升级过程中删除数据卷，除非已经完成备份并明确要重置环境。

## 8. 常见故障

### 后端容器不健康

查看：

```bash
docker compose logs backend
```

重点检查 runtime SQLite 路径是否可写、Oracle 地址是否从容器可达、Python 依赖是否安装成功。

### Schema 同步失败

确认：

- `DOCKER_BUSINESS_DATABASE_URL` 的主机、端口、service name 正确。
- Oracle 用户有读取数据字典和目标 schema 的权限。
- `BUSINESS_DATABASE_SCHEMAS` 使用逗号分隔，且大小写符合实际 schema。
- 外部 Oracle 的防火墙允许后端容器网段访问。

### 可以登录但不能查询

通常是尚未发布 active release。先完成 Schema 同步、四类草稿和首次发布，再创建新会话。

### 查询变慢或向量索引不可用

先查看 runtime 状态和 trace。确认 `VECTOR_API_KEY`、模型名和维度匹配；向量服务不可用时可以暂时关闭 `ENABLE_VECTOR_RETRIEVAL`，系统会使用 BM25。

### 前端显示 502 或无法加载

确认 backend 健康后，再检查 frontend 的 Nginx 日志。Compose 中前端通过内部服务名 `backend:8000` 反代 `/api` 和 `/health`，不要把浏览器端 API 地址改成容器内部地址。

## 9. 本机开发

```bash
cp env.example .env
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
docker compose up -d oracle
scripts/devctl.sh start
scripts/devctl.sh status
```

开发服务默认监听 `127.0.0.1:8000` 和 `127.0.0.1:5173`。需要局域网其他设备访问时：

```bash
BACKEND_HOST=0.0.0.0 FRONTEND_HOST=0.0.0.0 scripts/devctl.sh restart
```

同时确认防火墙只开放必要端口，并使用实际运行主机的 IP 访问前端。开发服务日志位于 `.runtime/backend.log` 和 `.runtime/frontend.log`。

停止开发服务：

```bash
scripts/devctl.sh stop
```
