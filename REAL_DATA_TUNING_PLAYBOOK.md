# 真实数据调优手册

## 1. 目标

本文用于指导当前 LLM-first Text2SQL 工程接入真实数据和真实问法。调优重点不是继续堆本地规则或数据库预建分析对象，而是让 LLM 基于真实 schema、业务知识和高质量样本生成正确 SQL，再由 validator 兜住安全边界。

## 2. 最低准备

进入联调前至少准备：

- 可访问的只读业务库账号
- 最新 `tables.json`
- 最新 `business_knowledge.json`
- 每个主业务域 5 到 10 条真实高频问题
- 每条问题的人工预期，包括指标、维度、过滤、排序、TopN、时间和版本口径
- 重点关注的 2 到 3 条主线，例如需求、库存、计划/实际

## 3. 联调顺序

### 3.1 先校准 schema

先核对 `tables.json`：

- 表名是否和真实库一致
- 字段名、类型、含义是否准确
- 时间字段、版本字段、主实体字段是否说明清楚
- 横表字段含义是否写清楚
- 常见 join 字段和方向是否明确

再核对 `business_knowledge.json`：

- 是否已经按 `domain / tables / keywords / notes` 组织
- 表间业务关系是否准确
- 指标口径是否明确
- `p_demand` / `v_demand` 这类横表的月份映射是否明确
- 最新版本、目标月份、TopN 等常见问法是否有说明

### 3.2 再收集真实问题

每条问题建议记录：

- `question`
- `business_domain`
- `expected_tables`
- `expected_metrics`
- `expected_dimensions`
- `expected_filters`
- `expected_sort`
- `expected_limit`
- `expected_result_note`

这些样本优先进入 eval case、replay 或 few-shot，而不是写成代码分支。

补充约束：

- `examples/nl2sql_examples.template.json` 初始应保持为空，避免假设样例污染在线检索。
- 只有真实问题、真实 trace、且 SQL 与业务结果都人工确认后，才通过 `materialize-example` 增加样例。
- `eval/evaluation_cases.json` 也只保留真实问题、真实 trace 或真实 replay 沉淀出的 case，不保留假设回归样本。

### 3.3 补业务知识时控制 prompt 增长

当前 prompt 主要是中文自然语言指令，但表名、字段名要保持真实英文命名。补业务知识时建议：

- 每条业务规则优先写成 `business_knowledge.json` 中的独立知识块
- 知识块里明确写出相关表名、字段名或指标名，方便 PromptBuilder 命中
- 高频但只适用于单域的问题，写到对应域知识块或 few-shot
- 不要把低频一次性问题放进全局 prompt

## 4. 跑联调链路

建议按下面顺序观察：

1. `POST /api/query/plan`
2. `POST /api/query/sql`
3. `POST /api/query/execute`
4. `POST /api/chat/query`
5. `GET /api/chat/sessions/{session_id}/workspace`
6. `POST /api/admin/runtime/query-logs/{trace_id}/replay`

重点看：

- Query Plan 是否给了合理约束
- LLM SQL 是否只用了真实表和字段
- prompt context summary 是否稳定
- validator 是误拦截还是正确拦截
- repair 后 SQL 是否更接近真实口径
- 执行结果是否符合人工预期

## 5. 修复顺序

建议固定按这个顺序修：

1. 字段或表含义不清，改 `tables.json`
2. 业务口径不清，优先改 `business_knowledge.json`
3. 高频问法不稳定，补 few-shot、example 或 eval case
4. prompt 通用指令不足，改 `PromptBuilder`
5. SQL 被误拦或漏拦，改 validator
6. 只有安全、权限或分类需要时，才加局部规则

不要把真实问题直接写成 `if question contains ... then SQL ...`。这会把工程重新拉回维护不完的规则系统。

## 6. demand 横表专项

对 `p_demand` / `v_demand`，联调时必须重点验证：

- `202604` 这类目标需求月份不是简单的 `MONTH = 202604`
- `MONTH` 表示起始月份
- `REQUIREMENT_QTY`、`NEXT_REQUIREMENT`、`LAST_REQUIREMENT`、`MONTH4` 到 `MONTH7` 分别映射不同偏移月份
- “最新 N 版”需要先取最新 N 个版本
- “需求最多的 fgcode”需要按 `FGCODE` 聚合后排序
- 如果展开成 CTE，`PM_VERSION` 要在每个 `UNION ALL` 分支里显式投影出来

这类逻辑应通过 `business_knowledge.json`、PromptBuilder 的 demand 指令和 few-shot 让 LLM 生成 CTE，不要求真实数据库额外创建展开对象。

## 7. 样本沉淀方式

对真实联调里有代表性的失败问题，建议按下面处理：

1. 先复现并保存 `trace_id`
2. 在管理端或 API 对该 `trace_id` 执行 replay
3. 若有价值，调用 `materialize-case`
4. 若属于高频标准问法，可再调用 `materialize-example`
5. 修复后再次 replay，确认变化稳定

这样比直接手写文档样例或临时分支更容易回归。

## 8. 首轮验收标准

首轮真实联调建议达到：

- 主线问题能稳定生成可执行 SQL
- 结果和人工预期一致或差异可解释
- SQL validation 错误能指导 repair
- 执行失败可以通过 trace 或 workspace 定位到 schema、业务知识、prompt、validator 或数据问题
- 新增修复可以沉淀成 eval case，避免反复回归

## 9. 不建议

- 不建议先落库额外分析对象再验证 LLM 能力
- 不建议继续扩展本地 SQL 模板
- 不建议没有真实样本就接大规模向量库
- 不建议只看 SQL 能不能跑，不看业务结果是否正确
- 不建议把每个失败问题都改成代码规则
