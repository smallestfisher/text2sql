# Text2SQL 架构说明

这份文档只描述**当前代码已经实现的架构**，不记录历史迁移过程，也不混入过期的“后续计划”表述。

如果你第一次进入仓库，推荐顺序：

1. `README.md`
2. `TEXT2SQL_ARCHITECTURE.md`
3. `DEBUG_PLAYBOOK.md`
4. `backend/README.md`
5. `frontend/README.md`

---

## 1. 总结

当前工程是一个 **LLM-first Text2SQL** 系统：

- 主 SQL 由 LLM 生成
- 本地代码负责问题理解、检索、约束、校验、执行、状态恢复和运行时审计
- 主链路直接面向真实业务表、检索结果和运行时上下文

主路径可以概括为：

**问题理解 -> 混合检索 -> Query Plan -> Prompt 构造 -> LLM 生成 SQL -> 校验/修复 -> 执行 -> 组织回答 -> runtime 落库 -> 前端恢复与排查**

这个系统不是：

- 本地模板或规则主导的 SQL 生成器
- “命中某条规则再套一段 SQL 模板”的引擎
- 一个单独依赖外部向量数据库的 RAG 系统

---

## 2. 核心概念

### 2.1 Session

`session` 是一段会话容器，对应一组连续消息和一份可继承的会话状态。

一个 session 里通常会有多轮提问。

### 2.2 Message

`message` 是会话中的单条用户消息或助手消息。

消息上可能挂 `trace_id`，用于把这条展示结果和某次真实执行过程关联起来。

### 2.3 Trace

`trace` 不是整段对话记录，而是**单次查询轮次的完整执行记录**。

一次真正进入主链路的提问，会生成一个新的 `trace_id`。  
所以通常关系是：

- 一个 `session`
- 包含多条 `message`
- 对应多个 `trace`

### 2.4 Query Log

`query_log` 是 runtime 库里的一条结构化查询记录，按 `trace_id` 落库，主要用于：

- 管理台查询
- 风险汇总
- replay
- workspace 聚合

### 2.5 SQL Audit

`sql_audit` 是某个 `trace_id` 对应的 SQL 审计记录，保存生成 SQL、校验结果、风险级别和执行摘要。

### 2.6 Workspace

`workspace` 是前端恢复会话的聚合视图。  
它不是单张表，而是把会话、消息、状态、trace、query log、SQL audit 和可恢复 response 拼成一份前端直接可用的结构。

### 2.7 Retrieval Corpus

`retrieval corpus` 是当前系统用于检索的语料集合。  
它既服务关键词/结构化检索，也可以在启用远端 embedding 时生成向量并持久化到 runtime 库。

---

## 3. 主要静态资产

当前主链路直接读取这些文件：

- `semantic/tables.json`
  - 真实表结构、字段说明、关系和部分时间/版本语义
- `semantic/business_knowledge.json`
  - 结构化业务知识
- `examples/nl2sql_examples.template.json`
  - 真实 few-shot/example 资产
- `semantic/join_patterns.json`
  - 稳定多表 join 经验
- `semantic/domain_config.json`
  - 语义配置 manifest
- `semantic/domain_config/*`
  - `domain_config` 的分片内容，最终由 `DomainConfigLoader` 合并
- `sql/runtime_store.sql`
  - runtime 库表结构定义

当前语义配置里，真正会被合并进运行时 `domain_config` 的分片主要包括：

- `base/*`
- `metrics/*`
- `query_profiles/*`
- `semantic_graph/*`

---

## 4. 容器与装配

系统装配中心是 [backend/app/core/container.py](/home/y/llm/new/backend/app/core/container.py) 里的 `AppContainer`。

初始化时它会装配这些核心对象：

- `DomainConfigLoader`
- `SemanticRuntime`
- `DatabaseConnector`
  - 业务查询库
  - runtime 库
- `RuntimeStoreInitializer`
- 各类 repository
- `LLMClient`
- `QueryPlanner`
- `QueryPlanCompiler`
- `QueryPlanValidator`
- `RetrievalService`
- `VectorRetriever`
- `VectorCorpusStoreService`
- `SqlExecutor`
- `SqlValidator`
- `ConversationOrchestrator`
- `SessionWorkspaceService`
- `EvaluationService`

`get_container()` 是一个 `lru_cache(maxsize=1)` 单例入口。  
当前 `POST /api/admin/metadata/reload` 的语义不是“局部 hot reload 某个 service”，而是通过 `reset_container()` 直接重建整套容器，保证：

