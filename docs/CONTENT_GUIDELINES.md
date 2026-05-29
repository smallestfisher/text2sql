# 样例与知识库编写规范

这份文档约束两类会直接影响 SQL 生成的内容资产：

- `examples/nl2sql_examples.template.json`
- `semantic/business_knowledge.json`

目标是让样例和业务知识都能被检索、进入 prompt，并且长期可维护。不要把它们写成一次性补丁、临时解释或和真实表结构脱节的经验描述。

## 1. 先判断应该改哪里

### 1.1 写入样例

当你已经有一条人工确认正确的用户问题和 Oracle SQL，并且希望模型学习这类问题的 SQL 组织方式时，写入 `examples/nl2sql_examples.template.json`。

适合写样例的情况：

- SQL 结构本身重要，例如横表展开、先聚合再 join、topN、版本筛选、库龄桶计算。
- 单靠业务规则描述容易误写，必须给模型一个可仿照的完整 SQL。
- 真实 trace 已经确认正确，可以沉淀成 few-shot。
- 某类问题经常出现，并且有稳定的表、字段、过滤和计算方式。

不适合写样例的情况：

- 只是补充某个字段含义、同义词或表选择规则。
- SQL 还没有被执行或人工验过。
- 问题只覆盖一次性口径，后续不希望模型泛化。
- 需要更新的是表结构、字段说明、指标定义或维度边界；这类内容优先改 `semantic/tables.json` 或 `semantic/domain_config/*`。

### 1.2 写入知识库

当你要表达可复用的业务规则，而不是给出完整 SQL 模板时，写入 `semantic/business_knowledge.json`。

适合写知识库的情况：

- 某个用户词应该稳定映射到某张表、某个字段或某个过滤条件。
- 某个业务口径需要解释，例如 Gap、达成率、TTL、最新版本、库龄桶。
- 需要告诉模型不要做某类错误行为，例如不要明细 join 后再 SUM。
- 同一规则会影响多个问题或多个 SQL 形态。

不适合写知识库的情况：

- 规则已经属于指标、维度、实体或字段语义定义，应放到 `semantic/domain_config/*`。
- 规则只有在一个完整 SQL 结构里才说得清楚，应写样例。
- 内容只是排查记录、历史原因或人的备忘；这类信息不会稳定帮助 SQL 生成。

### 1.3 写入 eval case

如果目标是防止某个真实问题回归，写入 `eval/evaluation_cases.json`，不要只写样例或知识库。样例和知识库负责影响生成，eval case 负责验收行为。

## 2. 样例编写规范

`nl2sql_examples.template.json` 是 JSON 数组。后端只要求 `question` 和 `sql` 必填，但推荐每条样例都显式写清这些字段：

```json
{
  "id": "plan_actual_array_approved_vs_actual_input_gap_rate_001",
  "question": "2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
  "sql": "WITH approved_input AS (...) SELECT ... FETCH FIRST 200 ROWS ONLY;",
  "subject_domain": "plan_actual",
  "metrics": ["approved_input_qty", "actual_input_qty", "input_gap_qty", "input_achievement_rate"],
  "dimensions": ["biz_month", "factory"],
  "tags": ["monthly_plan_approved", "production_actuals", "approved_vs_actual", "aggregate_then_join"],
  "result_shape": "biz_month,factory",
  "notes": "审批侧和实际侧必须先分别按月份、工厂聚合，再 join 聚合结果。"
}
```

### 2.1 字段要求

- `id`：稳定、唯一、可读，使用小写蛇形命名。建议包含业务域、关键场景、关键过滤或口径，例如 `demand_latest_p_recent6m_ttl_001`。
- `question`：写真实用户会问的自然语言，不要写成开发者说明或 SQL 需求说明。
- `sql`：必须是单条 Oracle 只读 `SELECT` 或 `WITH ... SELECT`。不要写 DDL、DML、多语句、临时表创建或过程调用。
- `subject_domain`：使用已存在的业务域，例如 `demand`、`inventory`、`plan_actual`、`sales_financial`。
- `metrics`：使用语义配置中已有或准备维护的指标名，不要临时造一个只在样例里出现的名称。
- `dimensions`：使用逻辑维度名，例如 `biz_month`、`factory`、`common_categories`；不要把 CTE 里的临时别名当成长期维度。
- `tags`：写检索友好的短标签，优先放表名、关键业务词、SQL 形态和风险点，例如 `horizontal_table`、`latest_version`、`aggregate_then_join`。
- `result_shape`：当输出形态对模型有约束时填写，例如 `metric_only`、`single_row_age_bucket_distribution`、`biz_month,factory`。
- `notes`：只写会影响 SQL 生成的短提醒，不写排查历史、长篇解释或无关背景。

### 2.2 SQL 要求

