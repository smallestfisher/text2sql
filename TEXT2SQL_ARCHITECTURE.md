# Text2SQL 架构说明：LLM-first

本文档描述当前工程的**真实运行架构**，目标是回答四类问题：

- 系统里有哪些模块，它们各自负责什么
- 一次真实提问从前端进入后端，经过哪些阶段，在哪些地方会终止或被拦截
- 运行时数据、会话状态、Trace、SQL 审计和前端工作台是如何串起来的
- 后续调试、扩展或重构时，哪些边界可以动，哪些边界不应该破坏

如果你是第一次进入仓库，建议阅读顺序：

1. `README.md`
2. `TEXT2SQL_ARCHITECTURE.md`
3. `DEBUG_PLAYBOOK.md`
4. `backend/README.md`
5. `frontend/README.md`

---

## 1. 总体架构结论

当前工程已经明确切换为 **LLM-first Text2SQL**，即：

- **LLM 负责 SQL 生成主路径**
- **本地代码负责理解问题、约束范围、控制风险、恢复状态、记录运行时信息**
- **少量配置规则只用于“理解问题”或“治理风险”，不再承担“主 SQL 生成器”的职责**

同时，问题理解层已经不再是纯 `parser/classifier/planner-first`：

- `QueryIntentParser` 负责 shallow parse
- `LLMIntentService` 负责 LLM intent
- `IntentNormalizer` 负责本地收口
- `QuestionClassifier` 已切到 `LLM-primary + baseline arbitration + hard guard`
- `QueryPlanner` 已回归 deterministic planner/compiler

换句话说，这个系统不是：

- 规则引擎主导 + LLM 辅助润色
- 本地模板拼 SQL + LLM 只补字段
- 预建一堆语义视图/分析对象再强依赖它们

而是：

- 基于真实 `tables.json`、`business_knowledge.json`、Query Plan、检索结果和少量场景 few-shot
- 让 LLM 直接生成 MySQL 只读 SQL
- 再由 Query Plan validator、SQL validator、执行器和 runtime 体系做闭环治理

这意味着系统的主链路可以概括为：

**问题理解 → Query Plan → Prompt 上下文选择 → LLM 生成 SQL → 校验/修复 → 执行 → 组织回答 → 落库审计 → 前端恢复与排查**

---

## 2. 设计原则

### 2.1 LLM-first，不等于 LLM 无约束

LLM 是主生成器，但不是裸奔：

- Query Planner 先把问题收敛成结构化 Query Plan
- PromptBuilder 只给当前问题真正相关的 schema、知识和少量 few-shot
- SqlValidator 再校验只读、安全、来源范围、时间/版本、LIMIT、风险级别
- 失败时允许一次 repair，而不是无限次自我修复

### 2.2 优先真实表结构，不优先预建分析对象

- `tables.json` 是真实物理表、字段、关系、时间字段、版本字段的主来源
- 系统不要求数据库预建额外分析对象
- 某些历史语义视图或预建对象可以存在，但不能成为运行主依赖
- 对复杂逻辑，优先通过 prompt / knowledge / few-shot / validator 驱动 LLM 正确生成 SQL

### 2.3 Prompt 必须做预算控制

LLM-first 的风险之一是 token 膨胀，所以当前系统坚持：

- 不把全量 schema 一次性塞进 prompt
- 不把全量业务知识塞进 prompt
- 不把全量上下文全部注入
- 只选择当前 Query Plan 命中的表、知识和少量 few-shot
- 在 trace 中记录 prompt 上下文摘要，便于回溯“本次到底给了模型什么”

### 2.4 会话工作台不是聊天 UI，而是可恢复的查询工作流界面

前端虽然是会话形态，但本质上不是自由聊天产品，而是：

- 以会话为容器
- 以每次查询轮次为 trace 单位
- 以 `workspace` 聚合数据为恢复入口
- 以右侧详情面板承接 SQL / Trace / State / Result 调试信息

因此，会话系统的重点不是“聊天体验”，而是：

- 上下文继承是否稳定
- 历史结果是否可恢复
- 每轮生成是否可追溯
- 失败后是否可 replay、可物化 sample、可进入 eval

---

## 3. 仓库核心构成

### 3.1 配置与知识层

#### `tables.json`

职责：

- 维护真实数据库表、字段、字段说明
- 维护表间关系、时间字段、版本字段、常用 join 依据
- 为 PromptBuilder、RetrievalService、SemanticRuntime、调试人员提供统一 schema 来源

不负责：

- 本地模板拼 SQL
- 针对单题写死的业务规则

