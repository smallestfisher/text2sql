# Text2SQL 系统架构

本文定义当前实现的数据所有权、发布模型和查询边界，也是后续改动必须遵守的架构约束。运行与调试细节见 [PROJECT_GUIDE.md](./PROJECT_GUIDE.md)，尚未完成的增强项见 [TODO.md](./TODO.md)。

## 1. 产品与部署边界

- 一个运行实例只连接一个 Oracle 业务数据库。
- 业务数据库连接只由部署配置 `BUSINESS_DATABASE_URL` 提供，不在 UI 中创建、保存或切换。
- 业务数据库可以包含多个 schema 和多张表，但系统不支持跨数据库查询或联邦查询。
- 切换业务数据库属于部署操作：修改连接配置、重启服务、重新同步物理目录并发布语义版本。
- 单实例 runtime store 使用 SQLite，由 `RUNTIME_DATABASE_URL` 指向持久化文件并启用 WAL，保存系统状态、语义配置、会话、审计和评测数据。
- 部署配置只来自环境变量或 Secret，不写入 runtime store，也不通过 UI 热修改。
- embedding 向量保存在 `VECTOR_CACHE_DIR` 下的可重建文件缓存中，不进入 runtime store。
- 工程尚未进入生产环境，不为现有实验数据、旧表结构、旧 API 或未完成模型保留兼容分支。实现目标架构时可以直接删除、重建或重命名相关结构。

明确不做：

- UI 动态注册多个业务数据库。
- 请求级或会话级切换数据库连接。
- workspace/data source 多租户隔离。
- 同一条 SQL 跨多个数据库执行。
- 为未发布的历史设计保留双读、双写或 fallback。

## 2. 总体结构

```text
┌────────────────────────────────────────────────────────────┐
│ 前端                                                       │
│ 对话工作台 | 语义配置 | 测试与发布 | 日志与审计            │
└───────────────────────────┬────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼────────────────────────────────┐
│ FastAPI                                                    │
│                                                            │
│ 查询链路                                                   │
│ Session -> QuestionContext -> Retrieval -> Prompt -> LLM   │
│         -> SQL Validation/Repair -> Oracle -> Answer       │
│                                                            │
│ 配置链路                                                   │
│ Schema Sync -> Physical Catalog -> Semantic Draft          │
│             -> Validation/Eval -> Immutable Release        │
└───────────────┬───────────────────────────────┬────────────┘
                │                               │
┌───────────────▼──────────────┐  ┌─────────────▼────────────┐
│ Oracle 业务数据库            │  │ SQLite runtime store      │
│ 单连接配置，只读查询         │  │ 目录、草稿、发布版本      │
│ 真实业务行数据               │  │ 会话、审计、日志、评测    │
└──────────────────────────────┘  └──────────────────────────┘
                                                │
                                  ┌─────────────▼────────────┐
                                  │ VECTOR_CACHE_DIR         │
                                  │ release 级可重建向量文件 │
                                  └──────────────────────────┘
```

架构层只负责连接、同步、加载、检索、组装、生成、校验、执行和审计。具体表名、字段含义、指标口径、关联路径、枚举映射和业务规则必须来自语义资产，不能写成 Python 或 React 中的业务场景分支。

## 3. 数据职责

### Oracle 业务数据库

- 保存真实业务数据。
- 应用只允许执行只读 `SELECT` 或 `WITH ... SELECT`。
- schema introspection 读取表、列、类型、注释、主外键和可见关系。
- 不保存 Text2SQL 会话、语义配置或运行日志。

### SQLite runtime store

- 保存当前 Oracle 数据库的物理目录快照。
- 保存语义草稿、不可变发布版本和 active release 指针。
- 保存用户、会话、消息、状态、trace、query log、SQL audit、feedback 和 eval。
- 保存按 semantic release 隔离的检索日志，但不保存 embedding 向量。
- 不复制 Oracle 的业务明细数据。
- 不保存环境变量、Secret 或运行时配置覆盖。

SQLite 适用于当前单实例部署。后端使用 WAL、busy timeout 和写事务串行化；如果未来需要多副本并发写入，再将 repository 边界迁移到服务型数据库。

### 向量缓存

- 按 semantic release 隔离，保存 embedding、内容 hash 和模型签名。
- 使用原子文件替换，缓存损坏或模型变化时可以删除后重建。
- 缓存目录由 `VECTOR_CACHE_DIR` 配置，不是审计或业务状态的真相来源。

### 离线测试资产

- `tests/fixtures/` 只用于开发期回归、代码评审和离线 lint。
- 后端镜像和 Compose 不挂载离线 fixture，runtime 库也不读取全局文件资产。
- 运行时只读取已发布 release snapshot；草稿和发布历史通过管理 UI 写入 runtime store。
- `eval/retrieval_cases.json` 只保存离线检索回归问题和期望约束，不进入生产镜像；管理员评测 case 保存在 SQLite 运行库中。
- Compose 的 Oracle 服务只提供空的本地开发数据库，不自动创建业务表；业务 schema 由使用者自行提供。

## 4. 物理目录与语义资产

物理事实与人工语义必须分层保存。

### Physical Catalog

由 Oracle 自动同步，包含：

- schema、表、字段、物理类型和 nullable。
- 主键、外键和数据库可见关系。
- Oracle 原始注释。
- 同步时间、catalog hash 和结构漂移信息。

重新同步只更新物理事实，不直接修改 active release。表或字段删除、类型变化、关系变化需要产生明确的漂移告警。

### Semantic Draft

由 UI 编辑，固定为四类文档：`tables_metadata`、`business_knowledge`、`join_patterns` 和 `examples_template`。主要包含：

