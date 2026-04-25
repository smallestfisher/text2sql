# 真实场景联调与覆盖手册

## 1. 结论先行

当前 LLM-first 架构不能承诺一次性覆盖全部实际使用场景。

它能做的是：

- 用真实表结构、业务说明和少量相关示例驱动 LLM 生成 SQL
- 用 Query Plan、权限、SQL validator 和执行器限制风险
- 用 trace、replay、eval case 把失败问题沉淀成可复现样本
- 持续扩大高频真实场景覆盖率

因此，接入真实数据后的工作重点不是“写完一套规则覆盖所有问题”，而是建立调试闭环：真实问题 -> 定位失败层 -> 修上下文或治理 -> replay 验证 -> 沉淀 case。

## 2. 接入真实数据前检查

### 2.1 数据库

- 使用只读账号连接业务库
- 确认 `BUSINESS_DATABASE_URL` 指向真实或脱敏测试库
- 确认 `RUNTIME_DATABASE_URL` 与业务库分离，避免审计、会话、反馈表写到业务库
- 先限制测试用户权限，避免大范围查询

### 2.2 Schema

核对 `tables.json`：

- 表名和真实库一致
- 字段名大小写和真实库一致
- 时间字段、版本字段、主业务键写清楚
- 横表字段含义写清楚
- 常用 join 字段写清楚

核对 `readme.txt`：

- 每条业务规则尽量短段落
- 段落包含相关表名、字段名或指标名，方便 prompt 上下文选择器命中
- 不把低频一次性问题写进全局说明

## 3. 单个真实问题怎么调

### 3.1 先跑完整链路

优先在前端工作台或接口跑：

```bash
POST /api/chat/query
```

拿到：

- `trace_id`
- `classification`
- `query_plan`
- `sql`
- `plan_validation`
- `sql_validation`
- `execution`
- `answer`

### 3.2 看状态分流

- `classification.question_type = clarification_needed`：优先看问题是否缺时间、指标、版本或主体，不要先改 SQL。
- `plan_validation.valid = false`：优先看 Query Plan 是否缺真实表、指标、过滤、时间或版本。
- `sql_validation.valid = false`：优先看 LLM SQL 是否引用错表、错字段、缺 LIMIT、缺权限过滤或越界。
- `execution.status = db_error`：优先看 SQL 语法、字段大小写、真实库字段是否存在。
- `execution.status = empty_result`：优先看过滤条件、版本、时间、权限范围和真实数据是否存在。
- 执行成功但答案不对：优先看业务口径、聚合粒度、目标月份、版本逻辑、排序和 TopN。

### 3.3 看 trace

重点看这些步骤：

- `plan`：分类、语义解析、初始 Query Plan 是否合理
- `retrieve`：是否召回了相关 example / metric / knowledge
- `compile_plan`：最终 Query Plan 是否含真实表
- `build_sql_prompt`：LLM SQL 是否实际返回
- `build_sql_prompt.metadata.context_summary`：本次 prompt 选了哪些真实表、业务说明长度、是否使用 few-shot
- `validate_sql`：validator 是否误拦或漏拦
- `execute`：数据库执行状态和错误

如果 trace 不足以判断，下一步看 SQL audit 和 query log。

### 3.4 看管理端查询日志

管理端最近查询日志会展示 prompt 上下文摘要：

- 选中的真实表或数据源
- 业务说明片段字符数
- 是否使用 few-shot
- 回答状态和返回行数

如果同一问题多次执行结果不稳定，优先对比 prompt 上下文摘要是否变化。若上下文变化，先查 Query Plan、检索和业务说明切片；若上下文稳定但 SQL 变化，再看 LLM 输出、validator 和 repair。

## 4. 分层修复策略

### 4.1 Schema 问题

症状：

- SQL 字段不存在
- 表名错
- join 字段错
- 时间/版本字段选错

修复：

- 改 `tables.json`
- 补字段说明、主键、时间列、版本列和关系说明
- 重新跑同一问题

### 4.2 业务口径问题