#### `business_knowledge.json`

职责：

- 维护稳定业务口径
- 表达业务关键词、领域归属、涉及表、说明 notes
- 给 PromptBuilder 和 RetrievalService 提供可命中的业务知识块

适合放进去的内容：

- 指标业务解释
- 口径定义
- 版本含义
- 横表解释
- 默认枚举语义

不适合放进去的内容：

- 一道题对应一条完整固定 SQL
- 具体 SQL join 模板
- 只能服务单一问题的临时规则

#### `semantic/domain_config.json`

职责：

- 辅助语义解析
- 辅助领域识别、别名、实体、指标映射
- 给 SemanticRuntime / QueryPlanValidator 提供基础结构化语义支撑
- 当前 `semantic/domain_config.json` 是清单入口，具体内容按职责拆在 `semantic/domain_config/` 目录下

边界：

- 它不是 SQL 模板库
- 它不是主编译器
- 它可以用于“用户这句话是什么意思”，不能扩展成“SQL 必须怎么写”

### 3.2 Backend 实现层

主要目录在 `backend/app/`：

- `api/`：FastAPI 路由、依赖注入、中间件
- `core/`：容器、全局设置、错误处理
- `models/`：Pydantic 模型和数据结构
- `repositories/`：runtime MySQL 持久化访问
- `services/`：核心业务逻辑

### 3.3 Frontend 工作台层

主要目录在 `frontend/src/`：

- `App.tsx`：主工作台和管理中心界面逻辑
- `api.ts`：前端 API 访问封装
- `types.ts`：前端类型定义
- `styles.css`：工作台样式和响应式布局

---

## 4. Backend 核心对象与依赖注入

### 4.1 应用入口

后端入口在 `backend/app/main.py`，核心事情只有几件：

1. 配置日志
2. 创建 FastAPI app
3. 注入请求级 trace middleware
4. 注册错误处理器
5. 挂载各类 router：
   - `health`
   - `admin`
   - `auth`
   - `semantic`
   - `query`
   - `sessions`
   - `chat`

这意味着：

- `/api/query/*` 是偏底层的分步接口
- `/api/chat/*` 是真正给工作台使用的会话/查询接口
- `/api/admin/*` 是管理与调试体系

### 4.2 AppContainer

`backend/app/core/container.py` 是系统的**装配中心**。它把配置、连接器、repository、service 和 orchestrator 全部串起来。

容器初始化顺序大致是：

1. 读取 settings
2. 载入 domain config
3. 初始化 `SemanticRuntime`
4. 初始化两个数据库连接器：
   - `business_database_connector`
   - `runtime_database_connector`
5. 初始化 runtime schema 自动升级器 `RuntimeStoreInitializer`
6. 初始化 runtime repositories：
   - auth
   - session
   - audit
   - feedback
   - runtime log
   - evaluation run
7. 初始化 `ProgressService`
8. 初始化 Prompt / LLM / Intent / Planner / Validator / Executor / Retrieval 等核心 service
9. 初始化 SessionWorkspace / ChatResponseRestore / Evaluation 等高层 service
10. 初始化 `ConversationOrchestrator`

这个容器表达了几个关键架构事实：

- 系统明确区分**业务查询库**和**运行时库**
- 会话、trace、query log、feedback、eval 都是 runtime 数据
- 主聊天流程不直接散落在路由里，而是统一走 orchestrator
- 理解主链路当前默认启用 LLM intent 与 LLM classifier，不再依赖过渡开关

---

## 5. 端到端运行链路

下面按一次真实查询来说明。

### 5.1 前端发起请求

前端默认不再直接优先打 `POST /api/chat/query`，而是走：

- `POST /api/chat/query/stream`

原因很明确：

- 需要把后端执行阶段主动推给前端
- 避免前端靠轮询猜“现在进行到哪了”
- 最终结果也可以通过流末尾的 `completed` 事件直接回传

前端在 `frontend/src/api.ts` 里实现了 SSE 读取：

- 发送 `Accept: text/event-stream`
- 读取 streaming body
- 以 `\n\n` 分块解析 SSE 事件
- 从 `data:` 行提取 JSON payload

前端在 `App.tsx` 中发请求时，会先：

1. 创建本地 pending user message
2. 创建本地 pending assistant message
3. 进入 `chatPending=true`
4. 实时消费后端进度事件
5. 收到 `completed` 事件时直接拿 `metadata.response`
6. 只有在 SSE 完全没有启动时，才回退到普通 `POST /api/chat/query`

