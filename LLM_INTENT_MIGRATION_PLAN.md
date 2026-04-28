# LLM Intent Migration Plan

## 1. 背景

当前工程虽然已经是 `LLM-first SQL generation`，但在 `QueryIntentParser / QuestionClassifier / QueryPlanner` 这三层，仍然以本地规则、别名匹配、打分逻辑和确定性推导为主。

这会带来几个稳定问题：

- 新表达法、口语问法、组合式问题容易漏解析
- 样例没覆盖过的问题，前置理解层更容易掉到 `unknown_request / missing_metric`
- `domain_config` 容易持续膨胀成“规则库”
- planner 不仅在“编排”，还在承担一部分“猜语义”的职责

目标不是把所有本地逻辑删掉，而是把理解链路改成：

**LLM 主理解 + 本地结构化约束 + validator 收口**

---

## 2. 改造目标

### 2.1 总目标

把当前：

**parser/classifier/planner-first**

迁移为：

**shallow parse + LLM intent understanding + deterministic planning**

### 2.2 预期收益

- 提升未覆盖表达的识别率
- 提升真实问题、未见样例问题的首轮可执行率
- 降低靠不断加规则补洞的维护成本
- 让 `domain_config` 回归“稳定语义配置”，而不是“问题规则池”

### 2.3 不在本次目标内

- 不重写 SQL validator
- 不直接让 LLM 跳过 QueryPlan 生成 SQL
- 不移除当前 session state / trace / replay / eval 体系
- 不一次性删除所有旧规则路径

---

## 3. 现状诊断

### 3.1 当前职责分布

- `backend/app/services/query_intent_parser.py`
  - 别名命中
  - 时间 / 版本 / filter / sort / limit 浅层抽取
  - demand 特殊快捷规则

- `backend/app/services/question_classifier.py`
  - smalltalk / invalid guard
  - follow-up / new_related / new_unrelated / clarification 的本地打分
  - 少量 LLM relevance / arbitration

- `backend/app/services/query_planner.py`
  - 基于 parser/classifier 结果组装 QueryPlan
  - 合并 session context
  - 推导 dimensions / sort / limit / table selection

### 3.2 当前核心问题

- parser 追求高覆盖时，规则会不断膨胀
- classifier 仍然主要依赖手工评分和规则 bonus
- planner 还在承担部分语义推断职责
- LLM 目前介入得太晚，主要在 query plan hint 和 SQL generation 阶段

---

## 4. 目标架构

### 4.1 新链路

目标链路如下：

1. `ShallowIntentExtractor`
2. `LLMIntentService`
3. `IntentNormalizer`
4. `QuestionClassifier`（LLM 主判定，本地 hard guard）
5. `QueryPlanner`（只做结构编排）
6. `QueryPlanValidator`
7. `SQL generation / SQL validation / execution`

### 4.2 职责边界

#### `ShallowIntentExtractor`

只保留高确定性抽取：

- 时间
- version
- topN / limit
- 明确枚举值
- 高确定性实体别名

特点：

- 高 precision
- 低 recall 可接受
- 不再要求它独立完成完整 intent 理解

#### `LLMIntentService`

作为主理解器，输出结构化 intent：

- `subject_domain`
- `metrics`
- `dimensions`
- `filters`
- `time_context`
- `version_context`
- `analysis_mode`
- `question_type`
- `inherit_context`
- `confidence`
- `reason`

#### `IntentNormalizer`

负责把 LLM 输出收敛到当前工程可接受的内部结构：

- metric 名称标准化
- field 合法性检查
- domain 合法性检查
- 时间/version 合法性修正
- 非法字段剔除或降级

#### `QuestionClassifier`

保留本地强约束，但把主分类判断交给 LLM：

- 保留：
  - invalid/smalltalk hard guard
  - allowed question types check
  - follow-up 安全边界
- 迁出：
  - 大量手工打分和规则 bonus

#### `QueryPlanner`

不再负责“猜语义”，只负责：

- context merge
- table selection
- dimension suggestion
- default sort / limit
- domain constraints
- 产出稳定 QueryPlan

---

## 5. 执行原则

### 5.1 渐进迁移，不一次切主链路

必须先走 `shadow mode`，不能直接替换现有 parser/classifier/planner。

### 5.2 LLM 负责理解，本地代码负责约束

- 语义泛化交给 LLM
- 结构安全、字段合法性、domain 约束交给本地代码

### 5.3 配置只承载稳定语义

`domain_config` 中只允许放：

- 稳定别名
- 枚举映射
- 默认业务口径
- 被多个真实问题验证过的稳定消歧

不允许继续放：

- 问题级规则
- 某个真实问题专属 planner 特判
- 固定 SQL 结构规则

