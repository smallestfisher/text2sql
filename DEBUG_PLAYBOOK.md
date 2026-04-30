# 调试与联调手册

## 1. 这份文档解决什么问题

这份文档把原来分散的几类内容合在一起：

- 单题准确度调试
- 真实场景联调
- 真实数据接入后的调优顺序
- 样本沉淀和 replay / eval / example 的使用方式

如果你面对的是一个真实业务问题，优先看这份文档。

---

## 2. 先记住总原则

当前工程是 **LLM-first Text2SQL**。

补一条实践原则：

- 字段叫法优先沉淀到 `semantic/domain_config/base/field_semantics.json`
- 让 parser、LLM intent 和 normalizer 共用同一份字段语义目录
- 不要因为一个新说法就立刻往 `extractors` 里再堆一条单独规则

遇到问题时，优先按这个顺序处理：

1. 修 `tables.json`
2. 修 `business_knowledge.json`
3. 补 `example / eval case / replay`
4. 修 `PromptBuilder`
5. 修 `validator`
6. 最后才考虑加局部规则

不要一上来就把问题改成 Python 分支或本地 SQL 模板。

### 2.1 5分钟排查清单

如果你刚拿到一个真实问题，先不要深挖代码，先按下面顺序走一遍：

1. 先复现问题，记下 `session_id` 和 `trace_id`
2. 打开 `GET /api/chat/sessions/{session_id}/workspace`
3. 先看 5 个字段：
   `classification.question_type`
   `classification.subject_domain`
   `plan_validation.valid`
   `sql_validation.valid`
   `execution.status`
4. 用下面的分流法快速判断该改哪一层：
   `classification` 不对：先查语义解析 / 分类
   `plan_validation.valid = false`：先查 Query Plan
   `sql_validation.valid = false`：先查 SQL 生成 / validator
   `execution.status = db_error` 或 `not_configured`：先查执行环境 / 数据库连接
   `execution.status = empty_result`：先查时间、版本、过滤条件和真实数据
5. 如果是“没听懂问题”：
   先看时间、版本、指标、字段叫法有没有被识别
   字段叫法问题优先查 `semantic/domain_config/base/field_semantics.json`
6. 如果 `QueryPlan` 已经正确但 SQL 仍然错：
   优先修 `PromptBuilder`、真实 example、validator
   不要先加单题规则
7. 修完后必须 replay 原 `trace_id`
   再看 `workspace`、`query_plan`、`sql_validation`、`execution` 是否一起变对

最短判断原则：

- 没听懂：优先修语义配置 / `field_semantics`
- 听懂了但 SQL 生成错：优先修 prompt / example / validator
- 高频真实问法：补 example
- 不要直接把系统拉回“大量规则 + 本地 SQL 模板”

---

## 3. 一个问题进来后，先看哪条链路

一次 `POST /api/chat/query/stream` 的关键步骤大致是：

1. 读取当前 `session_state`
2. 提取指标、实体、时间、版本和 follow-up 信号
3. 先走 hard guard，再按需要触发 relevance guard，并在有 session 时进入 `LLM-primary + baseline accept/reject` 分类
4. 生成基础 `Query Plan`，并在 SQL 前做 compile / validate
5. 检索 example / knowledge / metric 辅助上下文
6. 构造 SQL prompt，把 `retrieved_examples`、`business_notes`、`join_patterns` 等证据带入
7. LLM 生成 SQL
8. validator 校验，必要时触发 repair
9. 执行 SQL
10. 组织回答，并落 trace / query log / sql audit / response snapshot

所以“答错了”通常属于下面几类之一：

- 语义解析错
- 分类错
- prompt 上下文不对
- Query Plan 错
- SQL 生成错
- validator 误拦或漏拦
- workspace / 历史恢复错位
- 执行环境或数据问题

---

## 4. 推荐调试入口

### 4.1 优先入口

推荐按这条路径排：