### 5.2 `POST /api/chat/query/stream`

后端流式入口在 `backend/app/api/routes/chat.py`。

它做的事情不是自己执行业务，而是把 orchestrator 包装成一个 SSE 事件流：

1. 解析请求和用户上下文
2. 先生成一个 `trace_id`
3. 向 `ProgressService` 订阅这个 `trace_id` 对应的队列
4. 在后台线程里跑 `container.orchestrator.chat(request, trace_id)`
5. 一边消费 progress queue，一边按 SSE 格式 `yield`
6. 当收到 `None` 结束标记时结束流
7. 最后取消订阅并等待后台任务结束

这层的关键点是：

- orchestrator 仍然是同步主流程
- 路由层只负责把同步执行“桥接”成异步流
- 流结束条件不是 HTTP 轮询，而是 `ProgressService.complete(trace_id)`

### 5.3 Orchestrator 总控链路

真正的核心运行逻辑在 `backend/app/services/orchestrator.py`。

一次 `chat()` 调用的完整阶段是：

1. 建立 trace
2. 发布 `accepted`
3. 解析 / 恢复 session state
4. 构建 planning trace：依次产出 parser intent、LLM intent、normalized intent、classification 和初始 Query Plan
5. 判断是否需要在 plan 阶段提前终止
6. 检索 examples / metric / business knowledge 等上下文
7. compile Query Plan，把检索证据和规则补充进 plan
8. validate Query Plan
9. 再次判断是否提前终止
10. 构造 SQL prompt
11. 调 LLM 生成 SQL
12. validate SQL
13. 校验失败时尝试一次基于 validator 反馈的 SQL repair
14. 执行 SQL
15. 执行失败时再尝试一次基于数据库错误的 SQL repair
16. 构造回答
17. 生成下一轮 session state
18. 写入消息 / 状态 / trace / query log / sql audit
19. 发布 `completed`
20. finally 中发布 progress complete

这里有两个容易和旧架构混淆的点，需要单独说明：

- 当前主链路里已经没有单独的 “query-plan prompt / plan hint” 阶段；LLM 在主执行链路里主要参与 `llm_intent`、`classification` 和 `generate_sql / repair_sql`
- parser 仍然保留，但它现在主要是理解基线和 trace 证据；如果标准化后的 intent 不可用，主链路不会退回到 parser baseline 继续执行

### 5.4 进度事件阶段定义

当前已接入前端的阶段主要有：

- `accepted`
- `load_session`
- `planning`
- `retrieval`
- `sql_generation`
- `sql_validation`
- `execution`
- `answer_building`
- `completed`
- `failed`

事件类型分为：

- `accepted`
- `stage`
- `completed`
- `failed`

其中 `completed` 事件的 `metadata.response` 会直接携带序列化后的 `ChatResponse`，这就是前端无需再发第二个查询请求的原因。

---

## 6. 问题理解层：Shallow Parse + Intent + Classification + QueryPlan

当前理解链路已经演进为：

1. `QueryIntentParser` 产出 shallow parse
2. `LLMIntentService` 产出 LLM intent
3. `IntentNormalizer` 收口 LLM intent
4. `QueryPlanner` 选择 effective intent：优先 `normalized`，不可用时直接保留 `parser`
5. `QuestionClassifier` 基于 effective intent 做分类
6. `QueryPlanner` 基于 effective intent + classification 生成 deterministic `QueryPlan`

对应的核心开关有：

- `CLASSIFICATION_LLM_ENABLED`

### 6.1 QueryIntentParser

`QueryIntentParser` 现在不再承担“最终理解器”职责，而是一个更纯的 shallow extractor。它只负责解析高确定性的显式信号，不再在 parser 层做 metric resolve、demand shortcut 或 session 继承。通常包括：

- 命中的指标 `matched_metrics`
- 命中的实体 `matched_entities`
- 请求的维度 `requested_dimensions`
- filters
- time_context
- version_context
- requested_sort
- requested_limit
- analysis_mode
- follow-up cue / explicit slots 信号

这一步解决的是：

**用户这句话里显式说了什么。**

它的输出会保留为：

- `parser_query_intent`
- `parser_intent`
- `parser_signals`

这些信息会直接进入 trace，作为后续 intent 对比的基线。

### 6.2 LLMIntentService + IntentNormalizer

`LLMIntentService` 直接接管主链路的高层语义理解。当前输出结构已经统一为 `StructuredIntent`，核心字段包括：

- `subject_domain`
- `metrics`
- `entities`
- `dimensions`
- `filters`
- `time_context`
- `version_context`
- `analysis_mode`
- `question_type`
- `inherit_context`
- `confidence`
- `reason`