- 允许查询的表和字段范围。
- 表及字段的业务名称、说明和同义词。
- 人工确认的关联关系、方向和基数。
- 时间字段、物理格式和业务时间含义。
- 指标公式、聚合方式、去重粒度和默认过滤条件。
- 枚举值、业务词到数据库值的映射。
- 业务知识、禁止规则和风险提示。
- 人工确认的自然语言与 SQL 示例。

系统同步结构并生成建议，用户只需重点确认影响准确度的关联、时间、指标和业务词义，不要求逐字段重复录入物理信息。

### Semantic Release

- release 是完整、不可变、可审计的语义快照。
- 快照包含全部语义资产、catalog hash、draft 版本和资产 schema 版本。
- 当前发布先完成结构校验、引用校验和 SQL 示例校验；管理员评测可以在发布前独立运行，自动纳入发布闸门仍是待办。
- 检索语料准备完成后才能切换为 active。
- 发布失败不能改变当前 active release。

建议状态流转：

```text
building -> active -> inactive
    |
    +-----> failed
```

## 5. 查询运行时

启动时只创建一个 Oracle `DatabaseConnector`。所有业务 SQL 都通过这个连接器执行，不根据请求参数创建或选择其它数据库连接。

创建会话时绑定当时的 active `semantic_release_id`。后续请求沿用该版本，避免同一会话在发布前后混用不同指标或字段含义。发布新版本后，新会话使用新 release；旧会话仍可由不可变快照恢复。

```text
session.semantic_release_id
    -> ReleaseRuntime
       -> SemanticRuntime
       -> BM25 / vector corpus
       -> Prompt evidence
       -> SQL validator metadata
    -> single Oracle connector
```

`ReleaseRuntime` 按 release ID 构建并缓存。对象构建完成后只读；发布或 reload 通过创建新对象并原子替换 active 指针完成，不能在请求执行过程中原地修改共享字典。

查询运行时的业务域目录、表域映射、检索语料和提示词上下文均从该 release 的完整快照派生，不从代码中的固定枚举或离线 fixture 文件读取。问题上下文先抽取结构化的实体、指标、维度、过滤、时间、版本、排序和行数约束，再交给 retrieval、SQL prompt 与 validator；缺失的明确约束会阻止执行并进入 SQL repair。

业务域是用户定义的字符串，不是系统枚举。后端只根据 release 资产识别，前端只展示后端返回的值，不允许根据表名推断或维护内置中文映射。

检索默认先执行 BM25。向量索引处于重建中、缓存损坏或 embedding 服务不可用时，查询继续使用关键词证据并在 trace/retrieval warnings 中记录降级原因；只有向量检索实际成功时才把 `vector` 标记为本次查询的检索通道。

每次查询至少记录：

- `session_id`、`trace_id` 和 `semantic_release_id`。
- 原问题、effective question 和检索证据。
- prompt 版本、生成 SQL、repair 过程和最终 SQL。
- 校验结果、执行耗时、返回行数和错误信息。

## 6. UI 信息架构

管理端围绕当前部署数据库工作，不提供数据库连接列表。当前 Semantic Studio 包含数据库结构、表结构、业务知识、Join Pattern 和问题示例五个视图；数据库结构视图同时承载连接状态、Schema 同步、草稿状态、发布操作和发布历史。

UI 采用渐进式配置：先同步和选择表，再处理系统识别出的高风险缺口，最后测试并发布。配置数量不是目标，覆盖真实业务歧义和高频问题才是目标。

## 7. 数据库切换

切换 Oracle 数据库时执行：

1. 修改 `BUSINESS_DATABASE_URL` 和 schema scope。
2. 重启后端。
3. 清理或重建该环境的 physical catalog、draft、release、向量缓存和会话数据。
4. 同步新数据库结构。
5. 完成语义配置、评测和首次发布。
6. 新建会话开始查询。

由于当前不要求兼容旧环境，数据库切换不迁移旧语义版本和旧会话。需要保留历史记录时，应使用独立 runtime 文件或完整环境备份，而不是在同一个实例中混放多个业务数据库的数据。

## 8. 架构约束

- 业务数据库凭据只存在于环境变量或部署 secret 中，不进入 runtime 表和 API 响应。
- LLM、向量和执行治理配置同样只来自环境变量或部署 secret；系统设置 API 只读。
- UI 不能修改 `BUSINESS_DATABASE_URL`。
- 所有生成 SQL 必须经过 AST 只读、安全、表字段和结果限制校验。
- 问题上下文中明确表达的过滤、维度、排序、版本、时间格式和结果限制必须被 SQL 覆盖；违反这些约束时先尝试自动修复，修复失败不得执行。
- 业务准确性知识进入语义资产和样例，validator 不演化为按业务场景编写的规则引擎。
- 代码中不得新增具体业务表名、指标名或场景关键词分支。
- active release 切换必须原子完成。
- physical catalog、draft 和 release 必须有清晰的数据所有权，禁止继续维护两套运行时语义真相。
- 新架构直接替换未上线的旧设计，不增加兼容层。

## 9. 验收标准

架构验收应满足：

1. 启动后只使用 `BUSINESS_DATABASE_URL` 建立一个 Oracle 业务连接。
2. UI 能同步当前数据库结构，但不能创建或切换数据库连接。
3. 用户可以完成表字段、关联、时间、指标、枚举和示例配置。
4. 发布前完成引用校验、SQL 校验和评测，失败不影响 active release。
5. 新会话固定使用一个 semantic release，查询链路全部消费该 release。
6. 检索和向量缓存按 release 隔离，没有全局可变语义资产污染。
7. 切换数据库通过重启和重建配置完成，不存在旧模型兼容代码。
8. 后端测试、语义 lint、前端构建和核心端到端流程全部通过。