- `SemanticRuntime`
- `PromptBuilder`
- `QueryPlanner`
- `RetrievalService`
- `SqlValidator`

这些依赖同一份元数据的对象一起刷新，而不是只刷新其中一部分。

---

## 5. 主 API 入口

### 5.1 用户主入口

当前前端默认使用：

- `POST /api/chat/query/stream`

这是主查询入口，返回 SSE 事件流。

同步非流式入口仍然存在：

- `POST /api/chat/query`

它直接返回完整 `ChatResponse`。

### 5.2 调试拆分入口

系统还保留了一组拆分调试接口：

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`

这些接口主要用于开发期定位，不是前端主工作台的默认路径。

### 5.3 会话恢复入口

前端恢复会话的主入口是：

- `GET /api/chat/sessions/{session_id}/workspace`

它一次性返回：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- `trace_artifacts`

---

## 6. 一次查询的真实执行链路

主编排服务是 [backend/app/services/orchestrator.py](/home/y/llm/new/backend/app/services/orchestrator.py) 里的 `ConversationOrchestrator`。

一次 `POST /api/chat/query/stream` 的关键流程如下。

### 6.1 SSE 路由层

路由在 [backend/app/api/routes/chat.py](/home/y/llm/new/backend/app/api/routes/chat.py)。

它会做这些事情：

1. 解析请求和用户上下文
2. 先生成一个 `trace_id`
3. 向 `ProgressService` 订阅这个 `trace_id`
4. 在后台线程里执行 `container.orchestrator.chat(request, trace_id)`
5. 持续等待进度事件并按 SSE 格式向前端输出
6. 如果后台任务异常且还没发出 `failed`，路由层补发一个 `failed`
7. 最后取消订阅

这里的 `ProgressService` 是**进程内事件通道**，不是跨进程消息总线。  
当前实现是事件驱动唤醒，不靠固定间隔轮询。

### 6.2 Orchestrator 主阶段

`ConversationOrchestrator.chat()` 的主顺序是：

1. 新建 trace
2. 发布 `accepted`
3. 读取或恢复 `session_state`
4. 执行 planning
5. 进入 terminal gate 判断
6. 执行 retrieval
7. compile query plan
8. validate query plan
9. build SQL prompt
10. 生成 SQL
11. validate SQL
12. 必要时做一次 SQL repair
13. 执行 SQL
14. 生成 answer 和 `next_session_state`
15. 落库 runtime artifacts
16. 发布 `completed` 或 `failed`

---

## 7. 问题理解栈

### 7.1 QueryIntentParser

[backend/app/services/query_intent_parser.py](/home/y/llm/new/backend/app/services/query_intent_parser.py)

当前 parser 已收缩为 shallow parse，主要只抽高确定性信号，例如：

- 时间
- 版本
- 部分枚举/实体命中
- topN / sort / limit
- follow-up cue

它不再承担高层 few-shot 门控或本地 SQL 路由职责。

### 7.2 IntentService

[backend/app/services/intent_service.py](/home/y/llm/new/backend/app/services/intent_service.py)

它调用 `LLMClient` 和 `PromptBuilder` 生成 LLM intent。  
高层理解现在由这条链路主导。

### 7.3 IntentNormalizer

[backend/app/services/intent_normalizer.py](/home/y/llm/new/backend/app/services/intent_normalizer.py)

它负责把 LLM intent 收口到当前语义系统允许的边界里，例如：

- domain
- metrics
- dimensions
- filters
- 结构合法性

### 7.4 QuestionClassifier

[backend/app/services/question_classifier.py](/home/y/llm/new/backend/app/services/question_classifier.py)

当前分类是：

- `LLM-primary`
- baseline 只做轻量对照和仲裁
- 再叠加 hard guard / relevance guard

### 7.5 QueryPlanner

[backend/app/services/query_planner.py](/home/y/llm/new/backend/app/services/query_planner.py)

它负责把：

- parser intent
- llm intent
- normalized intent
- session_state
- classification

整理成主链路可消费的 `QueryIntent` 和初始 `QueryPlan`。

### 7.6 QueryPlanCompiler

[backend/app/services/query_plan_compiler.py](/home/y/llm/new/backend/app/services/query_plan_compiler.py)

它会在 retrieval 结果已经出来之后，对 `QueryPlan` 做一层 retrieval-aware compile，当前主要包括：

- 当 `subject_domain = unknown` 时，基于 retrieval 命中补 domain
- 基于 example / join pattern 命中补 support tables
- 再交给 `SemanticRuntime.sanitize_query_plan()` 收口

### 7.7 QueryPlanValidator

[backend/app/services/query_plan_validator.py](/home/y/llm/new/backend/app/services/query_plan_validator.py)

它负责校验 Query Plan 是否仍在允许边界内，包括：

- domain/table 合法性
- 维度/指标约束
- 风险标记
- clarification 条件

---

## 8. Retrieval 架构

检索服务在 [backend/app/services/retrieval_service.py](/home/y/llm/new/backend/app/services/retrieval_service.py)。

当前 retrieval 不是单通道，而是混合检索：

- lexical / keyword
- structured score
- vector retrieval

最终产出 `RetrievalContext`，包含：

- `domains`
- `metrics`
- `retrieval_terms`
- `retrieval_channels`
- `hits`
- `hit_count_by_source`
- `hit_count_by_channel`

### 8.1 当前会进入 corpus 的资产

当前会被组装成检索 corpus 的只有这些来源：

1. `examples/nl2sql_examples.template.json`
   - source type: `example`
2. `semantic/domain_config/*` 合并后的 `metrics`
   - source type: `metric`
3. `semantic/business_knowledge.json`
   - source type: `knowledge`
4. `semantic/tables.json`
   - source type: `knowledge`
5. `semantic/join_patterns.json`
   - source type: `join_pattern`

也就是说，不是整个工程文件都会被 embedding。  
代码、文档、schema、eval case 不会直接进入当前向量语料。

### 8.2 PromptBuilder 如何消费 retrieval

[backend/app/services/prompt_builder.py](/home/y/llm/new/backend/app/services/prompt_builder.py)

当前 SQL prompt 会消费 retrieval 产出的这些证据：

- `retrieved_examples`
- `business_notes`
- `join_patterns`

这也是当前 few-shot 和业务知识进入 SQL 生成的真实入口。

---

## 9. 向量链路与持久化

### 9.1 VectorRetriever

[backend/app/services/vector_retriever.py](/home/y/llm/new/backend/app/services/vector_retriever.py)

当前向量通道的边界很明确：

- 只有远端 embedding client 配置成功时，vector retrieval 才算启用
- 已移除 `local-hash` 这类本地 embedding fallback
- 未配置远端 client 时，vector channel 直接关闭

### 9.2 VectorCorpusStoreService

[backend/app/services/vector_corpus_store_service.py](/home/y/llm/new/backend/app/services/vector_corpus_store_service.py)

它负责把当前 corpus 和 runtime 库里的持久化向量做同步：

- 基于 `document_id` 和 `content_hash` 对比
- 尽量复用旧向量
- 只对 `new / changed` 文档重建 embedding
- 删除已经不在 corpus 中的旧文档

### 9.3 持久化位置

向量不会单独落到外部向量数据库。  
当前持久化位置是 runtime 库里的：

- `vector_corpus_documents`

索引和表结构由：

- `sql/runtime_store.sql`
- `RuntimeStoreInitializer`

共同保证。

### 9.4 运行方式

当前向量链路的职责分工是：

- runtime 库负责持久化 corpus 向量
- 应用内存负责实际 brute-force cosine search

如果向量同步失败：

- 应用不会因为没有向量而整条主链路崩掉
- `RetrievalService` 会记录 `vector_sync.error`
- vector channel 会被置为空 corpus

---

## 10. Prompt、SQL 生成与治理

### 10.1 PromptBuilder

`PromptBuilder` 负责按当前 Query Plan 和 retrieval context 组装 prompt。

它不会把全量 schema 和全量知识直接塞给模型，而是做预算控制：

- 只选相关表
- 只选命中的 few-shot
- 只选命中的业务知识
- 只选命中的 join pattern

### 10.2 LLMClient

[backend/app/services/llm_client.py](/home/y/llm/new/backend/app/services/llm_client.py)

当前 LLM client 负责：

- intent 生成
- 分类/guard
- SQL 生成
- SQL repair

如果 LLM 不可用或返回非法结果，主链路会显式失败，不再静默降级。

### 10.3 SqlValidator

[backend/app/services/sql_validator.py](/home/y/llm/new/backend/app/services/sql_validator.py)

SQL validator 当前会校验：

- 只读
- 表和字段范围
- Query Plan shape contract
- 时间/版本约束
- LIMIT
- 风险级别和风险 flags

当前只允许一次 repair，不做无限循环自修。

### 10.4 SqlExecutor

[backend/app/services/sql_executor.py](/home/y/llm/new/backend/app/services/sql_executor.py)

SQL 执行使用业务查询库连接。  
结果还会经过 `ExecutionCacheService` 做短 TTL 缓存。

---

## 11. 结果组织与会话状态

### 11.1 AnswerBuilder

[backend/app/services/answer_builder.py](/home/y/llm/new/backend/app/services/answer_builder.py)

它负责把：

- classification
- query plan
- validation
- execution

整理成最终 `answer`。

### 11.2 SessionStateService

[backend/app/services/session_state_service.py](/home/y/llm/new/backend/app/services/session_state_service.py)

它负责生成下一轮 `session_state`，供 follow-up 问题继承上下文。

### 11.3 SessionService

[backend/app/services/session_service.py](/home/y/llm/new/backend/app/services/session_service.py)

它负责：

- 创建/删除 session
- 追加用户消息和助手消息
- 读取历史消息
- 解析当前 state
- 做会话归属校验

---

## 12. Runtime 落库与可观测性

当前 runtime 数据默认落到 runtime 库。

### 12.1 Session 相关

- `chat_sessions`
- `chat_messages`
- `session_state_snapshots`

### 12.2 Trace 与查询相关

- audit trace
- `query_logs`
- `retrieval_logs`
- `sql_audit_logs`

### 12.3 其他 runtime 资产

- `feedback_logs`
- `evaluation_runs`
- `vector_corpus_documents`

### 12.4 Response Restore

[backend/app/services/chat_response_restore_service.py](/home/y/llm/new/backend/app/services/chat_response_restore_service.py)

它负责根据 `trace_id` 从：

- trace
- query log
- sql audit
- 当前 session state

恢复出一个可展示的 response snapshot。

这也是 `workspace` 能把“历史一次查询结果”重新还原给前端的关键能力。

---

## 13. Workspace 与前端工作台

工作台恢复服务在 [backend/app/services/session_workspace_service.py](/home/y/llm/new/backend/app/services/session_workspace_service.py)。

当前 `workspace` 会聚合：

- session
- messages
- state
- latest_response
- latest_trace
- latest_sql_audit
- latest_query_logs
- trace_artifacts

其中 `trace_artifacts` 是按 trace 维度整理的聚合项，方便前端在同一会话里切换不同轮次的结果。

所以前端主界面虽然长得像聊天界面，本质上更接近：

- 会话容器
- 查询工作流
- 可恢复排查面板

而不是一个自由聊天产品。

---

## 14. 管理台与运维入口

### 14.1 Runtime Status

`GET /api/admin/runtime/status` 当前会返回：

- business database health
- runtime database health
- llm health
- vector retrieval health
- retrieval corpus health
- sql ast validator health

### 14.2 Metadata 管理

管理接口支持直接修改：

- metadata documents
- examples

需要注意：

- 通过 examples 管理接口写入时，会触发当前容器内的 retrieval reload
- `POST /api/admin/metadata/reload` 会直接重建整个缓存容器

### 14.3 Replay / Materialize / Eval

当前管理台支持：

- replay 历史 query log
- 把真实 trace 物化为 example
- 把真实 trace 物化为 evaluation case
- 执行 evaluation run

这部分能力对应服务主要在：

- `EvaluationService`
- `RuntimeAdminService`

---

## 15. 当前明确边界

这几个边界是当前系统的真实约束。

### 15.1 不是本地 SQL 模板系统

不要把系统理解成“规则路由 + SQL 模板填空”。

### 15.2 没有本地 embedding fallback

当前没有 `local-hash`、本地假 embedding 或静默伪向量通道。

### 15.3 没有独立向量数据库

当前向量持久化用 runtime 库表，检索在应用内存里完成。

### 15.4 ProgressService 不是跨进程事件总线

当前 SSE 进度通道只适合同进程工作流。  
如果后面改成多实例/多 worker 共享事件，需要单独引入跨进程机制。

### 15.5 LLM 是强依赖

如果 LLM 不可用，主链路会失败，而不是自动切回旧模板或本地 stub。

---

## 16. 这份文档之后怎么维护

以后更新这份文档时，遵守两条规则：

1. 只写代码里已经存在的行为，不写“预计后面会做”
2. 如果某个历史问题已经改完，就从“问题/计划”改成“当前状态/边界”，不要继续保留迁移期措辞

如果你要排查准确率问题，优先改：

- `semantic/tables.json`
- `semantic/business_knowledge.json`
- `examples`
- `join_patterns`
- retrieval
- prompt
- validator

不要把系统重新拉回“大量本地规则 + 本地 SQL 模板”的旧路径。
