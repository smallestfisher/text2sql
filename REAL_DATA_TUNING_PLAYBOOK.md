# 真实数据调优手册

## 1. 目标

本文用于指导 LLM-first Text2SQL 后端接入真实数据和真实问法。当前调优重点不是继续堆本地规则或 semantic view，而是让 LLM 基于真实 schema、真实业务说明和真实示例生成正确 SQL，再由 validator 兜住安全边界。

## 2. 最低准备

进入联调前至少准备：

- 可访问的只读业务库账号
- 最新 `tables.json`
- 最新 `readme.txt`
- 每个主业务域 5 到 10 条真实高频问题
- 每条问题的人工预期，包括指标、维度、过滤、排序、TopN、时间和版本口径
- 重点关注的 2 到 3 条主线，例如需求、库存、计划/实际

## 3. 联调顺序

### 3.1 校准 schema

先核对 `tables.json`：

- 表名是否和真实库一致
- 字段名、类型、含义是否准确
- 时间字段、版本字段、主实体字段是否说明清楚
- 横表字段含义是否写清楚

再核对 `readme.txt`：

- 表间业务关系是否准确
- 指标口径是否明确
- `p_demand` / `v_demand` 这类横表的月份映射是否明确
- 最新版本、目标月份、TopN 等常见问法是否有说明

### 3.2 收集真实问题

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

这些样本优先进入 eval case 或 few-shot，而不是写成代码分支。

### 3.2.1 控制 prompt 增长

补业务说明时不要把所有内容写成长段落。建议：

- 每条业务规则尽量独立成短段落
- 段落里明确写出相关表名、字段名或指标名，方便 PromptBuilder 命中
- 高频但只适用于单域的问题，写到对应域的短说明或 few-shot
- 不要把低频一次性问题放进全局 prompt
- 如果说明文件持续变大，优先拆成可检索知识块

### 3.3 跑 LLM-first SQL

按以下链路观察：

1. `POST /api/query/plan`
2. `POST /api/query/sql`
3. `POST /api/query/execute`
4. `POST /api/chat/query`

重点看：

- Query Plan 是否给了合理约束
- LLM SQL 是否只用了真实表和字段
- validator 是误拦截还是正确拦截
- repair 后 SQL 是否更接近真实口径
- 执行结果是否符合人工预期

### 3.4 沉淀修复

修复顺序：

1. 字段或表含义不清，改 `tables.json`
2. 业务口径不清，改 `readme.txt`
3. 高频问法不稳定，补 few-shot 或 eval case
4. prompt 通用指令不足，改 `PromptBuilder`
5. SQL 被误拦或漏拦，改 validator
6. 只有安全或分类需要时，才加局部规则

不要把真实问题直接写成 `if question contains ... then SQL ...`，这会把工程重新拉回维护不完的规则系统。

## 4. 需求横表专项

对 `p_demand` / `v_demand`，重点验证：

- `202604` 这类目标需求月份不是简单的 `MONTH = 202604`
- `MONTH` 表示版本月，目标月份可能对应 `REQUIREMENT_QTY`、`NEXT_REQUIREMENT`、`LAST_REQUIREMENT`、`MONTH4` 到 `MONTH7`
- “最新 4 版 p 版需求”需要先取最新 4 个版本
- “需求最多的 fgcode”需要按 `FGCODE` 聚合后排序

这类逻辑应通过 `readme.txt`、prompt 和 few-shot 让 LLM 生成 CTE，不要求真实数据库创建 `semantic_demand_unpivot_view`。

## 5. 验收标准

首轮真实联调建议达到：

- 主线问题能稳定生成可执行 SQL
- 结果和人工预期一致或差异可解释
- SQL validation 错误能指导 repair
- 执行失败可以通过 trace 定位到 schema、业务说明、prompt、validator 或数据问题
- 新增修复可以沉淀为 eval case，避免反复回归

## 6. 不建议

- 不建议先落库 semantic view 再验证 LLM 能力
- 不建议继续扩展本地 SQL 模板
- 不建议没有真实样本就接大规模向量库
- 不建议只看 SQL 能不能跑，不看业务结果是否正确
- 不建议把每个失败问题都改成代码规则