`IntentNormalizer` 再做本地收口，当前重点是：

- domain 合法性检查
- metric 合法性检查
- dimension / filter 字段白名单过滤
- analysis_mode / question_type / confidence 规范化

当前系统会同时保留三份意图：

- `parser_intent`
- `llm_intent`
- `normalized_intent`

并记录两组差异：

- `diff_vs_parser`
- `diff_vs_shadow`

这使得单题调试时可以直接看出：

- LLM 比 parser 多理解了什么
- normalizer 又删掉了什么

### 6.3 Intent 选择

主链路统一由 `QueryPlanner` 完成：parser 提供基线，LLM intent 提供主理解，normalizer 收口后进入后续分类与规划。

当前规则是：

- 如果 normalized intent 可用，使用 normalized intent 进入主链路
- 如果 normalized intent 不可用，主链路不会继续沿用 parser 结果执行；parser 只保留为 trace 对照基线

当前 trace 中会显式记录：

- `intent_selection.selected_source`

### 6.4 QuestionClassifier

`QuestionClassifier` 现在已经切成 `LLM-primary + baseline arbitration + hard guard`。

它仍然负责决定：

- 这是首轮问题还是追问
- 是否需要继承上一轮上下文
- 是同域新问还是跨域新问
- 是否需要澄清
- 是否是无效问题

分类的结果直接影响：

- 是否继承 `session_state`
- `context_delta` 怎么生成
- 是否提前终止 SQL 生成

当前分类链路分三层：

1. 本地 hard guard
2. LLM primary classification
3. 本地 local classification 基线

其中本地 hard guard 仍保留：

- invalid/smalltalk
- relevance out-of-scope
- `unknown_request`
- `missing_metric`
- 无 session 时的直接 `new`

在进入会话分类后：

- 本地 baseline 启发式仍会计算，但只作为 `baseline_classification` 仲裁基线
- 只要 `CLASSIFICATION_LLM_ENABLED=true` 且有 session，就优先尝试 LLM classification
- LLM 输出仍需通过本地 accept/reject 校验
- 不可接受时保留本地分类结果

当前 trace / debug 中会显式记录：

- `decision_source`
- `baseline_classification`
- `score_details`
- `score_gap`
- `llm_hint`

### 6.5 QueryPlanner

`QueryPlanner` 现在已经回归 deterministic planner/compiler。它不再依赖 orchestrator 内部拼 plan，而是统一通过自己的 API 完成：

- `build_planning_trace()`
- `build_plan_from_intent()`
- `create_plan()`

主要逻辑：

1. 先拿到 effective intent
2. 再做 classify
3. 提取 metrics / entities / filters / time / version / sort / limit / dimensions
4. 如果是 follow-up，并且需要继承，则从 `session_state` 合并：
   - entities
   - metrics
   - filters
   - time_context
   - version_context
   - dimensions
   - sort
   - limit
5. 推导 dimensions
6. 选择候选表 `tables`
7. 生成基础 `QueryPlan`
8. 交给 `SemanticRuntime.sanitize_query_plan()` 做结构修正
9. 处理特殊场景，例如：
   - topN 聚合时去掉不必要时间维度
   - compare 模式 sort 清理
   - demand 月度趋势默认排序

当前的关键变化是：

- orchestrator 不再自己拼 query plan
- planner 的 deterministic 逻辑重新收回到 `QueryPlanner.build_plan_from_intent()`
- planner 的职责已经更接近编排器，而不是语义猜测器

可以把 QueryPlan 理解为：

**给 LLM 看的结构化任务描述书**。

它不是 SQL，但已经足够表达：

- 要查哪个业务域
- 可能需要哪些表
- 要按什么维度聚合
- 要哪些 filters
- 是否需要版本上下文
- 是否需要澄清

---

## 7. 检索与 Prompt 上下文选择

### 7.1 RetrievalService

`RetrievalService` 的职责不是“最终决定 SQL”，而是给 LLM 和调试流程补上下文。

它会从几个来源取检索 hit：

- example
- metric
- business knowledge
- vector retrieval（如果启用）

然后统一 rerank，取 top hits，形成 `RetrievalContext`。

`RetrievalContext` 会记录：

- domains
- metrics
- retrieval_terms
- retrieval_channels
- hits
- hit_count_by_source
- hit_count_by_channel

这层的目标是：

- 给 SQL prompt 和后续 few-shot 资产治理提供更相关的 evidence
- 给 trace / debug 留下检索命中依据
- 避免问题定位时完全靠裸 schema