### 5.4 保留完整回退路径

在新链路稳定前，必须保留：

- 本地 parser 结果
- 本地 classifier 结果
- 当前 planner 编排路径

### 5.5 优先沉淀真实问题，不优先加规则

对未覆盖问法，默认处理顺序固定为：

1. 先补真实问题样例
2. 再补 prompt / business knowledge
3. 再补 normalizer / validator 收口
4. 最后才考虑增加稳定语义配置

这条顺序的目的，是避免问题一出现就把 parser / classifier / planner 再拉回规则驱动。

### 5.6 规则准入边界

后续遇到问题时，按下面边界处理：

- **真实表达差异**
  - 优先补到 real examples / prompt 中
- **稳定业务术语**
  - 允许补到 `domain_config`
  - 例如固定缩写、稳定枚举、长期存在的业务别名
- **结构安全问题**
  - 补到 normalizer / validator
  - 例如非法字段、非法时间、非法 domain 组合
- **单题特判**
  - 不允许进入 parser / planner
  - 也不允许继续把 `domain_config` 当问题规则池使用

---

## 6. 迁移开关与灰度策略

为了保证迁移可控，建议在 orchestrator 层增加明确开关。

### 6.1 建议开关

- `INTENT_SHADOW_ENABLED`
  - 是否并行生成 LLM intent 并写入 trace
- `INTENT_PRIMARY_ENABLED`
  - 是否让 normalized intent 进入主链路
- `CLASSIFIER_LLM_PRIMARY_ENABLED`
  - 是否让 LLM 成为主分类判定来源
- `INTENT_FALLBACK_ENABLED`
  - 新链路失败时是否回退本地链路

### 6.2 建议灰度顺序

1. 仅本地链路
2. 本地链路 + intent shadow
3. intent 主链路 + classifier 仍走旧链路
4. intent 主链路 + classifier LLM 主判定
5. 收缩旧规则

### 6.3 每阶段必须可回退

每个阶段上线时，都必须满足：

- 关闭对应开关即可回退
- trace 中能分清新旧链路产物
- 新链路失败不会阻断主查询

---

## 7. 分阶段执行计划

### Phase 0：补观测，不改行为

### 目标

先把“问题漏在哪里”看清楚。

### 任务

- 给 parser 增加更细粒度日志
- 给 classifier 增加得分明细和边界日志
- 给 planner 增加输入/输出快照
- 在 trace 中保留：
  - shallow parse
  - classification score details
  - final query plan

### 涉及文件

- `backend/app/services/query_intent_parser.py`
- `backend/app/services/question_classifier.py`
- `backend/app/services/query_planner.py`
- `backend/app/services/orchestrator.py`
- `backend/app/services/audit_service.py`

### 验收

- 单题调试时能明确判断：
  - parser 漏了什么
  - classifier 误判了什么
  - planner 又补了什么

### 输出物

- trace 中新增 intent 相关调试节点
- 单题问题可直接定位到 parser / classifier / planner 的责任边界
- 为 Phase 3 shadow mode 提前准备对比视图

---

### Phase 1：定义新的 Intent 契约

### 目标

把“语义理解”和“规划编排”完全解耦。

### 任务

- 新增 `StructuredIntent` 模型
- 定义字段 schema
- 约定 parser 输出、LLM 输出、normalizer 输出的统一结构

### 建议文件

- `backend/app/models/intent.py`

### 验收

- 新的 intent schema 可以独立序列化、落 trace、回放

### 输出物

- `StructuredIntent` 模型
- intent schema 文档
- 与 QueryPlan 的字段映射约定

---

### Phase 2：Parser 降级为 Shallow Extractor

### 目标

让 parser 从“最终理解器”变成“浅层高精度信号抽取器”。

### 任务

- 重命名或重构 `QueryIntentParser`
- 删除它对完整意图覆盖的职责预期
- 保留：
  - 时间
  - version
  - topN / limit
  - 枚举过滤
  - 明显实体命中

### 建议文件

- `backend/app/services/query_intent_parser.py`

### 验收

- parser 输出更小、更稳定
- 不再为了追求 recall 不断加问题特判

### 输出物

- 更小的 shallow parse 结构
- parser 职责文档更新
- 删除或停用一批“为了补 recall 而写的 parser 特判”

---

### Phase 3：引入 LLM Intent，先 shadow mode

### 目标

先并行跑，不切主链路。

### 任务

- 新增 `LLMIntentService`
- 新增 `build_intent_prompt`
- 新增 `llm_client.generate_intent()`
- 输入：
  - question
  - shallow parse
  - session state
  - relevant schema summary
  - business knowledge
  - real examples
- 输出：
  - `StructuredIntent`
- 只落日志，不参与最终执行