1. 在前端工作台或 `POST /api/chat/query/stream` 复现问题
2. 看 `GET /api/chat/sessions/{session_id}/workspace`
3. 看 `GET /api/chat/traces/{trace_id}`、`/sql-audit`、`/retrieval`
4. 历史问题优先走 `POST /api/admin/runtime/query-logs/{trace_id}/replay`
5. 有代表性的失败样本再物化为 `eval case` 或 `example`

### 4.2 为什么先看 `workspace`

因为它已经是前端主入口，一次性带回：

- 消息历史
- `session_state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `trace_artifacts`

如果左边消息对、右边详情不对，先看 `workspace` 返回是不是已经错位。

### 4.3 管理台最短操作路径

如果你是管理员，推荐直接按下面的前端操作走：

1. 在工作台复现问题，先拿到 `session_id`、`trace_id`
2. 留在工作台右侧详情先看一次结果卡
   重点确认 `question_type`、`subject_domain`、`plan_validation`、`sql_validation`、`execution.status`
3. 切到管理台，打开 `query logs`
4. 按时间或问题文案找到对应 `trace_id`
5. 先看这几个后端记录是否一致：
   `GET /api/admin/runtime/query-logs/{trace_id}`
   `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
   `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
6. 如果怀疑是历史上下文、prompt 抖动或修复前后不一致，直接点“复跑”
   对应后端接口是 `POST /api/admin/runtime/query-logs/{trace_id}/replay`
7. 如果复跑后确认这是高频真实问题，再考虑沉淀资产
   `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
   `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`

最短理解方式：

- 工作台负责复现和看当前会话恢复结果
- 管理台负责查 runtime 记录、看 replay、沉淀 case/example
- 如果工作台结果和管理台 runtime 记录不一致，优先排查 `workspace` 恢复链路

---

## 5. 先分流，不要先改 SQL

先看这几个字段：

- `classification.question_type`
- `classification.subject_domain`
- `answer.status`
- `plan_validation.valid`
- `sql_validation.valid`
- `execution.status`

### 5.1 常见分流方式

- `invalid`：先看 `classification.reason_code` 是 `invalid_smalltalk` 还是 `llm_out_of_scope`
- `chat`：先确认 `ENABLE_CHITCHAT_MODE=true`，并且当前用户带有 `chitchat` 角色；再回头看 `reason_code`
- `clarification_needed`：先看是否缺指标、时间、版本或主体
- `plan_validation.valid = false`：先看 Query Plan
- `sql_validation.valid = false`：先看 SQL 生成和 validator
- `execution.status = db_error`：先看 SQL、字段大小写、真实库对象
- `execution.status = empty_result`：先看过滤条件、时间、版本、继承上下文和底层数据

---

## 6. 分层调试方法

### 6.1 语义解析层

看：

- `query_intent.matched_metrics`
- `query_intent.matched_entities`
- `query_intent.filters`
- `query_intent.time_context`
- `query_intent.version_context`
- `query_intent.subject_domain`

典型症状：

- 问库存却识别成计划/实际
- 时间没提出来
- 版本没提出来
- 指标别名没识别
- 维度叫法没落到正确 canonical field

优先修：

- 指标别名、实体别名、domain inference
- `field_semantics` 里的字段别名是否覆盖了当前问法
- 时间/版本提取
- follow-up cue

### 6.2 分类层

看：

- `classification.question_type`
- `classification.inherit_context`
- `classification.context_delta`
- `classification.reason_code`
- trace 里的 `classify_question.metadata.classifier_debug`
  重点看 `decision_source`、`score_gap`、`llm_hint`、`baseline_classification`

典型症状：

- 明明是追问，却被当成新问题
- 明明切到新域，却还继承老上下文
- 信息足够，却总被判成 `clarification_needed`

优先修：

- `QuestionClassifier` 的 baseline 打分和 accept/reject 边界
- `semantic_runtime` 里的 follow-up / semantic diff 信号
- follow-up 线索
- 澄清文案
- relevance guard 的边界

### 6.3 检索和 prompt 上下文层

看：

- `retrieval_terms`
- `retrieval_channels`
- 命中的 example / knowledge / metric
- `build_sql_prompt.metadata.context_summary`

典型症状：