### 7.2 PromptBuilder

`PromptBuilder` 是控制 prompt 质量的关键模块。

它当前主要负责这些 prompt：

- intent prompt
- classification prompt
- relevance guard prompt
- SQL prompt

其中 SQL prompt 的重点是：

1. 根据 `query_plan.tables` 选择真实 source schema
2. 把允许字段、表、业务知识、场景指令整理成结构化 payload
3. 针对 demand 这类横表场景追加专项指令
4. 在必要时注入少量 few-shot
5. 记录 context_summary 和 context_budget，便于 trace 审计

当前工程在 demand 场景里的很多“复杂口径”并不是用本地模板硬编码，而是通过：

- 专项业务知识
- SQL prompt 中的专项约束
- 内置场景模板 few-shot 的 SQL 形态提示
- 检索命中的真实 example SQL
- validator 的结构校验

共同逼近正确 SQL。

补充一个当前实现边界：

- `examples/nl2sql_examples.template.json` 会参与 RetrievalService 的 example 检索、管理接口和调试证据
- SQL prompt 会把命中的 example 以 `retrieved_examples` 形式带入
- 主 SQL prompt 同时保留内置场景模板 few-shot；当前是“检索样例 + 场景模板”双轨并存

### 7.3 当前 Trace 里的理解层观测点

当前 trace 中，理解链路已经明确拆出这些步骤：

- `parse_intent`
- `llm_intent`
- `normalized_intent`
- `classify_question`
- `plan`

因此单题调试时，可以直接看清：

- parser 看到了什么
- LLM intent 补了什么
- normalizer 去掉了什么
- 最终选了哪份 intent 进入主链路
- classifier 的最终 decision_source 是 `llm_primary`、`llm_aligned_with_baseline`、`llm_rejected`，还是 `llm_unavailable`

---

## 8. 治理层

当前主链路已经移除了查询权限裁剪服务，运行时默认行为是：

1. **所有登录用户都可执行查询**
2. **SQL / 结果 / SQL audit 默认可见**
3. **结果下载只保留会话 / Trace 归属校验**

当前治理重点收敛为两层：

- 查询前：`QueryPlanValidator`
- 查询后：`SqlValidator` + `SqlExecutor` + runtime 审计

也就是说，这里的“治理”不再负责：

- 往 Query Plan 注入数据权限过滤
- 按用户裁掉 SQL 可见性
- 按列做结果 hidden / masked

---

## 9. QueryPlan compile / validate

### 9.1 QueryPlanCompiler

`QueryPlanCompiler` 负责把 planner 的结构进一步收敛成适合 SQL prompt 的 plan，重点不是拼 SQL，而是：

- 结合 retrieval 和 runtime 语义做 plan 修正
- 把别名字段、排序字段、补充筛选等收敛成更稳定的结构化 plan
- 保持 plan 输出结构一致

### 9.2 QueryPlanValidator

`QueryPlanValidator` 负责校验 QueryPlan 本身是否合法，典型关注点包括：

- domain 是否存在
- metric 是否允许
- dimension 是否允许
- filter field 是否存在
- 风险级别和风险 flags

这一步的意义是：

- 先在“SQL 生成前”阻断明显无效 plan
- 让 LLM 生成 SQL 时拥有更稳定的输入边界

planner 终版不再引入额外的 LLM query plan hint 改写链路；QueryPlan 由 deterministic planner/compiler 直接产出并校验。

---

## 10. SQL 生成、校验、修复、执行

### 10.1 LLMClient

`LLMClient` 在主链路里承担三类调用：

- `generate_sql_hint`
- `repair_sql`

其中：

- SQL hint 是主 SQL 生成输出
- repair 是在 validator 或执行器返回错误后的一次定向修复

### 10.2 SQL 生成

当前系统对 SQL 生成的原则是：

- 只让模型输出只读 SQL
- 首选 `SELECT` 或 `WITH ... SELECT`
- SQL 来源必须落在 query_plan.tables 及其合法 CTE 范围内
- 不允许发明未知表、字段、维度或指标

### 10.3 SqlValidator

`SqlValidator` 是 SQL 治理核心之一，会做这些校验：

1. 只允许 `SELECT` / `WITH`
2. 禁止 `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE`
3. 检查 source 是否在允许集合内
4. 检查是否超出 QueryPlan 允许来源
5. 检查 QueryPlan filters 是否被覆盖
6. 检查 group by 是否覆盖了必需 dimensions
7. 检查 sort 是否保留
8. 检查 time_context / version_context 一致性
9. 检查 LIMIT 是否缺失或超过配置上限
10. 检查 join 是否存在笛卡尔风险
11. 检查 demand 横表的专项结构约束
12. 调 `SqlAstValidator` 做 AST 级只读和结构检查

