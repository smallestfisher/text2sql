# 输入问题后的准确度调试手册

## 1. 目的

本文面向当前已经落地的 LLM-first Text2SQL 主链路，说明一个问题进来后，应该怎么判断错在哪一层，以及应该把修复沉淀到哪里。

当前默认原则是：

- 优先修真实 schema 上下文
- 优先修业务知识和 few-shot
- 优先修 Query Plan 和 validator 的边界
- 不把问题重新改成 Python 规则或本地 SQL 模板

## 2. 先看哪条链路

一次 `POST /api/chat/query` 的关键步骤大致是：

1. 读取当前 `session_state`
2. 语义解析，提指标、实体、时间、版本和 follow-up 信号
3. 问题分类，判断首轮新问、同域新问、跨域新问、追问、澄清或无效问题
4. 对低信号问题触发 LLM relevance guard
5. 生成基础 Query Plan
6. 检索 example / knowledge / metric，辅助收敛上下文
7. 注入权限过滤条件
8. 根据真实表结构、结构化业务知识和 few-shot 生成 SQL prompt
9. LLM 生成 SQL
10. validator 校验 SQL，必要时触发一次 repair
11. 执行 SQL
12. 组织回答、落查询日志、落 trace、落 response snapshot

因此，“答错了”本质上通常属于下面几类之一：

- 语义解析错
- 分类错
- prompt 上下文不对
- Query Plan 错
- 权限注入影响结果
- SQL 生成错
- validator 误拦或漏拦
- 数据本身或执行环境有问题

## 3. 调试入口

### 3.1 用户工作台

当前前端工作台已经足够做单题排查：

- 消息流里可以直接看每轮 assistant 的结果卡
- 右侧详情栏可以切到 `结果 / SQL / Trace / 状态`
- 普通用户也能看详情栏，但 SQL 是否可见受 `can_view_sql` 控制

如果问题是“为什么这轮回答不准”，优先直接在消息对应的结果卡点 `查看详情`。

### 3.2 管理端

管理端适合做历史问题和批量问题排查：

- 最近 query logs
- SQL audit
- runtime 状态
- replay
- materialize case / materialize example

其中最重要的是 replay，因为它能让你基于原 `trace_id` 复跑，不用靠人工重复构造环境。

### 3.3 API

当前最常用的调试接口是：

