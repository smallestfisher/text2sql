# 真实场景联调与覆盖手册

## 1. 结论先行

当前 LLM-first 架构不能承诺一次性覆盖全部实际使用场景。

它真正能提供的是一条可调、可复跑、可沉淀的闭环：

- 用真实表结构和业务知识驱动 LLM 生成 SQL
- 用 Query Plan、权限、validator 和执行器限制风险
- 用 trace、workspace、replay、eval case 复现问题
- 用真实失败问题持续扩充覆盖率

所以，接入真实数据后的重点不是“列举完所有规则”，而是建立稳定的联调闭环：

`真实问题 -> 定位失败层 -> 修上下文或治理 -> replay 验证 -> 沉淀 case/example`

## 2. 接入真实数据前检查

### 2.1 数据库

- 使用只读账号连接业务库
- 确认 `BUSINESS_DATABASE_URL` 指向真实或脱敏测试库
- 确认 `RUNTIME_DATABASE_URL` 与业务库分离
- 先限制测试用户权限，避免大范围查询
- 确认 runtime 库已经升级到当前表结构版本

### 2.2 元数据

核对 `tables.json`：

- 表名和真实库一致
- 字段名大小写和真实库一致
- 时间字段、版本字段、主业务键写清楚
- 横表字段含义写清楚
- 常见 join 字段和方向写清楚

核对 `business_knowledge.json`：

- 是否按 `domain / tables / keywords / notes` 组织
- 每条知识块是否足够短，且只表达一个稳定业务点
- 是否显式包含相关表名、字段名或指标名，便于 prompt 选择命中
- 是否避免把低频一次性问题写进全局说明

核对 `readme.txt`：

- 是否仍然只是 fallback 说明
- 是否没有重新膨胀成大段业务规则文本

## 3. 单个真实问题怎么调

### 3.1 先跑完整链路

优先在前端工作台或接口跑：

```text
POST /api/chat/query
```

如果问题涉及历史会话恢复、消息和详情不一致，再直接看：

```text
GET /api/chat/sessions/{session_id}/workspace
```

核心关注字段：

- `classification`
- `query_plan`
- `sql`
- `plan_validation`
- `sql_validation`
- `execution`
- `answer`
- `latest_response`
- `trace_artifacts`

### 3.2 看状态分流

- `answer.status = invalid`：优先看是否被 relevance guard 判成了非业务查询
- `classification.question_type = clarification_needed`：优先看时间、指标、版本或主体是否缺失
- `plan_validation.valid = false`：优先看 Query Plan 是否缺表、指标、过滤、时间或版本
- `sql_validation.valid = false`：优先看 LLM SQL 是否错表、错字段、缺权限或越界
- `execution.status = db_error`：优先看 SQL、字段大小写、真实库字段是否存在
- `execution.status = empty_result`：优先看过滤条件、时间、版本、权限范围和真实数据
- 执行成功但答案不对：优先看业务口径、目标月份、最新版本、聚合粒度、排序和 TopN

### 3.3 看 workspace

如果用户反馈是：

- “查看详情不对”
- “历史会话打开后右侧内容不对”
- “消息有了，但结果面板像没执行过”

先不要直接查前端组件，先看 `workspace` 接口返回的：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `trace_artifacts`

如果这里已经不一致，优先按后端恢复链路排查；如果这里一致，再查前端状态消费和 `activeTraceId` 切换。

### 3.4 看 trace

重点看这些步骤：

- `terminal_gate`
- `retrieve`
- `compile_plan`
- `build_sql_prompt`
- `validate_sql`
- `execute`
- `response_snapshot`

重点关注：

- 是否在 SQL 前被 `invalid` 或 `clarification_needed` 短路
- 是否召回了相关 example / knowledge
- `build_sql_prompt.metadata.context_summary` 里本次选了哪些真实表、知识长度、是否用了 few-shot
- validator 是误拦还是正确拦
- `response_snapshot` 是否已经可恢复

### 3.5 看管理端日志

管理端最近 query logs 会展示 prompt 上下文摘要，例如：

- 选中的真实表或来源
- 业务说明字符数
- 是否使用 few-shot
- 回答状态
- 返回行数

如果同一问题多次执行结果不稳定，优先对比 prompt context summary 是否变化。上下文在变，先查 Query Plan、检索和知识切片；上下文没变但 SQL 在变，再看 LLM 输出、validator 和 repair。

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

### 4.2 业务口径问题

症状：

- SQL 能跑，但结果和业务预期不一致
- 需求月份、最新版本、TopN、分组口径错
- 计划/实际/库存指标混淆

修复：

- 优先改 `business_knowledge.json`
- 把规则拆成独立知识块
- 高频问题补 few-shot

### 4.3 Query Plan 问题

症状：

- 主题域错
- 没有选到真实表
- 维度、过滤、版本、排序缺失
- 追问继承错

修复：

- 补语义别名、指标别名、实体别名
- 补分类和 follow-up 样本
- 调整 Query Plan 编译/校验，而不是写 SQL 模板

### 4.4 SQL 生成问题

症状：

- LLM SQL 使用了错误字段
- 横表展开错
- 聚合或 group by 错
- CTE 逻辑不对

修复：

- 优先补 `tables.json` 和 `business_knowledge.json`
- 高频稳定问题补 few-shot
- repair 失败时优化 validator 错误信息

### 4.5 SQL 校验问题

症状：

- 正确 SQL 被拦截
- 明显错误 SQL 被放过
- 权限过滤误判

修复：

- 改 `sql_validator.py`
- 改 `sql_ast_validator.py`
- 让错误信息更具体，方便 LLM repair

### 4.6 执行问题

症状：

- 超时
- 空结果
- 慢查询
- 数据被截断

修复：

- 补时间范围和默认 limit
- 优化过滤条件
- 评估索引、物化表或受控数据库视图

## 5. 真实场景覆盖怎么判断

不要用“能答一个问题”来判断覆盖率。建议按场景矩阵判断。

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

每个失败问题都建议按下面处理：

1. 记录原问题、时间、用户和 `trace_id`
2. 保存生成 SQL、执行结果和人工预期
3. 判断失败层：schema、业务口径、Query Plan、SQL、validator、执行、权限
4. 修最小必要上下文或治理代码
5. 用 replay 复跑原问题
6. 对比 replay diff，重点看分类、Query Plan、SQL、执行状态和 prompt 上下文是否变化
7. 如果是代表性问题，调用 `materialize-case`
8. 如果是高频问法，可再沉淀为 example 或 few-shot

few-shot 只放高频、代表性、可复用的问题，不要把所有低频失败题都写进去。

## 7. 覆盖边界

这套架构天然不能自动覆盖：

- `tables.json` 没描述的表和字段
- `business_knowledge.json` 没说明且字段名也无法推断的业务口径
- 需要复杂人工判断、审批或线下确认的问题
- 需要跨系统、跨库、外部文件的查询
- 写库、改数据、创建对象的操作
- 超出权限范围的数据查询

遇到这些问题，系统应该澄清、拒绝、提示缺少上下文，或只返回安全可执行的部分。

## 8. 推荐验收目标

首轮真实联调不要追求 100% 覆盖。建议目标：

- 高频 Top 20 问题执行正确率达到可接受水平
- 每个主域至少有 10 条 eval case
- 每个主域至少有 3 条多轮追问 case
- SQL validation 能拦截明显危险 SQL
- execution failure 能被 trace 定位
- 新失败能在 1 到 2 次 replay 内复现

等高频场景稳定后，再逐步扩到长尾问题。