这层是系统“防止 LLM 生成表面能跑但实际上不可信 SQL”的关键屏障。

### 10.4 repair 机制

repair 只允许一次，而且分两类：

- **校验失败 repair**：SQL 被 validator 拦截后，把 errors/warnings 返给 LLM
- **执行失败 repair**：SQL 通过校验但执行失败，再把 DB 错误返给 LLM

一旦 repair 成功，还要重新过 validator。

### 10.5 SqlExecutor

`SqlExecutor` 基于业务库执行最终 SQL。

它还配套：

- `ExecutionCacheService`
- 超时配置
- 最大返回行数控制
- 慢查询阈值记录

执行完成后结果会直接进入回答构建、Trace 落库和前端展示。

---

## 11. Answer、Session、Trace、Runtime 闭环

### 11.1 AnswerBuilder

`AnswerBuilder` 根据：

- classification
- query_plan
- execution
- plan_validation
- sql_validation

来生成最终回答，输出的重点通常包括：

- `status`
- `summary`
- `detail`
- `follow_up_hint`

所以 answer 不是简单地把 rows stringify，而是一个经过状态判断后的业务型回复对象。

### 11.2 SessionStateService

`SessionStateService` 负责把本轮 QueryPlan 和 SQL 更新为下一轮会话状态：

- 当前 subject_domain
- metrics
- dimensions
- filters
- sort
- time_context
- version_context
- last_query_plan
- last_sql
- last_result_shape

它决定了后续 follow-up 是否能稳定继承上下文。

### 11.3 SessionService

`SessionService` 管理：

- 创建会话
- 获取会话
- 删除会话
- 校验访问权限
- 追加 user / assistant message
- 更新当前 session state

这里的“会话”不是缓存，而是 runtime 库中的正式对象。

### 11.4 AuditService

`AuditService` 负责：

- 创建 trace
- 追加 trace step
- finalize trace
- 获取历史 trace

Trace 是整个系统的“执行骨架”，它记录每一轮从计划、检索、校验到快照恢复的完整步骤。

### 11.5 RuntimeLogRepository

`RuntimeLogRepository` 负责落 runtime 侧的：

- retrieval log
- query log
- sql audit log

其中：

- query log 更偏摘要和检索入口
- sql audit 更偏 SQL / validation / execution 审计
- trace 则是更完整的步骤链路

### 11.6 response snapshot

orchestrator 在完成 `ChatResponse` 后，会把一个安全版本的响应快照追加到 trace：

- 排除 `trace`
- 排除原始 `sql`
- 清空 `execution.sql`
- 清空 `next_session_state.last_sql`

这个 `response_snapshot` 的意义很大：

- 历史会话恢复时，不必重新执行 SQL
- 历史详情面板可以直接恢复为结构化 `ChatResponse`
- 普通用户重开历史会话时，不会因为运行态丢失而只剩一堆 message 文本

---

## 12. Workspace 恢复架构

### 12.1 为什么要有 workspace 聚合接口

如果前端自己去拼：

- session
- history
- state
- latest trace
- latest sql audit
- latest query logs
- 每条 assistant message 的 trace 详情

会非常容易出现状态漂移：

- 左边有消息
- 右边详情不是同一轮结果
- 会话重开后像“没执行过”
- 历史 trace 对不上当前卡片

所以当前前端工作台主入口统一改成：

- `GET /api/chat/sessions/{session_id}/workspace`

### 12.2 SessionWorkspaceService

`SessionWorkspaceService` 做的事情是：

1. 读取 session
2. 读取消息 history
3. 读取当前 state
4. 解析消息里的 trace_ids
5. 读取 query logs
6. 构建 `trace_artifacts`
7. 找出 latest trace 对应 artifact
8. 返回一份聚合后的 `SessionWorkspaceResponse`

`trace_artifacts` 每项包含：

- `trace_id`
- `response`
- `trace`
- `sql_audit`
- `query_log`

### 12.3 ChatResponseRestoreService

`ChatResponseRestoreService` 用于从 trace / log / sql_audit 恢复 `ChatResponse`：

优先级是：

1. 如果 trace 里有 `response_snapshot`，优先从 snapshot 恢复
2. 否则退化为从：
   - trace step metadata
   - query_log
   - sql_audit
   - session_state
   - messages
   组合重建