- `POST /api/chat/query`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/export`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `POST /api/admin/eval/cases/{case_id}/replay`

如果问题是新问题，优先先发起一次真实查询；如果问题是历史问题，优先走 `trace_id -> replay`。

## 4. 先分流，不要先改 SQL

先看这些字段：

- `classification.question_type`
- `classification.subject_domain`
- `answer.status`
- `plan_validation.valid`
- `sql_validation.valid`
- `execution.status`

推荐的粗分规则：

- `invalid`：先看 relevance guard 是否把问题判成非业务查询
- `clarification_needed`：先看语义槽位是否不足，不要先改 SQL
- `plan_validation.valid = false`：先看 Query Plan、表选择、维度、过滤、时间、版本
- `sql_validation.valid = false`：先看 SQL 生成、字段引用、权限过滤和 validator
- `execution.status = db_error`：先看 SQL 语法、字段大小写、真实库字段是否存在
- `execution.status = empty_result`：先看过滤条件、时间、版本、权限和底层数据
- 执行成功但答案不准：优先看 schema、业务口径、few-shot、聚合口径和排序

## 5. 先看 workspace 和 trace

### 5.1 `workspace` 看什么

优先看：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `trace_artifacts`

为什么先看这个：

- 它是当前前端真正消费的聚合接口
- 它能直接告诉你“前端展示错了”还是“后端恢复错了”
- 它能确认每条 assistant 消息是否挂到了正确的 `trace_id`

如果左侧消息对，但右侧详情不对，先看 `workspace` 返回是否已经错位，再决定是查后端恢复逻辑还是查前端状态消费。

### 5.2 `trace` 看什么

当前最有价值的 trace 步骤通常是：

- `terminal_gate`
- `retrieve`
- `compile_plan`
- `build_sql_prompt`
- `validate_sql`
- `execute`
- `response_snapshot`

重点看：

- 是否在 SQL 前被 `invalid` 或 `clarification_needed` 短路
- 检索是否命中相关 example / knowledge
- `build_sql_prompt.metadata.context_summary` 里本次选了哪些表、知识块和 few-shot
- validator 到底是误拦还是正确拦
- `response_snapshot` 是否已经正确持久化

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

- 明明问库存，却识别成计划/实际
- 时间没提出来，导致宽范围扫描
- 版本没提出来，导致结果偏大
- 指标别名没识别，后面全链路都漂

优先修：

- 指标别名、实体别名、domain inference
- 时间/版本提取
- follow-up cue

### 6.2 分类层

看：

- `classification.question_type`
- `classification.inherit_context`
- `classification.context_delta`
- `classification.reason_code`

典型症状：

- 明明是追问，却被当成新问题
- 明明切到新域，却还继承老上下文
- 信息足够，却总被打成 `clarification_needed`
- 无关问题没有被 relevance guard 挡住

优先修：

- `classification_rules`
- follow-up 线索
- 澄清文案
- relevance guard 的判断边界

### 6.3 检索与 prompt 上下文层

看：

- `retrieval_terms`
- `retrieval_channels`
- hits 命中了哪些 example / knowledge
- `build_sql_prompt.metadata.context_summary`

典型症状：

- 选对了业务域，但没有把关键知识块放进 prompt
- few-shot 命中了很多，但都不相关
- 同一个问题多次执行时，上下文摘要波动很大

优先修：

- `tables.json`
- `business_knowledge.json`
- example / few-shot
- PromptBuilder 的上下文选择规则

### 6.4 Query Plan 层

看：

- `tables`
- `metrics`
- `dimensions`
- `filters`
- `time_context`
- `version_context`
- `sort`
- `limit`

典型症状：

- 指标对，但表选错了
- 维度缺失，导致 group by 不对
- filter 丢失，结果集过大
- latest_n / 版本条件没落下来

优先修：

- Query Plan 约束
- 真实表和字段上下文
- 版本/时间提取
- plan validator

### 6.5 权限层

看：

- Query Plan 里是否注入了权限过滤
- validator 是否因为缺权限字段报错
- SQL 是否真的包含必须的过滤条件
- 返回结果是否被 field visibility 裁掉

典型症状：

- 管理员能查到，普通用户为空
- SQL 校验总报缺少权限过滤
- 结果字段缺失，其实是被隐藏或脱敏

优先修：

- 用户角色、data scope、field visibility
- `permission_scope_fields`
- 权限注入和 SQL 校验的一致性

### 6.6 SQL 生成与校验层

看：

- SQL 是否只用了真实表和字段
- 是否引用了 Query Plan 外的 source
- 是否缺过滤条件、group by、sort、time filter、limit
- validator 报错是否足够明确，便于 repair

典型症状：

- 字段名错
- 来源表错
- 聚合对了但 group by 漏维度
- 缺时间条件或 limit

优先修：

- `tables.json`
- `business_knowledge.json`
- few-shot
- PromptBuilder 的通用指令
- validator 和 repair 错误信息

### 6.7 执行与结果层

看：

- `execution.status`
- `row_count`
- `elapsed_ms`
- `truncated`
- 真实业务库是否存在预期数据

典型症状：

- SQL 合法但没有数据
- 慢查询导致 timeout
- 结果被截断后误读

优先修：

- 补时间范围和默认 limit
- 补清晰业务口径
- 优化 SQL 风险治理

## 7. demand 横表专项检查

对 `p_demand` / `v_demand` 类问题，排查时务必确认：

- 目标月份不是简单的 `MONTH = YYYYMM`
- `MONTH` 是起始月份，不同列对应不同偏移月
- latest N 版本逻辑是否先取了最新版本集合
- 展开的 CTE 是否显式带了 `PM_VERSION`
- 对紧凑 `YYYYMM` 是否做了正确日期转换

这类问题如果不准，优先修：

- `business_knowledge.json`
- demand few-shot
- PromptBuilder 的 demand 指令
- SqlValidator 的 demand 相关校验

不要回退到预建数据库展开对象作为运行时必需对象。

## 8. 单题调试标准流程

建议固定按下面步骤走：

1. 在工作台或 API 里复现问题，拿到 `session_id` 和 `trace_id`
2. 看 `workspace`，确认消息、详情和 `trace_artifacts` 是否一致
3. 看 `trace`、`sql-audit` 和 `retrieval`
4. 判断问题属于解析、分类、上下文、Query Plan、SQL、validator、执行还是权限
5. 按最小必要原则修 `tables.json`、`business_knowledge.json`、few-shot、PromptBuilder 或 validator
6. 用 `replay` 复跑同一条 `trace_id`
7. 对比 replay diff，尤其看分类、Query Plan、SQL、执行状态和 prompt context summary 是否变化
8. 如果问题具有代表性，物化成 eval case 或 example

## 9. 修复优先级

建议严格按这个顺序修：

1. 先确认是不是数据口径或权限问题
2. 再确认语义解析和分类是否正确
3. 再看 prompt 是否拿到了正确 schema、业务说明和 few-shot
4. 再看 Query Plan 是否稳定
5. 再看 SQL 生成和 validator
6. 最后才考虑模型参数或临时补丁

原因是：

- 前面层错了，后面层再聪明也只是对错误目标优化
- schema、业务知识和样例的修复可以复用到一类问题
- 单题硬编码补丁维护成本最高

## 10. 一句话原则

当前架构下，提升准确度最有效的方法不是继续写 SQL 模板，而是把失败问题持续下沉到 `tables.json / business_knowledge.json / few-shot / PromptBuilder / validator / replay / eval case` 这几层。
