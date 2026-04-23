# 后端差距分析

## 1. 说明

本文基于以下文档与当前后端实现对照整理：

- [TEXT2SQL_ARCHITECTURE.md](/home/yang/code/text2sql/TEXT2SQL_ARCHITECTURE.md)
- [DEVELOPMENT_PLAN.md](/home/yang/code/text2sql/DEVELOPMENT_PLAN.md)
- [TODO_BACKLOG.md](/home/yang/code/text2sql/TODO_BACKLOG.md)

本文只整理当前范围内仍未完成或仍需继续完善的后端能力。

以下两项已明确移出当前范围，因此不再视为缺口：

- 企业认证能力，包括 `SSO / OAuth2 / OIDC / 用户目录同步`
- 组织/部门模型

当前后端状态可以概括为：

- 主链路已可运行：分类、规划、SQL 生成、校验、执行、回答、会话、审计、反馈、评测、管理端接口均已有首版
- 系统更接近“可继续增强的 alpha/beta 后端”，而不是架构文档中的最终态
- 当前差距主要集中在 `真实模型能力`、`执行治理`、`检索增强`、`规则下沉`、`管理治理补强` 这几类能力

## 2. 未实现

### 2.1 稳定的真实混合检索体系

当前情况：

- 已有 `example + semantic_view + metric + knowledge` 的多源检索
- 已有向量检索接口骨架
- 默认仍以本地轻量向量化占位实现为主

尚未实现的目标能力：

- 稳定的真实 embedding provider 接入
- 真实向量库或稳定索引层
- 明确的检索重排治理与线上可调参数体系
- 更完整的召回来源解释与效果分析闭环

代码参考：

- [vector_retriever.py](/home/yang/code/text2sql/backend/app/services/vector_retriever.py)
- [retrieval_service.py](/home/yang/code/text2sql/backend/app/services/retrieval_service.py)

### 2.2 真正落地的数据库语义视图层

当前情况：

- `semantic_views` 已存在于语义配置和检索/规划链路中
- Planner 会参考语义视图进行排序和选择

尚未实现的目标能力：

- 设计文档中建议的语义视图真正落成数据库可执行对象
- 复杂底表预处理逻辑前移到视图层，而不是继续主要依赖运行时规划和生成
- 例如 `semantic_inventory_view`、`semantic_plan_actual_view`、`semantic_demand_perf_view`、`semantic_demand_unpivot_view` 这类统一口径视图的实际执行接入

代码参考：

- [retrieval_service.py](/home/yang/code/text2sql/backend/app/services/retrieval_service.py)
- [query_planner.py](/home/yang/code/text2sql/backend/app/services/query_planner.py)

### 2.3 缓存、限流、告警

当前情况：

- 主链路可以运行，但尚未形成完整平台治理能力

尚未实现的目标能力：

- 高频查询缓存
- API 限流
- 慢查询告警
- 异常告警
- 更系统化的运行监控

## 3. 部分实现，仍需完善

### 3.1 LLM 主链路仍偏“启发式主导 + LLM hint 增强”

当前情况：

- 已接入真实 LLM client
- `query_plan`、`classification`、`sql hint` 已支持超时、重试、失败回退
- Orchestrator 已能在 LLM hint 不可靠时回退本地规划和本地 SQL 生成

仍需完善：

- 进一步收紧 Planner 输出边界，不让 hint 轻易越出语义层白名单
- 提升 SQL Generator 对 Query Plan 的强绑定程度
- 增加更多真实模型联调，降低对本地启发式的依赖
- 让主链更接近设计文档中的“先规划后生成”的稳定生产模式

代码参考：

- [llm_client.py](/home/yang/code/text2sql/backend/app/services/llm_client.py)
- [query_planner.py](/home/yang/code/text2sql/backend/app/services/query_planner.py)
- [orchestrator.py](/home/yang/code/text2sql/backend/app/services/orchestrator.py)

### 3.2 问题分类器已可用，已转向“语义特征分析 + LLM 仲裁”，但稳定性仍需完善

当前情况：

- 已基于 `semantic_parse + session_state + semantic_diff` 工作
- 已支持 `follow_up / new_related / new_unrelated / clarification_needed / invalid / new` 等判定
- 已从“规则短路命中”改为“候选分类打分 + 规则加权 + 受限 LLM 仲裁”
- classification prompt 已包含候选分数、冲突信号、`context_delta` 字段说明、业务化 few-shot 与继承目标摘要
- 已支持在 `follow_up` 场景下让 LLM 仲裁更细的 `context_delta`

仍需完善：

- 继续减少残余启发式打分偏差，尤其是边界 case 对 follow-up 与 new_related/new_unrelated 的区分
- 用真实模型联调验证仲裁 prompt 的稳定性，而不只停留在本地结构检查
- 扩充分类回归样本和业务化 few-shot，使 `context_delta` 输出更稳定覆盖版本、过滤、维度、排序、limit 等变更
- 细化澄清原因标签，并补分类阶段效果评测与失败归因

代码参考：

- [question_classifier.py](/home/yang/code/text2sql/backend/app/services/question_classifier.py)
- [prompt_builder.py](/home/yang/code/text2sql/backend/app/services/prompt_builder.py)

### 3.3 SQL 治理已具骨架，但企业级约束仍不足

当前情况：

