# 调试与联调手册

这份文档只回答一个问题：

**当一条真实业务问题答错时，应该先看哪里、怎么分层定位、怎么把修复沉淀成长期资产。**

它描述的是当前代码已经实现的调试路径，不记录历史迁移过程。

---

## 1. 先记住三条原则

### 1.1 不要先改规则

遇到准确率问题，优先修这些地方：

1. `semantic/tables.json`
2. `semantic/business_knowledge.json`
3. `examples/nl2sql_examples.template.json`
4. `semantic/join_patterns.json`
5. PromptBuilder / retrieval / validator

不要一上来就加本地 SQL 模板或单题规则分支。

### 1.2 Trace 不是整段对话

当前系统里：

- `session`：整段会话容器
- `message`：单条消息
- `trace`：单次查询轮次的完整执行记录
- `query_log`：按 `trace_id` 落库的结构化摘要
- `workspace`：前端恢复会话的聚合视图

所以一段会话里通常会有多个 `trace`。

### 1.3 工作台主入口不是拆分接口

真实工作台默认走：

- `POST /api/chat/query/stream`
- `GET /api/chat/sessions/{session_id}/workspace`

`/api/query/*` 这一组接口主要用于单步调试，不是前端真实主链路。

---

## 2. 5 分钟排查清单

拿到一个真实问题，先按这个顺序走：

1. 在工作台或 `POST /api/chat/query/stream` 复现
2. 记下 `session_id` 和 `trace_id`
3. 打开 `GET /api/chat/sessions/{session_id}/workspace`
4. 先看 6 个字段：
   - `classification.question_type`
   - `classification.subject_domain`
   - `plan_validation.valid`
   - `sql_validation.valid`
   - `execution.status`
   - `answer.status`
5. 再看：
   - `GET /api/chat/traces/{trace_id}`
   - `GET /api/chat/traces/{trace_id}/retrieval`
   - `GET /api/chat/traces/{trace_id}/sql-audit`
6. 修完后 replay 原 `trace_id`

最短分流判断：

- 没听懂问题：先查 intent / classification / field semantics
- Query Plan 错：先查 planner / compiler / semantic config
- SQL 错：先查 retrieval / prompt / validator
- SQL 对但结果错：先查真实数据、时间、版本、口径
- 左侧消息对、右侧详情不对：先查 `workspace` 恢复链路

---

## 3. 推荐入口

### 3.1 用户侧入口

最常用的是：

- `POST /api/chat/query/stream`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`

### 3.2 管理台入口

管理员常用的是：

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`

### 3.3 拆分调试入口