症状：

- SQL 能跑，但结果和业务预期不一致
- 需求月份、最新版本、TopN、分组口径错
- 计划/实际/库存指标混淆

修复：

- 改 `readme.txt`
- 把业务规则写成短段落
- 段落中明确表名和字段名
- 高频问题补 few-shot

### 4.3 Query Plan 问题

症状：

- 主题域错
- 没有选到真实表
- 维度、过滤、版本、排序缺失
- 追问继承错

修复：

- 补语义别名、指标别名、实体别名
- 补分类或 follow-up 样本
- 调整 Query Plan 编译/校验，而不是写 SQL 模板

### 4.4 SQL 生成问题

症状：

- LLM SQL 使用了错误字段
- 横表展开错
- 聚合或 group by 错
- CTE 逻辑不对

修复：

- 优先补 `tables.json` 和 `readme.txt`
- 高频稳定问题补 few-shot
- repair 失败时优化 validator 错误信息
- 不要恢复本地 SQL 模板生成器

### 4.5 SQL 校验问题

症状：

- 正确 SQL 被拦截
- 明显错误 SQL 被放过
- 权限过滤误判

修复：

- 改 `sql_validator.py`
- 改 `sql_ast_validator.py`
- 增加具体错误信息，让 LLM repair 有可用反馈

### 4.6 执行问题

症状：

- 超时
- 空结果
- 慢查询
- 数据被截断

修复：

- 补时间范围和默认 limit
- 优化业务问题的过滤条件
- 对高频慢 SQL 单独评估索引、物化表或受控数据库视图

## 5. 如何判断是否覆盖真实场景

不要用“能回答一个问题”判断覆盖。建议按场景矩阵判断。

每个业务域至少覆盖：

- 单指标查询
- 按时间过滤
- 按版本过滤
- TopN 排序
- 分组聚合
- 多轮追问
- 空结果
- 权限受限用户
- 错误或模糊问题

需求域额外覆盖：

- p 版 / v 版
- 目标月份不是 `MONTH` 本身
- 最新 N 版
- 按 `FGCODE` 聚合
- 按客户或产品过滤

计划/实际域额外覆盖：

- 计划投入
- 实际投入
- 实际产出
- 计划 vs 实际对比
- 日/月粒度切换

库存域额外覆盖：

- 型号库存
- 工厂/库位过滤
- 产品属性关联
- 空库存和多型号歧义

## 6. 样本沉淀流程

每个失败问题都按下面处理：

1. 记录原问题、用户、时间、`trace_id`
2. 保存生成 SQL、执行结果和人工预期
3. 判断失败层：schema、业务口径、Query Plan、SQL、validator、执行、权限
4. 修最小必要上下文或治理代码
5. 用 replay 复跑原问题
6. 对比 replay diff，重点看分类、Query Plan、SQL、执行状态和 prompt 上下文是否变化
7. 如果是代表性问题，沉淀到 eval case
8. 如果是高频问法，补 few-shot

不要把低频失败问题全部写成 few-shot。few-shot 只放高频、代表性、可复用的问题。

## 7. 覆盖边界

这套架构不能自动覆盖：

- `tables.json` 没有描述的表和字段
- `readme.txt` 没有说明且字段名也无法推断的业务口径
- 需要复杂业务审批或人工判断的问题
- 需要跨系统、跨库、外部文件的数据
- 需要写库、改数据、创建对象的操作
- 超出权限范围的数据查询

遇到这些问题，系统应该澄清、拒绝、提示缺少上下文，或只返回可安全执行的部分。

## 8. 推荐验收目标

首轮真实联调不要追求 100% 覆盖。建议目标：

- 高频 Top 20 问题执行正确率达到可接受水平
- 每个主域至少有 10 条 eval case
- 每个主域至少有 3 条多轮追问 case
- SQL validation 能拦截明显危险 SQL
- execution failure 能被 trace 定位
- 新失败能在 1 到 2 次 replay 内复现

等高频场景稳定后，再扩大到长尾问题。
