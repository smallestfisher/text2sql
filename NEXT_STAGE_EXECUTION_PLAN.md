# 下一阶段执行清单

本文档把当前最值得推进的两条线合在一起：

- `retrieval-first` 去门控一期改造清单
- 未来两周最值得补的真实资产清单

目标不是继续大改主架构，而是基于当前已经成立的主链路，持续减少前置门控、扩大真实覆盖、提高 replay / eval 的稳定收益。

---

## 1. 当前判断

当前工程已经没有明显的致命架构阻塞，主链路是成立的：

`问题理解 -> 检索 -> Query Plan -> SQL 生成 -> 校验 -> 执行 -> replay/example/eval`

所以接下来不应该再频繁重构主流程，而应该并行推进两件事：

1. 用小步改造把系统进一步推向 `retrieval-first`
2. 用真实问题补齐字段语义、知识、example、eval case 和 join pattern

这两件事必须一起做：

- 只补资产，不动前置门控，很多命中的 evidence 仍然进不了主链路
- 只动去门控，不补真实资产，LLM 和 retrieval 也没有足够证据可用

---

## 2. 现阶段不能直接“一刀切去门控”的原因

目前前置 parser / domain 还不能整块删除，原因主要有四个：

1. Retrieval 仍然强依赖 parser 先验
   当前检索打分会直接使用 `subject_domain`、`matched_metrics`、`filters`、`time_context` 等结构化信号。

2. `unknown domain` 时，LLM intent 看到的 schema 候选还不够强
   当前 intent prompt 在 `subject_domain=unknown` 时拿不到 domain tables，候选上下文会变弱。

3. QueryPlanCompiler 现在只会“补域 / 补支持表”，还不会稳定地从 retrieval 结果里选主事实表
   这意味着前面如果不给基础 domain/table，后面容易没有可执行 plan。

4. QueryPlanValidator / SqlValidator 仍然默认要求先有 `subject_domain + tables`
   现在的治理层是围绕“先有结构化 plan，再校验 SQL”设计的。

因此，当前正确方向不是“立刻拿掉 parser”，而是：

- 保留 parser 作为低歧义信号抽取器
- 逐步拿掉它作为强门控的角色
- 让 retrieval 和 LLM 在更大的候选空间里主导决策

---

## 3. 去门控一期改造清单

### 3.1 一期目标

一期只做“弱化门控”，不做“完全移除 parser”。

一期完成后的预期效果：

- parser 仍然提供时间、版本、枚举、topN、follow-up 这类硬信号
- domain 不再是 retrieval / intent prompt / plan compiler 的强前置依赖
- 当 parser 没打准 domain 时，retrieval 仍然可以把主表、支持表和 few-shot 带进来

### 3.2 一期不做的事

- 不做全面 planner 重写
- 不做完全无 domain 的自由 SQL 生成
- 不取消 QueryPlanValidator / SqlValidator 的现有 contract
- 不把系统改回“全靠向量相似度”或“全靠 LLM 自由发挥”

### 3.3 一期具体改动

#### A. 让 intent prompt 在 `unknown domain` 下也能看到 retrieval 候选

现状：

- `subject_domain=unknown` 时，intent prompt 的 `domain_tables` 为空
- LLM intent 可用的 schema 候选明显不足

要做的事：

- 在 intent 阶段先跑一轮轻量 retrieval
- 把 top-k 命中的 candidate tables / candidate domains / candidate semantic fields 一起给 LLM
- 不要求 parser 先给准 domain 才能让 LLM 看见相关表

验收标准：

- `unknown domain` 问题的 intent prompt 中不再只有空的 `domain_tables`
- 命中 example / knowledge / join_pattern 时，相关表能进入 intent 候选

#### B. 降低 retrieval rerank 对 parser domain 的依赖

现状：

- example / metric / knowledge / join_pattern 的打分都还明显吃 `query_intent.subject_domain`

要做的事：

- 把 lexical / vector / field semantics / table metadata 的权重抬高
- 把 parser domain 从“前置必要条件”降到“加分项”
- 对 `unknown domain` 或弱 domain 信号场景增加容错

验收标准：

- parser domain 错误时，相关 example / knowledge / join_pattern 仍能进入 top hits
- retrieval summary 里不再高度依赖 `domain:*` 特征才有命中

#### C. 让 QueryPlanCompiler 可以从 retrieval 里提升主事实表

现状：

- compiler 现在主要会补 domain 和支持表
- 还不会在“当前没表或表很弱”时，从高分 example / join_pattern 里提主事实表

要做的事：

- 当 `query_plan.tables` 为空或只有弱支持表时
- 允许 compiler 从 top retrieval hits 中提升一个主事实表进入 plan
- 仍然要求后续 validator 审核通过

验收标准：

- parser 没打出 domain，但 retrieval 命中真实样例时，plan 不再频繁出现“没有表”
- compiler 行为在 trace 里可见

#### D. 给 plan 编译过程增加 candidate trace

现状：

- 现在 trace 里能看到 retrieval summary，但还看不清“为什么最终选了这个 domain/table”

要做的事：

- 记录 candidate domains、candidate tables、promotion reason
- 区分 parser 提供、retrieval 提供、compiler 提升的来源

验收标准：

- trace 中能明确回答：
  - parser 原来认为是什么
  - retrieval 推荐了什么
  - compiler 最后采纳了什么

#### E. 用真实回放样本保护一期改造