需要单步断层定位时再用：

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`

---

## 4. 先看 workspace

`workspace` 是当前前端恢复会话的主入口，所以很多问题先看它最省时间。

`GET /api/chat/sessions/{session_id}/workspace` 当前会带回：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- `trace_artifacts`

先看它的原因很简单：

- 左边消息流来自这里
- 右侧详情面板也依赖这里
- 历史恢复错位时，通常在这里就能看出来

常见判断：

- `messages` 对，但 `latest_response` 不对：优先查 response restore / workspace 聚合
- `latest_trace` 和消息上挂的 `trace_id` 对不上：优先查 query log / trace artifact 拼接
- `trace_artifacts` 缺某一轮：优先查该轮 `trace / query_log / sql_audit` 是否有缺口

---

## 5. 一条查询的真实分层

当前主链路可以按下面几层排查：

1. intent / classification
2. retrieval
3. query plan
4. sql generation
5. sql validation / repair
6. execution
7. answer build
8. workspace / response restore

下面按层说明。

---

## 6. 各层怎么查

### 6.1 Intent / Classification

先看：

- `query_intent.matched_metrics`
- `query_intent.matched_entities`
- `query_intent.filters`
- `query_intent.time_context`
- `query_intent.version_context`
- `query_intent.subject_domain`
- `classification.question_type`
- `classification.reason_code`
- `classification.inherit_context`
- `classification.context_delta`

再看 trace 里的：

- `parse_intent`
- `llm_intent`
- `normalized_intent`
- `classify_question.metadata.classifier_debug`

典型症状：

- 明明是库存，识别成计划/实际
- 时间没提出来
- 版本没提出来
- 追问被当成新问题
- 信息足够却一直 `clarification_needed`

优先修：

- `semantic/domain_config/base/field_semantics.json`
- `semantic/domain_config/base/domain_inference.json`
- `semantic/domain_config/base/prompt_assets.json`
- `QueryIntentParser`
- `IntentService`
- `IntentNormalizer`
- `QuestionClassifier`

### 6.2 Retrieval

先看：

- `retrieval_terms`
- `retrieval_channels`
- `hit_count_by_source`
- `hit_count_by_channel`
- top hits 的 `source_type / source_id / score / matched_features`

当前 retrieval 来源包括：

- `example`
- `metric`
- `knowledge`
- `join_pattern`
- `vector`

典型症状：

- 域大致对了，但关键 example 没进 prompt
- 业务知识明明存在，但没命中
- join pattern 存在，却没进 top hits
- 同题多次执行，上下文抖动大

优先修：

- `semantic/tables.json`
- `semantic/business_knowledge.json`
- `examples/nl2sql_examples.template.json`
- `semantic/join_patterns.json`
- `RetrievalService`
- `PromptBuilder`

如果怀疑是向量通道问题，再看：

- `GET /api/admin/runtime/status`
  - `vector_retrieval`
  - `retrieval_corpus`

重点看：

- `vector_enabled`
- `vector_ready`
- `vector_sync.error`
- `persisted_document_count`
- `rebuilt_document_count`

### 6.3 Query Plan

先看：

- `query_plan.subject_domain`
- `query_plan.tables`
- `query_plan.metrics`
- `query_plan.dimensions`
- `query_plan.filters`
- `query_plan.time_context`
- `query_plan.version_context`
- `query_plan.need_clarification`

当前 plan 相关服务有三层：

- `QueryPlanner`
- `QueryPlanCompiler`
- `QueryPlanValidator`

典型症状：

- 表选错
- 没表
- support table 没补进来
- domain 是 `unknown`
- clarification 条件触发得不对

优先修：

- `semantic/domain_config/*`
- `QueryPlanner`
- `QueryPlanCompiler`
- `QueryPlanValidator`

### 6.4 SQL 生成

先看：

- `sql`
- trace 里 `sql_generation`
- `build_sql_prompt` 的上下文摘要

典型症状：

- SQL 用了不存在的表或字段
- 逻辑字段名直接进了 SQL
- 维度和过滤条件没正确落进去
- 没把命中的 example / business note / join pattern 用起来

优先修：

- `PromptBuilder`
- `semantic/tables.json`
- `semantic/business_knowledge.json`
- `examples`
- `join_patterns`

### 6.5 SQL 校验 / Repair

先看：

- `sql_validation.valid`
- `sql_validation.errors`
- `sql_validation.warnings`
- `sql_validation.risk_flags`

典型症状：

- 生成 SQL 基本对，但被 validator 拦下
- 该拦没拦
- repair 后结构变坏

优先修：

- `SqlValidator`
- `SqlAstValidator`
- Query Plan shape contract

### 6.6 Execution

先看：

- `execution.status`
- `row_count`
- `columns`
- `rows`
- `elapsed_ms`
- `errors`

典型症状：

- `db_error`
- `not_configured`
- `empty_result`
- 结果能出但业务口径不对

优先排查：

- 数据库连接和权限
- 真实表/字段是否和 `semantic/tables.json` 一致
- 时间/版本/过滤条件是否带偏
- 真实数据本身是否为空

### 6.7 Workspace / Response Restore

如果 SQL 和执行都对，但工作台显示不对，优先看：

- `workspace.latest_response`
- `workspace.trace_artifacts`
- `trace`
- `query_log`
- `sql_audit`

这层问题通常不是 SQL 问题，而是恢复链路问题。

---

## 7. 管理台最短排查路径

如果你是管理员，建议固定按这个顺序：

1. 在工作台复现，拿到 `session_id`、`trace_id`
2. 先看工作台右侧结果卡和详情
3. 打开 `GET /api/admin/runtime/status`
   - 确认 DB、LLM、vector channel 都健康
4. 打开 `GET /api/admin/runtime/query-logs?limit=...`
5. 看：
   - `GET /api/admin/runtime/query-logs/{trace_id}`
   - `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
   - `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
6. 如果怀疑 prompt 抖动、上下文漂移或修复效果不稳，执行：
   - `POST /api/admin/runtime/query-logs/{trace_id}/replay`
7. 如果这是高价值真实问题，再决定是否沉淀：
   - `materialize-case`
   - `materialize-example`

---

## 8. 什么时候用 replay、materialize、eval

### 8.1 Replay

适用：

- 想确认修复是否真的生效
- 想排除“这次线上状态和上次不一样”的偶然因素
- 想验证 session/context 对结果的影响

优先入口：

- `POST /api/admin/runtime/query-logs/{trace_id}/replay`

### 8.2 Materialize Case

适用：

- 这是一条值得长期回归的真实失败样本
- 你希望后面能批量 eval 回归

入口：

- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`

### 8.3 Materialize Example

适用：

- 这是一条高频、标准、对 SQL 生成有直接参考价值的真实问法

入口：

- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`

当前行为要点：

- 写入 example 后会触发 retrieval corpus reload
- 受影响向量会增量重建并持久化到 runtime 库
- 通常不需要重启服务

### 8.4 Eval

适用：

- 你已经有一批真实 case，想批量看有没有回归

入口：

- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`

---

## 9. demand 横表专项

`p_demand / v_demand` 仍然是最容易出错的一类。

排这类问题时，先确认：

- 目标需求月份不是简单的 `MONTH = xxxx`
- `MONTH` 是起始月份，不是每个需求列的唯一月份
- `REQUIREMENT_QTY / NEXT_REQUIREMENT / LAST_REQUIREMENT / MONTH4~7` 是偏移列
- “最新 N 版”要先确定版本集合
- “需求最多的 fgcode”要先按 `FGCODE` 聚合再排序

这类问题优先通过下面几层修：

- `semantic/business_knowledge.json`
- demand 相关 example
- PromptBuilder 的上下文构造
- validator 的结构约束

不要把它写回本地固定 SQL 模板。

---

## 10. 样本沉淀原则

### 10.1 Example 只收真实样本

当前 example 应满足：

- 来源是真实问题或真实 trace
- SQL 和业务结果都人工确认过
- 能复用到一类问题，而不是单题补丁

### 10.2 先修语义还是先补 example

最简单判断：

- 如果系统没听懂“这句话在说哪个字段/指标/版本/时间”，先修语义配置
- 如果系统已经听懂，但 SQL 结构总是生成错，优先补 example / prompt / validator

### 10.3 不要长期保留误导性样本

即使样本来源真实，如果它会系统性误导同域其他问题，也不应该继续保留。

---

## 11. 最后一条原则

面对真实问题时：

- 先定位错在哪一层
- 再把修复沉淀到对的地方
- 修复后 replay 原 trace

不要把系统重新拉回“大量本地规则 + 本地 SQL 模板”的旧路径。