### 建议文件

- `backend/app/services/intent_service.py`
- `backend/app/services/prompt_builder.py`
- `backend/app/services/llm_client.py`
- `backend/app/services/orchestrator.py`

### 验收

- 可以对比本地 parser 和 LLM intent
- 能看到 LLM 在哪些真实问题上补齐了未覆盖表达

### 输出物

- `LLMIntentService`
- intent prompt
- trace 中的新旧理解结果对比
- 一组基于真实问题的 shadow 对比样本

---

### Phase 4：增加 Intent Normalizer / Validator

### 目标

LLM 先理解，但不能直接信。

### 任务

- 新增 `IntentNormalizer`
- 做：
  - metric 标准化
  - field 映射
  - unknown field 剔除
  - domain 合法性验证
  - version/time 修正
- 对低置信度或非法 intent 做降级策略

### 建议文件

- `backend/app/services/intent_normalizer.py`

### 验收

- LLM 输出即使不干净，也能被收口为合法内部结构

### 输出物

- `IntentNormalizer`
- 非法 intent 降级策略
- intent 字段白名单 / 映射表

---

### Phase 5：QuestionClassifier 切到 LLM 主判定

### 目标

减少当前大量本地打分规则。

### 任务

- 保留本地 hard guard：
  - smalltalk / invalid
  - allowed question types
  - follow-up safety checks
- 让 LLM 主输出：
  - `follow_up`
  - `new_related`
  - `new_unrelated`
  - `clarification_needed`
- 本地只做 accept / reject

### 涉及文件

- `backend/app/services/question_classifier.py`
- `backend/app/services/prompt_builder.py`
- `backend/app/services/llm_client.py`

### 验收

- 分类边界案例减少靠人工评分调参
- classification rules 数量开始下降

### 输出物

- 新版分类 prompt
- 分类置信度与拒绝原因
- 本地 hard guard 与 LLM 判定的边界说明

---

### Phase 6：QueryPlanner 回归 deterministic compiler

### 目标

planner 不再主导理解，只消费 intent。

### 任务

- planner 输入改成 normalized intent
- 只保留：
  - context merge
  - table selection
  - dimensions suggestion
  - sort / limit defaults
  - domain constraints
- 去掉 planner 中“补猜语义”的逻辑

### 涉及文件

- `backend/app/services/query_planner.py`
- `backend/app/services/query_plan_compiler.py`

### 验收

- planner 逻辑更短、更稳定
- 语义问题主要集中在 intent 层，不再蔓延到 planner

### 输出物

- planner 输入切换到 normalized intent
- planner 中语义猜测逻辑收缩
- planner 责任边界文档

---

### Phase 7：切主链路并保留 fallback

### 目标

正式启用新链路，但保留安全回退。

### 任务

- 主链路改为：
  - shallow parse
  - LLM intent
  - intent normalization
  - classifier
  - planner
- 当以下情况触发回退：
  - LLM intent 为空
  - LLM intent 不合法
  - confidence 过低
  - normalizer 无法收口
- fallback 到现有本地解析链路

### 验收

- 新链路可稳定运行
- fallback 次数可观测
- 线上不出现大面积澄清率或 SQL 失败率上升

### 输出物

- 主链路切换开关
- fallback 监控
- 新旧链路效果对比报表

---

### Phase 8：收缩旧规则

### 目标

防止旧规则继续反向增长。

### 任务

- 逐步删除无必要规则
- 清理只服务个别问题的 alias / classifier bonus / planner 特判
- 保留真正稳定的业务语义规则

### 验收

- `domain_config` 停止继续长成规则库
- parser/classifier 本地规则体量下降

### 输出物

- 旧规则清理清单
- 被删除规则对应的真实问题回归记录
- 新的规则准入说明

---

## 8. 验收指标

### 8.1 线上/联调指标

- `unknown_request` 占比下降
- `missing_metric` 误报下降
- 首轮 SQL valid 率提升
- 首轮空 query plan 率下降
- follow-up/new 误判率下降

### 8.2 真实问题集指标

- 未见样例问题覆盖率提升
- 真实问题首轮 QueryPlan 合法率提升
- 真实问题首轮 SQL 通过率提升

### 8.3 工程指标

- `domain_config` 规则膨胀速度下降
- 分类器本地打分规则减少
- planner 职责更聚焦

---

## 9. 风险与控制

### 风险 1：LLM 输出不稳定

控制：

- 结构化 schema 输出
- normalizer 收口
- validator 保底
- fallback 保留

### 风险 2：时延变高

控制：

- 先 shadow mode 测量
- 仅在 sparse/ambiguous case 启动 LLM intent
- 后续再决定是否全量启用

### 风险 3：调试复杂度提升