- 已限制只读 SQL
- 已支持 SQL 校验、权限过滤、执行限制、状态分类
- 已支持 AST 优先、轻量解析回退的双通道校验思路

仍需完善：

- 增加更强的 Query Plan 与 SQL 一致性校验
- 增加缺失权限条件、缺失时间条件、可疑 Join 的更细粒度检查
- 增加复杂度风险和执行风险提示
- 进一步加强结果规模治理和执行分类

代码参考：

- [orchestrator.py](/home/yang/code/text2sql/backend/app/services/orchestrator.py)
- [permission_service.py](/home/yang/code/text2sql/backend/app/services/permission_service.py)

### 3.4 下载与失败回放已落地首版，但治理仍需完善

当前情况：

- 已新增 `can_download_results` 权限，并接通结果导出接口
- 已支持按 `trace_id` 导出结果 CSV
- 已支持评测 case replay 和运行时 query log replay
- 管理端已能直接对最近查询日志发起复跑

仍需完善：

- 为下载行为补审计日志和管理端可观测性
- 为 replay 增加分类/检索/规划/SQL 的分阶段差异对比
- 让 replay 结果可沉淀为失败样本或评测样本
- 补评测 case 列表、单 case 复跑和失败筛选界面

代码参考：

- [chat.py](/home/yang/code/text2sql/backend/app/api/routes/chat.py)
- [admin.py](/home/yang/code/text2sql/backend/app/api/routes/admin.py)
- [evaluation_service.py](/home/yang/code/text2sql/backend/app/services/evaluation_service.py)


### 3.5 数据权限模型是简化版，不是完整策略治理体系

当前情况：

- 已有角色
- 已有数据范围字段：`factories / sbus / bus / customers / products`
- 已有字段可见性控制

仍需完善：

- 继续补足权限覆盖校验
- 让权限注入与 Query Profile 的 `permission_scope_fields` 更稳定对齐
- 补更多管理端侧的权限观测与配置辅助能力

说明：

- 当前不追求复杂组织树与企业级策略引擎
- 但在现有范围内，权限治理仍有继续增强空间

代码参考：

- [policy_engine.py](/home/yang/code/text2sql/backend/app/services/policy_engine.py)
- [permission_service.py](/home/yang/code/text2sql/backend/app/services/permission_service.py)

### 3.6 运行时持久化已数据库化，但治理能力还不完整

当前情况：

- 会话、消息、快照、反馈、查询日志、SQL 审计、检索日志、评测运行都已进入数据库
- 管理端已有运行状态、会话、日志、反馈、评测相关接口

仍需完善：

- 继续补更多管理查询接口
- 为关键运行时表补索引
- 设计归档策略与数据保留策略
- 提高管理端面向运维分析的查询能力

代码参考：

- [db_session_repository.py](/home/yang/code/text2sql/backend/app/repositories/db_session_repository.py)
- [db_runtime_log_repository.py](/home/yang/code/text2sql/backend/app/repositories/db_runtime_log_repository.py)
- [runtime_admin_service.py](/home/yang/code/text2sql/backend/app/services/runtime_admin_service.py)

### 3.7 Metadata / Example 管理仍然是文件治理，不是统一运行时治理

当前情况：

- 后端已有 metadata 文档管理和 example 管理接口
- 运行时 reload 已接通

仍需完善：

- 如果后续希望更强治理能力，需要考虑把 metadata/example 也纳入数据库或更正式的配置治理流程
- 补更细的版本控制、变更审计和发布流程

代码参考：

- [metadata_service.py](/home/yang/code/text2sql/backend/app/services/metadata_service.py)

### 3.8 示例库规模和覆盖度不足

当前情况：

- 示例库已经存在并接入检索
- 已能支撑最小主链路

仍需完善：

- 扩大到高频业务问法规模
- 提升 follow-up、clarification、unrelated 场景覆盖
- 增加库存、计划/实际、需求/销售对比、版本类典型样例

代码参考：

- [retrieval_service.py](/home/yang/code/text2sql/backend/app/services/retrieval_service.py)
- [metadata_service.py](/home/yang/code/text2sql/backend/app/services/metadata_service.py)
- [evaluation_service.py](/home/yang/code/text2sql/backend/app/services/evaluation_service.py)

### 3.9 真实数据库执行环境与生产治理还需补强

当前情况：

- SQL 执行器已经可跑
- 状态分类已补到 `empty_result / truncated / timeout / db_error`

仍需完善：

- 使用稳定的真实只读账号与生产级连接策略
- 提升慢查询、连接失败、超时等场景的治理力度
- 加强返回规模、并发与执行风险的管理

## 4. 当前建议优先级

### P0：优先补

- 真实 LLM 主链收紧与联调
- 真实数据库执行治理补强
- 继续弱化规则，往语义层和 Query Profile 下沉
- 示例库扩充与真实混合检索接入

### P1：第二批补

- SQL 治理增强
- 分类器稳定性提升
- 运行时数据治理与管理端查询增强
- metadata/example 治理增强

### P2：可后置

- 下载类权限
- 独立失败回放工具链
- 缓存、限流、告警

## 5. 不纳入当前范围

以下内容不再作为当前后端缺口跟踪：

- 企业认证，包括 `SSO / OAuth2 / OIDC / 用户目录同步`
- 组织/部门模型