现状：

- 去门控属于结构性改造，如果没有 replay/eval 保护，容易引入跨域退化

要做的事：

- 挑一批“parser 先验弱，但 retrieval 能救回”的真实问题
- 做成 replay / eval case
- 一期改造每做一步都 replay

验收标准：

- 这批 case 的 `plan_valid / sql_valid / execution_status` 至少不退化

### 3.4 一期建议顺序

1. 先做 intent prompt 候选增强
2. 再做 retrieval rerank 去 domain 依赖
3. 再做 compiler 提升主事实表
4. 最后补 trace / replay 保护

原因：

- 先增强候选输入，风险最小
- 再弱化门控，系统仍有更多证据可用
- 最后再让 compiler 接管更多职责

---

## 4. 未来两周最值得补的资产清单

### 4.1 第一优先级：字段语义目录

目标：

- 把高频业务叫法统一收进 `semantic/domain_config/base/field_semantics.json`

优先补的内容：

- `common_categories`
  - 产品大类
  - 产品类别
  - 产品分类
  - 常用分类
- 各 domain 的高频维度字段中文叫法
- 各类工厂、版本、客户、成品型号、产品型号的稳定业务说法

验收标准：

- 新增字段叫法时，优先补 `field_semantics`
- 不再为单个新叫法继续往 `extractors` 里堆散规则

### 4.2 第二优先级：`tables.json`

目标：

- 让 schema grounding 更稳定

优先补的内容：

- 字段说明是否足够像业务语言，而不是只有物理列名
- 关系字段是否完整
- month/date/version 语义是否清楚
- 容易混淆的数量口径是否在描述里写清楚

重点关注：

- `production_actuals`
- `monthly_plan_approved`
- `product_attributes`
- `p_demand`
- `v_demand`

验收标准：

- LLM 和调试人员都能从 `tables.json` 直接看懂表职责、时间字段、关系字段和口径边界

### 4.3 第三优先级：`business_knowledge.json`

目标：

- 把稳定业务口径从“对话记忆”变成可检索知识

优先补的内容：

- `MDL` 工厂实际投入 / 实际产出默认使用 panel 口径
- 其他工厂默认使用 GLS 口径
- 计划 / 审批 / 实绩三类口径的对照关系
- demand 横表月份展开原则
- 高频业务缩写和中文口语说法

验收标准：

- 高频口径问题不需要靠临时 prompt 补丁反复修

### 4.4 第四优先级：真实 example

目标：

- 让 SQL prompt 拿到更贴近真实业务的 few-shot

未来两周建议至少补的 example 类别：

1. `plan_actual`
   - `MDL + panel 默认口径`
   - `common_categories` 分组
   - 审批版 vs 实际 的月度对比
2. `demand`
   - `最新N版`
   - 横表 `demand_month` 展开
   - 产品数量 / 属性维度过滤
3. `inventory`
   - `factory_code` vs `ERP_FACTORY` 区分
   - `common_categories` 分组
4. `sales_financial`
   - 按客户 / FGCODE / 月份的 topN 和拆分

验收标准：

- 每个主 domain 至少有一组真实高频例子
- example 不是“假设题”，而是 replay 过的真实题

### 4.5 第五优先级：eval case

目标：

- 不只沉淀 few-shot，还要有可回归资产

优先补的 case：

- parser 容易弱命中的问题
- domain 容易判错的问题
- 时间语义容易错的问题
- quantity semantic 容易错的问题
- 多表 join 容易漏维表的问题

验收标准：

- 每修一类问题，至少补一个 replay / eval case

### 4.6 第六优先级：join pattern

目标：

- 把稳定多表经验从“prompt 临场发挥”变成可检索资产

优先补的 join pattern：

- `production_actuals -> product_attributes`
- `monthly_plan_approved -> production_actuals`
- `p_demand / v_demand -> product_attributes`
- `sales_financial_perf -> product_attributes`

验收标准：

- 多表 join 不再主要依赖 LLM 即时猜测

---

## 5. 两周执行节奏建议

### 第 1 周

- 补 `field_semantics`
- 补 `tables.json` / `business_knowledge.json`
- 沉淀一批真实 example / eval case
- 先做去门控一期的 `A + B`

### 第 2 周

- 做去门控一期的 `C + D`
- 用第一周沉淀的 replay/eval 保护改造
- 再补一轮 example / join pattern

---

## 6. 优先级判断法

遇到一个新问题时，按下面顺序决定要不要进这个清单：

1. 这是单题问题，还是一类问题？
2. 这是“没听懂”，还是“听懂了但 SQL 没写对”？
3. 这是该补资产，还是该做一期结构改造？

最简单判断：

- 没听懂字段叫法：先补 `field_semantics`
- 没听懂业务口径：先补 `business_knowledge`
- Query Plan 对了但 SQL 错：先补 example / prompt / validator
- parser/domain 经常把 evidence 卡死：进入去门控一期

---

## 7. Done 标准

这份计划不以“代码改了多少”为完成标准，而以这几件事为准：

1. 高价值真实问题进入 example / eval / replay 资产库
2. `unknown domain` 或弱 parser 信号场景下，retrieval 仍能把 evidence 带进主链路
3. QueryPlanCompiler 能在更多真实问题上从 retrieval 提升可执行 plan
4. 文档、trace、管理台都能解释“系统为什么这样选”

只要这四点持续改善，就说明方向是对的。