控制：

- trace 中明确拆出：
  - shallow parse
  - llm intent
  - normalized intent
  - final query plan

### 风险 4：团队再次回到加规则补洞

控制：

- 明确规则准入边界
- 真实问题优先沉淀到 example / business knowledge / prompt
- 不是所有漏判都进 `domain_config`

---

## 10. 第一批建议执行项

按投入产出比，第一批只做三件事：

1. **Phase 0**
   - 补观测

2. **Phase 1**
   - 定义 `StructuredIntent`

3. **Phase 3（shadow mode）**
   - 引入 `LLMIntentService`，先并行跑、不切主链路

原因：

- 风险最小
- 最快看到 LLM intent 是否真能覆盖更多真实问法
- 不会马上打断现有线上行为

### 第一批明确不做

第一批暂时不做以下动作：

- 不切 classifier 主链路
- 不重写 planner
- 不删除现有 parser/classifier/planner
- 不把所有历史规则一次性清空

---

## 10.1 当前落地进度（2026-04-28）

截至当前，这份计划里的主要阶段已经有真实代码落地：

- **Phase 0**
  - 已完成
  - `parse_intent / shadow_intent / normalized_intent / classify_question / plan` 已独立进入 trace
- **Phase 1**
  - 已完成
  - 已新增 `backend/app/models/intent.py`
- **Phase 3**
  - 已完成
  - 已新增 `LLMIntentService`，并进入 shadow mode
- **Phase 4**
  - 已完成
  - 已新增 `IntentNormalizer`
- **Phase 5**
  - 已完成最小版本
  - `QuestionClassifier` 已切为 `LLM-primary + local fallback/hard guard`
- **Phase 6**
  - 已完成最小版本
  - deterministic 规划逻辑已收回 `QueryPlanner`
- **Phase 7**
  - 已完成最小版本
  - 已新增 `INTENT_PRIMARY_ENABLED / INTENT_FALLBACK_ENABLED`
  - normalized intent 已可在主链路中被选择
- **Phase 8**
  - 已完成第一轮收缩
  - classification rule bonus 不再主导 LLM-primary 分类路径
  - orchestrator 侧重复规划逻辑已删除

当前仍未完成的，不是主链路迁移，而是后续收尾工作：

- 用更多真实问题验证 `normalized intent` 的选择策略
- 继续收缩不再必要的 parser / classifier 补洞规则
- 修复 runtime 数据库环境，使 trace / audit / query log 可以稳定持久化

---

## 11. 里程碑

### Milestone A：可观测

- 能看清 parser/classifier/planner 漏点

### Milestone B：LLM intent shadow

- 能稳定产出结构化 intent
- 能与现有链路做效果对比

### Milestone C：LLM classifier 主判定

- 分类边界问题明显减少

### Milestone D：Planner 回归编排器

- 理解与编排解耦完成

### Milestone E：旧规则收缩

- 工程不再靠持续堆规则扩展覆盖

---

## 12. 本计划对应的代码边界

优先涉及：

- `backend/app/services/query_intent_parser.py`
- `backend/app/services/question_classifier.py`
- `backend/app/services/query_planner.py`
- `backend/app/services/prompt_builder.py`
- `backend/app/services/llm_client.py`
- `backend/app/services/orchestrator.py`

预计新增：

- `backend/app/models/intent.py`
- `backend/app/services/intent_service.py`
- `backend/app/services/intent_normalizer.py`

保留并继续依赖：

- `backend/app/services/semantic_runtime.py`
- `backend/app/services/query_plan_compiler.py`
- `backend/app/services/query_plan_validator.py`
- `backend/app/services/sql_validator.py`

---

## 13. 落地顺序建议

建议按下面顺序落地，而不是并行大改：

1. 先做 Phase 0
   - 先把现状看清楚
2. 再做 Phase 1
   - 先定义 intent 契约，避免后面边做边改模型
3. 再做 Phase 3
   - 先 shadow 跑真实问题
4. 再做 Phase 4
   - 确认 LLM intent 常见脏点后再补 normalizer
5. 最后再推进 Phase 5 / 6 / 7 / 8
   - 先证明确实有效，再切主链路

这样做的原因是：

- 可以最早拿到真实收益判断
- 可以控制架构改动范围
- 可以避免一开始就把 parser/classifier/planner 全部推倒重来

---

## 14. 最终判断

这次迁移不是“要不要继续加规则”的问题，而是：

**把 parser/classifier/planner 从“规则主理解器”迁成“LLM 主理解 + 本地治理编排器”。**

如果这条线不改，后续每出现一种新表达，系统都会继续倾向于：

- 加 alias
- 加 extractor
- 加 classification score
- 加 planner 特判

这条路长期不可维护。
