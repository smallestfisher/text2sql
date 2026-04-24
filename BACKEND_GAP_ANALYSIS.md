# 后端差距分析

## 1. 说明

本文基于以下文档与当前后端实现对照整理：

- [TEXT2SQL_ARCHITECTURE.md](/home/y/llm/new/TEXT2SQL_ARCHITECTURE.md)

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
- 已补基础混排治理和更细的检索解释输出

当前判断：

- 这部分不是当前阶段的刚性优先项
- 现有轻量检索已足够支撑离线规则收敛、回归和真实联调前准备
- 是否升级到“真实混合检索体系”，应由真实联调结果触发，而不是先行重投入

仍未实现、但需由真实联调触发的目标能力：

- 稳定的真实 embedding provider 接入
- 真实向量库或稳定索引层
- 基于真实问法效果的检索重排治理与线上可调参数体系
- 更完整的召回来源解释与效果分析闭环

建议触发条件：

- 真实问题样本规模上来后，轻量检索开始频繁排错
- 语义相近但词面差异较大的真实问法召回明显不足
- example / semantic_view / knowledge 的现有混排已成为主链瓶颈

代码参考：

- [vector_retriever.py](/home/y/llm/new/backend/app/services/vector_retriever.py)
- [retrieval_service.py](/home/y/llm/new/backend/app/services/retrieval_service.py)

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
- 示例库扩充，并在真实联调确认检索瓶颈后再升级真实混合检索

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

## 6. 近期可先做的事项

这部分不再按“还能继续补哪些离线能力”来列，而是按“当前阶段最值得做、且最接近真实联调”的事项来列。原则很明确：

- 不再继续投入低 ROI 的平台化治理主题
- 优先把后端推进到“可接真实数据、可收真实问题、可快速定位问题”
- 所有新增工作都尽量服务于下一阶段的真实调优，而不是继续做脱离场景的抽象建设

### 6.1 P0：准备第一批真实数据接入清单

建议优先做：

- 明确首批要接的真实表、视图或数据快照
- 为每个数据源补齐字段说明：主键、时间字段、版本字段、组织字段、常用指标字段
- 明确哪些字段已经可以进入语义层，哪些字段先不暴露
- 形成一份最小可联调的数据清单，避免一开始就追求全量接入

为什么现在先做：

- 后续很多问题不是代码逻辑问题，而是字段口径、时间口径、版本口径没有先对齐
- 这一步越早做，后续 LLM、检索、SQL、语义层调优越不会反复返工

建议交付物：

- 一份真实数据接入清单文档
- 一份字段映射表或语义映射草稿
- 一批可用于只读验证的样例 SQL

验收标准：

- 团队能明确说清楚“先拿哪几张表做第一轮联调”
- 每张表的关键口径字段都有初步说明

### 6.2 P0：收集第一批真实问题样本

建议优先做：

- 收集首批真实用户问题，先不追求多，先追求典型
- 样本至少覆盖：库存、计划/实际、需求、销售、版本对比、时间趋势、排序 TopN、多轮追问
- 为每条问题补最小标注：领域、是否多轮、期望指标、期望维度、是否需要澄清
- 把问题样本整理成后续可沉淀到 eval/example 的格式

为什么现在先做：

- 当前主链路离线能力已经差不多，真正缺的是“真实问法分布”
- 没有真实问题，就无法判断到底该继续前移规则、补语义层，还是增强 LLM 理解

建议交付物：

- 一份首批真实问题样本表
- 样本分类标签约定
- 可回放的问题导入格式

验收标准：

- 至少有一批能覆盖主要业务主题的真实问题
- 每条问题都能进入回放和后续归因流程

### 6.3 P0：建立真实问题调优闭环

建议优先做：

- 为真实问题建立统一归因维度：`classification / retrieval / query_plan / sql / execution / answer`
- 每修一类问题，都要求能回放、能复现、能判断修复是否有效
- 形成“问题录入 -> 复跑 -> 定位 -> 修复 -> 回归”的最小流程
- 把真实问题调优和已有 offline regression 串起来，而不是分成两套体系

为什么现在先做：

- 接入真实数据后，最大的风险不是缺能力，而是问题很多但无法快速归因
- 没有调优闭环，后续优化会重新回到拍脑袋修问题

建议交付物：

- 一份失败归因模板
- 一套最小问题状态流转约定
- 将真实样本纳入 replay/regression 的落库或文件约定

验收标准：

- 任意一条真实失败问题都能被清楚归到某一个主环节
- 修复后能快速验证是否真的改善

### 6.4 P1：继续收紧 Query Plan 与 SQL 风险边界

建议优先做：

- 继续加强 `QueryPlan -> SQL` 一致性校验
- 对 `join / group by / order by / limit / version filter / permission filter` 保持逐项核对
- 把现有风险输出继续用于真实问题归因，而不只是离线校验
- 优先修“会产生业务误解但不一定报错”的灰区问题

为什么放在这一档：

- 这部分已经有较完整骨架
- 后续继续补的价值，主要体现在真实联调时能更快识别“看似能跑、其实不可靠”的结果

建议交付物：

- 更清晰的风险分类与错误提示
- 面向 replay/eval 的风险 diff 输出
- 与真实失败样本联动的回归 case

验收标准：

- 能更稳定地区分 `hard error / risky but executable / acceptable`
- 高风险 SQL 不再只停留在“执行成功”这一层判断

### 6.5 P1：把真实修复持续沉淀为离线资产

建议优先做：

- 每修一个真实问题，至少补一条 eval case 或 example
- 按失败类型沉淀样本：短问句、错别字、口语化、省略、多轮继承、模糊时间、版本切换、维度切换
- 把真实样本和纯人工构造样本区分标记，便于后续看效果差异

为什么现在要持续做：

- 离线资产建设本身已经不是主目标，但它仍然是避免问题反复回归的最低成本方式
- 真实样本一旦开始积累，example/eval 的价值会比此前更高

建议交付物：

- 扩充后的 [eval/evaluation_cases.json](/home/y/llm/new/eval/evaluation_cases.json)
- 按来源分类的 example 资产
- 一份失败类型与样本映射说明

验收标准：

- 最近修过的问题不会在后续改动中无声回归
- 能区分“离线构造样本表现”和“真实样本表现”

### 6.6 P2：平台化治理主题暂时冻结

当前判断：

- 完整权限策略治理不继续做
- metadata/example 的统一运行时治理不作为当前重点
- 缓存、限流、告警不作为当前主线
- 更重的混合检索、语义视图落库，也不在真实瓶颈出现前提前重投入

为什么明确写出来：

- 这能防止后续又回到“继续补看起来完整、但当前收益不高的基础设施”
- 当前阶段最缺的是效果调优和真实场景收敛，不是平台化完备性
## 7. 推荐执行顺序

如果按当前阶段来排，建议顺序如下：

1. 先进入真实数据与真实问题联调阶段，参考 [REAL_DATA_TUNING_PLAYBOOK.md](/home/y/llm/new/REAL_DATA_TUNING_PLAYBOOK.md)。
2. 先校准真实表结构、字段类型、时间字段和版本字段。
3. 再补真实高频问题样本，并把样本沉淀到回归和 example 库。
4. 之后再根据真实结果收紧 `retrieval / QueryPlan / SQL` 三段链路。
5. 最后再决定是否需要更重的检索、视图落库和执行治理优化。