这就是当前工作台能稳定重开历史详情的关键。

---

## 13. 前端工作台运行逻辑

### 13.1 页面总体结构

当前前端是三栏工作台：

- 左侧：会话列表和用户信息
- 中间：消息流、结果卡、输入框
- 右侧：详情侧栏 `结果 / SQL / Trace / 状态`

管理员还能切换到管理中心。

### 13.2 前端状态核心对象

在 `frontend/src/App.tsx` 里，几个关键状态是：

- `sessions`
- `selectedSessionId`
- `messages`
- `sessionState`
- `latestResponse`
- `latestTrace`
- `latestSqlAudit`
- `latestQueryLogs`
- `traceArtifacts`
- `activeTraceId`
- `pendingProgress`
- `inspectorOpen`
- `activeTab`

这套状态的含义是：

- `workspace` 是会话级完整恢复基础
- `traceArtifacts` 是轮次级详情切换基础
- `activeTraceId` 决定右侧面板当前聚焦哪一轮
- `pendingProgress` 决定执行中进度卡展示

### 13.3 发问流程

当前前端发送一条问题时：

1. 先在本地插入 pending user / assistant message
2. 若没有 session，则先创建 session
3. 调 `api.chatQueryStream(...)`
4. 持续消费 SSE：
   - 更新 `pendingProgress`
   - 更新 `pendingTraceId`
   - 在 `completed` 事件中取 `response`
5. 成功后刷新 session workspace
6. 用 workspace 结果替换本地临时状态

### 13.4 结果卡与详情侧栏

每条 assistant message 如果有 `trace_id`，消息下会出现结果卡。

结果卡承接的是“该轮 trace”的摘要：

- 业务域
- 状态
- 行数
- 小表格预览
- 查看详情
- 下载

点击 `查看详情` 后：

- 设置 `activeTraceId`
- 切换 `activeTab="result"`
- 打开或聚焦详情栏
- 详情栏短暂高亮提示，明确告诉用户“右侧已切换到这轮结果”

### 13.5 进度流展示

前端对 `ProgressEvent` 做了专门的视图层处理：

- 阶段序列固定：`accepted → load_session → planning → retrieval → sql_generation → sql_validation → execution → answer_building`
- 终态事件有：`completed / failed`
- 通过 `buildPendingProgressView()` 计算：
  - 当前阶段
  - 完成百分比
  - 已完成步骤数
  - 每步文案 / icon / 状态 badge

所以当前进度 UI 并不是简单打印后端原始事件，而是：

- 对阶段做了归一化
- 对当前阶段做了更细文案说明
- 对失败、跳过、完成、进行中做了可视化区分

---

## 14. 管理台与调试闭环

### 14.1 管理台职责

管理员界面除了用户管理外，还承接运行时调试：

- runtime status
- metadata overview
- query logs
- feedback summary
- evaluation summary
- replay 结果查看

### 14.2 replay / materialize

管理台的重要作用不是“看个表”，而是把失败样本沉淀成资产：

- 历史问题可以 replay
- 真实 trace 可以 materialize 为 example
- 真实 trace 可以 materialize 为 eval case

这保证系统不是靠拍脑袋补样例，而是：

**从真实线上/联调问题中沉淀样例与回归资产**。

### 14.3 eval / replay / lint

当前还支持：

- `EvaluationService`
- runtime replay
- domain config lint

这几层的意义分别是：

- replay：重放单题
- eval：批量评估一组真实 case
- lint：校验领域配置本身的结构质量

---

## 15. SSE 主动推送架构

### 15.1 为什么不是轮询

轮询的问题很明显：

- 无法表达细粒度阶段
- 延迟高
- 前端不知道后端卡在哪一层
- 用户会觉得“点了没反应”

所以当前实现改成 SSE：

- 后端边执行边发 progress event
- 前端直接展示当前阶段
- 最终结果通过 `completed.metadata.response` 一次带回

### 15.2 为什么不是 WebSocket

当前选择 SSE 而不是 WebSocket，原因是：

- 这里只有后端单向推送需求
- 不需要维护复杂长连接协议状态
- FastAPI + fetch stream 即可完成
- 对当前查询型单次任务模型更简单、更稳定

### 15.3 现有局限

当前 SSE 模式仍有边界：

- ProgressService 是进程内 subscriber queue，不是跨进程事件总线
- 多 worker / 多实例场景下，需要额外的共享事件机制才可横向扩展
- 现在更适合单实例或同进程内的工作台执行流