- 使用真实物理表和物理字段，不写伪列、逻辑字段或不存在的别名。
- 业务库固定为 Oracle，SQL 语法按 Oracle 写，例如 `FETCH FIRST n ROWS ONLY`、`NVL`、`TO_DATE`、`ADD_MONTHS`。
- 默认加结果行限制。常规明细或聚合结果使用 `FETCH FIRST 200 ROWS ONLY`，topN 问题按问题要求取对应数量。
- 时间过滤必须匹配真实存储格式。`YYYYMMDD` 字符串日字段用整月范围或 `SUBSTR(..., 1, 6)`；`YYYYMM` 月字段直接按月比较。
- 对比类问题先在两侧事实表内按对齐粒度聚合，再 join 聚合结果，避免明细 join 放大量。
- 比率类计算必须处理除零，例如 `CASE WHEN denominator = 0 THEN NULL ELSE numerator / denominator END`。
- 聚合数值时按业务需要使用 `NVL`，但不要用 `NVL` 掩盖本应暴露的口径缺失。
- 不使用 `SELECT *`。输出列应该和问题、指标、维度一致。
- 不把样例写成过度宽泛的模板。样例应该回答一个具体问题，同时展示可泛化的 SQL 形态。

### 2.3 样例数量与覆盖

- 每个样例应该覆盖一个明确问题形态，不要把多个无关场景塞进同一条样例。
- 同一场景已有高质量样例时，优先更新原样例的 SQL、tags 或 notes，不要重复追加近似样例。
- 新增样例前先确认它能被检索命中：`question`、`tags`、`metrics`、`dimensions` 和 `notes` 中至少有几项能和真实用户问法对齐。
- 样例不能代替知识库。若某条样例里的规则会影响很多问题，应同步沉淀到 `business_knowledge`。

## 3. 知识库编写规范

`business_knowledge.json` 是一个 JSON 对象，核心字段是 `entries` 数组。每条 entry 表示一组可复用业务规则。

推荐格式：

```json
{
  "id": "plan_actual_approved_actual_gap_rate",
  "domains": ["plan_actual"],
  "tables": ["monthly_plan_approved", "production_actuals"],
  "keywords": ["审批版", "实际", "投入", "Gap", "达成率"],
  "notes": [
    "问句同时出现审批版、实际、Gap、达成率，且语义是投入时，应同时查询审批投入和实际投入，再计算差异与达成率。",
    "审批侧来源是 monthly_plan_approved；实际侧来源是 production_actuals，且实际投入必须过滤 act_type=投入。",
    "必须先分别在审批侧 CTE 和实际侧 CTE 内按对齐粒度聚合，再把两个已聚合 CTE join。"
  ]
}
```

### 3.1 字段要求

- `id`：稳定、唯一、可读，使用小写蛇形命名。不要按日期或个人命名。
- `domains`：只填这条规则实际适用的业务域。跨域规则可以写多个域，但不要为了提高检索概率乱填。
- `tables`：只填规则直接涉及的事实表、维表或桥接表。不要把可能无关的表放进去。
- `keywords`：同时覆盖用户词、业务词和关键物理字段，例如 `最新P版`、`PM_VERSION`、`库龄分布`、`ONE_AGE_panel_qty`。
- `notes`：每条 note 只表达一个可执行规则。优先写“何时适用、用什么表/字段、怎么计算、禁止什么错误”。

### 3.2 note 写法

- 写成面向 SQL 生成的指令，而不是业务背景介绍。
- 一条 note 尽量保持短句；复杂规则拆成多条。
- 同时写清正向规则和关键禁忌。例如“必须先聚合再 join”，以及“禁止明细 join 后再 SUM”。
- 公式必须明确方向。例如 `Gap = 实际投入 - 审批投入`，不要只写“计算 Gap”。
- 口径有条件分支时写清触发条件。例如“MDL 默认 panel，其他工厂默认 glass”。
- 时间、版本、单位、枚举值要贴近真实物理字段和存储格式。
- 不写互相冲突的规则。发现冲突时，先合并或修正旧 entry。

### 3.3 粒度控制

- 一个 entry 聚焦一个业务主题，例如“需求横表月份展开”或“OMS 库龄桶映射”。
- 不要把整个业务域的所有知识塞进一个 entry；过大的 entry 会降低检索和 prompt 压缩质量。
- 每条 entry 推荐 3 到 8 条 notes。超过这个范围时，通常应该拆分。
- 过细的一次性知识不要写入库；只有可复用、可解释、可验证的规则才进入知识库。

## 4. 维护流程

1. 从真实失败问题开始，看 trace、retrieval、prompt 和 SQL audit，确认失败原因。
2. 按第 1 节判断修改目标：样例、知识库、语义配置、join pattern 或 eval case。
3. 编辑 JSON 时保持格式稳定，不做无关排序或大面积重排。
4. 修改后执行语义配置检查：

```bash
python3 backend/domain_config_lint.py
```

5. 如果改了样例或知识库，重启服务或调用 `POST /api/admin/metadata/reload`，让容器和 retrieval corpus 刷新。
6. 用原始问题重新跑一次，确认 retrieval 命中、SQL prompt 使用了新内容，并且生成 SQL 通过 validator 和执行。
7. 对真实高价值问题，补充或更新 eval case，防止后续回归。

## 5. 提交前检查

- JSON 合法，结构仍符合当前加载器要求。
- 样例 SQL 是 Oracle 单条只读查询，并且只使用已知表字段。
- `id`、`tags`、`keywords` 可读且稳定。
- 样例和知识库没有互相矛盾。
- 新内容能解释真实问题，而不是只为了碰巧影响一次 prompt。
- 如果新增了业务规则，已有相邻 entry 没有被遗忘或冲突。
- 对影响准确率的变更，至少用原始问题完成一次 replay 或手工验证。