- 域对了，但关键知识块没进 prompt
- few-shot 命中很多，但都不相关
- 同一问题多次执行时，上下文抖动很大

优先修：

- `tables.json`
- `business_knowledge.json`
- example / few-shot
- PromptBuilder 的上下文选择规则

如果问题出在会话分类或 `context_delta` 漂移，还要额外检查：

- `semantic/domain_config/base/prompt_assets.json` 里的 classification few-shot 是否缺覆盖

### 6.4 Query Plan 层

看：

- `tables`
- `metrics`
- `dimensions`
- `filters`
- `sort`
- `limit`
- `time_context`
- `version_context`

典型症状：

- 表选错
- 维度缺失
- limit 不对
- 排序没落进去
- 过滤条件丢失

优先修：

- `semantic/domain_config.json` 清单入口
- `semantic/domain_config/` 分片配置
- Query Planner
- sanitize / validator 边界

### 6.5 SQL 生成与校验层

看：

- `sql`
- `sql_validation.errors`
- `sql_validation.warnings`
- `sql_validation.risk_flags`

典型症状：

- 用了不存在的表或字段
- 把逻辑字段名直接写进 SQL
- time / version filter 没落进去
- GROUP BY、ORDER BY、LIMIT 不一致

优先修：

- `tables.json`
- `business_knowledge.json`
- PromptBuilder
- SqlValidator

### 6.6 执行与结果层

看：

- `execution.status`
- `row_count`
- `columns`
- `rows`
- `elapsed_ms`

典型症状：

- SQL 能过校验但执行失败
- SQL 能执行但结果为空
- 结果能出，但业务口径不对

优先修：

- 真实库字段/大小写
- 真实数据情况
- SQL 自身过滤条件
- session_state 继承是否把条件带偏
- 业务口径说明

---

## 7. 真实场景联调顺序

### 7.1 接入真实数据前最低准备

至少准备：

- 可访问的只读业务库账号
- 最新 `tables.json`
- 最新 `business_knowledge.json`
- 每个主业务域 5 到 10 条真实高频问题
- 每条问题的人工预期：指标、维度、过滤、排序、TopN、时间、版本口径

### 7.2 先校准 schema

先核对 `tables.json`：

- 表名是否和真实库一致
- 字段名、含义是否准确
- 时间字段、版本字段、主实体字段是否写清楚
- 横表字段含义是否写清楚
- 常见 join 方向是否明确

再核对 `business_knowledge.json`：

- 是否按 `domain / tables / keywords / notes` 组织
- 表间关系是否准确
- 指标口径是否明确
- 横表月份映射是否明确
- 最新版本、目标月份、TopN 是否有说明

### 7.3 再跑完整链路

建议观察顺序：

1. `POST /api/query/classify`
2. `POST /api/query/plan`
3. `POST /api/query/plan/validate`
4. `POST /api/query/sql`
5. `POST /api/query/execute`
6. `POST /api/chat/query/stream`
7. `GET /api/chat/sessions/{session_id}/workspace`
8. `POST /api/admin/runtime/query-logs/{trace_id}/replay`

使用建议：

- `/api/query/*` 更适合单步调试某一层，不是前端真实主链路
- 工作台真实入口仍然是 `POST /api/chat/query/stream` + `workspace`

重点看：

- Query Plan 是否合理
- LLM SQL 是否只用了真实表和字段
- prompt context summary 是否稳定
- validator 是误拦还是正确拦
- repair 后 SQL 是否更接近真实口径

---

## 8. demand 横表专项

对 `p_demand` / `v_demand`，必须重点验证：

- `202604` 这种目标需求月份不是简单的 `MONTH = 202604`
- `MONTH` 是起始月份
- `REQUIREMENT_QTY` / `NEXT_REQUIREMENT` / `LAST_REQUIREMENT` / `MONTH4~7` 分别映射不同偏移月份
- “最新 N 版”要先取最新 N 个版本
- “需求最多的 fgcode”要按 `FGCODE` 聚合再排序
- 如果用了 CTE，`PM_VERSION` 要在每个 `UNION ALL` 分支里显式投影出来

这类逻辑优先通过：