这点在后续如果要走多副本部署，需要单独升级。

---

## 16. demand 横表专项原则

demand 是当前架构里最容易被误解的区域，所以单列说明。

### 16.1 问题本质

`p_demand / v_demand` 不是标准纵表，而是横向月度需求表。

这意味着：

- `MONTH` 不是“唯一月份字段”那么简单
- `REQUIREMENT_QTY` / `NEXT_REQUIREMENT` / `LAST_REQUIREMENT` / `MONTH4...` 对应的是相对 base month 的多个月份值
- `ttl` / total 月度问题通常需要“展开后再按 demand_month 聚合”

### 16.2 当前系统的正确处理方式

不是靠数据库预建横转纵对象强绑定，而是靠四层共同保证：

1. `business_knowledge.json` 提供稳定业务口径
2. PromptBuilder 给 demand 专项 SQL shape 指令
3. few-shot 提供真实问题 SQL 形态参考
4. SqlValidator 校验：
   - 月份映射
   - `PM_VERSION` 投影
   - monthly shape

### 16.3 什么不能做

不能用以下方式“偷懒”：

- 把 demand 所有 SQL 写成本地模板
- 在 domain config 里硬编码一题一 SQL
- 让配置直接决定 UNION ALL / CTE 结构

这些会把系统重新拖回 template-first，而不是 LLM-first。

---

## 17. 配置规则治理边界

系统允许有少量配置规则，但边界必须严格。

### 17.1 可以进配置的内容

适合放配置：

- 时间解析：`26年 -> 2026`
- 枚举映射：`投入 / 产出 / 报废 -> act_type`
- 稳定字段别名
- 被多个真实问题验证过的稳定 metric 消歧

### 17.2 不可以进配置的内容

不适合放配置：

- 某个问题对应哪条固定 SQL
- 某个场景必须怎么 join
- 横表必须怎么展开
- UNION / CTE / SELECT shape 模板

这些应该优先落在：

- `business_knowledge.json`
- PromptBuilder
- example
- replay / eval case
- validator

### 17.3 最简单判断法

- 如果规则回答的是“这句话是什么意思”，可以考虑进配置
- 如果规则回答的是“SQL 应该怎么写”，就不应该进配置

---

## 18. 运行时数据分层

### 18.1 业务库

业务库只负责：

- 承载真实业务数据
- 提供只读 SQL 执行对象

它不负责：

- 会话
- trace
- 日志
- feedback
- eval

### 18.2 runtime 库

runtime 库负责：

- users / roles / permissions
- sessions / messages / session state snapshots
- query_logs
- sql_audit_logs
- traces
- feedback
- evaluation_runs

这种拆分的好处是：

- 业务库污染最小
- 会话系统可独立演进
- 调试资产可长期保留
- 权限与审计能放在同一运行时域里统一治理

---

## 19. 调试与扩展建议

### 19.1 调试优先级

遇到准确率问题，优先顺序应始终是：

1. 修 `tables.json`
2. 修 `business_knowledge.json`
3. 补真实 few-shot / example
4. 修 PromptBuilder 上下文选择
5. 修 QueryPlan / SQL validator 边界
6. 最后才考虑加局部规则

### 19.2 不要轻易破坏的边界

后续重构时，这些边界不建议破坏：

- `workspace` 仍应保持前端主恢复入口
- orchestrator 仍应是主链路唯一总控
- QueryPlan 和 SQL validator 必须保留前后双重治理
- 会话 / Trace 归属校验必须保留
- response snapshot 必须保留，否则历史恢复会退化

### 19.3 可以继续增强的方向

后续合理增强方向包括：

- 把 progress event 升级成跨进程事件总线
- 给 trace step 增加更细粒度的 prompt/context 摘要
- 引入更强的 eval 资产治理和 golden trace 比对
- 对高频稳定复杂口径做“评审后”的数据库侧固化，但必须是补充层，不是主路径替代

---

## 20. 一句话总结

当前工程的本质不是“聊天机器人”，也不是“规则模板 SQL 系统”，而是一个：

**以 LLM 为 SQL 生成核心、以 Query Plan/Validator/Permission/Runtime 为治理闭环、以 Workspace/Trace 为恢复与调试入口的企业级 Text2SQL 工作台。**

如果后续要继续演进，正确方向是：

- 提升知识质量
- 提升上下文选择质量
- 提升 validator 和 replay 能力
- 提升前后端对 trace / progress / workspace 的联动稳定性

而不是把系统重新拉回“大量硬编码规则 + 本地 SQL 模板”的旧路径。