- `business_knowledge.json`
- PromptBuilder 的 demand 指令
- few-shot / example
- validator 的专项校验

不要把它写成固定 SQL 模板。

---

## 9. 样本沉淀方式

### 9.1 样本来源原则

只保留真实来源：

- `examples/nl2sql_examples.template.json` 只收真实问题、真实 trace、且 SQL 与业务结果都人工确认后的样例
- `eval/evaluation_cases.json` 也只保留真实问题、真实 trace 或真实 replay 沉淀出的 case
- example 会参与检索、管理和调试证据，并在命中时以 `retrieved_examples` 形式进入 SQL prompt
- 主 SQL prompt 不再依赖问题特定的内置场景模板；当前主要依赖 `retrieved_examples`、`business_notes`、`join_patterns` 和 validator 约束
- 像“202604，MDL工厂各个产品大类实际投入数量”这类高频真实问法，既适合沉淀为 example，也适合把稳定字段叫法收进 `field_semantics`
- 像“oms库存，近6个月库存变化趋势”这类高频真实问法，也适合同时沉淀为 example 和 eval case；这类样例要把 `oms_inventory`、`biz_month` 趋势维度，以及常规库存默认同时返回 `glass_qty` / `panel_qty` 的口径写清楚

不要再维护假设样本。

### 9.1.1 Example 格式约束

- `coverage_tags` 至少包含 `real`、业务域和关键口径标签
- `result_shape` 用结构语义，不用随意命名：
  - 单维分组：直接写维度名，例如 `biz_month`
  - 多维分组：使用 `_by_` 连接
  - 无维度但有指标：`metric_only`
- 新样例优先通过 `materialize-example` 物化，再补充必要的 `notes`
- `materialize-example` 写入后当前会触发 retrieval corpus reload；受影响向量会增量重建并持久化到 runtime 库，通常不需要重启服务
- 如果一条样例会误导同域其他问题，即使来源真实，也不应该继续保留在 example 集里

### 9.2 推荐沉淀流程

对真实联调里有代表性的失败问题，建议按下面处理：

1. 先复现并保存 `trace_id`
2. 对该 `trace_id` 执行 replay
3. 若有价值，调用 `materialize-case`
4. 若属于高频标准问法，再调用 `materialize-example`
5. 修复后再次 replay，确认变化稳定

补一个判断口径，避免每次失败都直接加配置规则：

1. 先判断失败点是在 `QueryPlan` 之前，还是在 SQL 生成之后
2. 如果系统没听懂问题，例如 `最新p版`、`最近6个月` 或 `产品分类` 这类字段说法没被解析出来，优先修语义配置 / `field_semantics`
3. 如果 `QueryPlan` 已经正确，但 SQL 仍然按错维度或错结构生成，优先修 prompt / example / validator
4. 只有当新增内容能复用到一类真实问题时，才保留；单题补丁不要长期沉淀

---

## 10. Eval / Replay 怎么用

当前不再维护离线 planner-only 回归脚本。问题回归优先走真实链路：

1. 先在前端或 `POST /api/chat/query/stream` 复现问题
2. 记录 `trace_id`
3. 优先 replay 该 trace
4. 若值得长期保留，再 materialize 成 eval case
5. 用在线 eval 批量回归这些真实 case

语义配置 lint：

```bash
python3 backend/domain_config_lint.py
```

说明：

- `eval/evaluation_cases.json` 继续保留，但定位是在线 eval / replay case 集
- LLM intent、SQL 生成、SQL 校验和执行结果应在 live / replay / eval 链路验证
- 不再单独维护离线 planner-only 回归入口

---

## 11. 首轮验收标准

首轮真实联调建议达到：

- 主线问题能稳定生成可执行 SQL
- 结果和人工预期一致或差异可解释
- SQL validation 错误能指导 repair
- 执行失败可以通过 trace 或 workspace 快速定位
- 新增修复可以沉淀成 eval case，避免反复回归

---

## 12. 最后只记一句话

面对真实问题时：

- 先定位错在哪一层
- 再把修复沉淀到对的地方
- 不要把系统重新拉回规则模板时代
