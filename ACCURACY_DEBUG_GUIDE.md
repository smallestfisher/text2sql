# 输入问题后的准确度调试手册

## 1. 目的

本文面向当前仓库已经实现的 Text2SQL 主链路，说明在用户输入一个问题之后，应该如何沿现有架构定位问题、提升准确度，并把修复优先沉淀到语义层、示例库和治理层，而不是继续堆 prompt 补丁。

适用范围：

- 当前 `backend/app/services/orchestrator.py` 主链路
- 当前前端管理端、会话详情、Trace 和 replay 能力
- 当前 `semantic_layer.json + examples + retrieval + query_plan + sql validation` 这套结构

## 2. 当前链路怎么看

一次 `POST /api/chat/query` 进入系统后，当前主链路大致是：

1. `chat.py` 接口接收问题和用户上下文
2. `orchestrator.py` 生成 trace，并加载 `session_state`
3. `query_planner.py` 先做 `semantic_parse`，再做问题分类和基础 Query Plan
4. `retrieval_service.py` 基于 parse 结果做 example / semantic_view / metric / knowledge / vector 检索
5. `llm_client + query_plan_compiler.py` 尝试给 Query Plan 注入 LLM hint，并做边界收缩
6. `permission_service.py` 把权限过滤条件注入 Query Plan
7. `query_plan_validator` 校验 Query Plan
8. `sql_generator.py` 生成 SQL，必要时尝试使用 LLM SQL hint
9. `sql_validator.py` 和 `sql_ast_validator.py` 校验 SQL
10. `sql_executor.py` 执行 SQL
11. `answer_builder.py` 组织回答
12. `runtime_log_repository`、`audit_service`、`session_service` 落日志、trace、会话消息、SQL 审计和快照

因此，准确度问题不要笼统看成“模型答错了”，而要先判定它属于哪一层：

- 语义解析错
- 问题分类错
- 检索召回错
- Query Plan 错
- 权限注入影响结果
- SQL 生成错
- SQL 校验过松或过严
- 执行环境或底层数据口径问题

## 3. 调试入口

当前仓库已经有几类可直接用的调试入口。

### 3.1 用户工作台

前端用户工作台可直接看：

- 回答结果
- SQL 面板
- Trace 面板
- State 面板

适合做单次问题的快速定位。

### 3.2 管理端

管理端目前可看：

- 最近查询日志
- SQL 审计
- 运行状态
- 最近运行会话
- 失败日志复跑

其中最新补上的 replay 能力最重要，因为它让你可以直接对历史 `trace_id` 复跑，避免“问题已经过去了，但无法复现”。

### 3.3 API 级别调试

当前可直接用的接口包括：

- `POST /api/chat/query`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/export`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/eval/cases/{case_id}/replay`

如果问题是线上历史问题，优先走 `trace_id -> replay`；如果是新问题，优先直接在会话里发问，然后拿 trace 看链路。

## 4. 先判断是哪一层出错

### 4.1 回答状态先分流

先看返回里的这些字段：

- `classification.question_type`
- `classification.subject_domain`
- `answer.status`
- `plan_validation.valid`
- `sql_validation.valid`
- `execution.status`

粗分规则：

- `clarification_needed` 或 `invalid`：优先看分类和语义解析，不要先改 SQL
- `plan_validation` 失败：优先看 Query Plan、语义域、指标和视图选择
- `sql_validation` 失败：优先看 SQL 生成和字段映射
- `execution.db_error / timeout / empty_result`：优先看 SQL、权限注入、时间条件、底表口径
- 执行成功但答案不准：优先看检索、Query Plan、指标口径和语义视图

### 4.2 先看 Trace，不要先猜

`trace` 里至少能回答几个关键问题：

- 会话状态是否正确加载
- 检索是否命中相关 example / semantic_view / metric / knowledge
- LLM hint 是否被接受，还是被 reject 后回退
- 计划校验和 SQL 校验在哪一步失败

如果 trace 已经显示 `llm query plan hint rejected` 或 `llm sql hint rejected`，那就说明不是简单的“模型质量差”，而是模型输出越界，当前系统已经在保护主链路。此时应该收紧语义层或补示例，而不是一味放开 LLM。

## 5. 分层调试方法

### 5.1 语义解析层

代码入口：

- `backend/app/services/semantic_parser.py`
- `backend/app/services/semantic_runtime.py`

重点看：

- `matched_metrics` 是否识别对
- `matched_entities` 是否识别对
- `filters` 是否提对
- `time_context` / `version_context` 是否提对
- `subject_domain` 是否推断对

典型症状：

- 明明问库存，却跑到计划域
- 时间没提出来，导致全表扫
- 版本口径没提出来，导致结果偏大
- 指标别名没识别，后续全链路都开始漂

优先修复方式：

- 给语义层补指标别名、实体别名、domain inference 特征
- 给时间/版本提取规则补更稳定的表达
- 不要先在 SQL 生成器里硬写一个 if 去救这个问题

### 5.2 问题分类层

代码入口：

- `backend/app/services/question_classifier.py`
- `backend/app/services/query_planner.py`

重点看：

- 当前问题被判成 `new / follow_up / clarification_needed / invalid` 是否合理
- `inherit_context` 是否正确
- `context_delta` 是否符合预期
- 是否因为分类器过保守，导致过度澄清

典型症状：

- 用户明显在追问，却被当成新问题
- 用户已经切题到新域，却还继承老上下文
- 明明信息足够，却总被打成 `clarification_needed`

优先修复方式：

- 先补 `classification_rules`、follow-up cue、澄清文案和 `session_semantic_diff`
- 再考虑是否需要打开或调整 classification LLM
- 不要直接在前端把问题改写后再塞给后端，这会污染真实链路

### 5.3 检索层

代码入口：

- `backend/app/services/retrieval_service.py`
- `backend/app/services/vector_retriever.py`

重点看：

- top hits 是否命中了正确 example / semantic_view / metric / knowledge
- `retrieval_terms` 是否合理
- `retrieval_channels` 和 `hit_count_by_source` 是否异常

典型症状：

- 问题被分到对的域，但命中的 example 很偏
- 该命中的语义视图没进 top hits
- example 命中很多，但都是低质量样例

优先修复方式：

- 补 example，尤其是高频问法和 follow-up 样例
- 补 semantic view purpose / output_fields / notes，让文档检索更有信号
- 补 metric alias 和 table metadata 描述
- 如果问题是召回排序错，不要先改 SQL 生成

### 5.4 Query Plan 层

代码入口：

- `backend/app/services/query_planner.py`
- `backend/app/services/query_plan_compiler.py`
- `backend/app/services/query_plan_validator.py`

重点看：

- `semantic_views / tables / metrics / dimensions / filters / time_context` 是否稳定
- LLM plan hint 是否被接受
- fallback 到本地 planner 后是否恢复正常

典型症状：

- 指标对，但选错语义视图
- 维度没带，导致 group by 不对
- filter 丢失，导致结果集过大
- LLM hint 想加越界字段，被 reject 后又退回保守版本

优先修复方式：

- 优先增强 `semantic_runtime.sanitize_query_plan` 的可解释性和约束
- 补 query profile / semantic view ranking 所需特征
- 如果某类问题稳定需要特定视图，优先沉淀到视图排序和语义层，而不是 prompt 魔改

### 5.5 权限层

代码入口：

- `backend/app/services/permission_service.py`
- `backend/app/services/policy_engine.py`

重点看：

- Query Plan 是否被注入了额外权限过滤条件
- `required_filter_fields` 是否和 SQL 对齐
- 结果是否因字段 masking / hidden 被裁掉

典型症状：

- 管理员看到数据正常，普通用户为空
- SQL 校验一直报缺少权限过滤条件
- 结果字段缺失，其实是字段可见性策略生效

优先修复方式：

- 先核对用户角色、data scope、field visibility
- 再核对 domain 的 `permission_scope_fields` 是否配对
- 不要把权限问题误判成模型理解问题

### 5.6 SQL 生成与校验层

代码入口：

- `backend/app/services/sql_generator.py`
- `backend/app/services/sql_validator.py`
- `backend/app/services/sql_ast_validator.py`

重点看：

- SQL 来源是本地生成还是 LLM hint
- 是否引用了 Query Plan 外的 source
- 是否缺过滤条件、group by、sort、time filter、limit

典型症状：

- SQL 字段名错
- SQL 用了错误的 source
- SQL 聚合正确但 group by 漏维度
- SQL 没有时间条件，扫描过大

优先修复方式：

- 优先修 `semantic_runtime.resolve_field`、metric 定义和 semantic view 字段映射
- 再补 SQL validator 的一致性检查
- 尽量不要给 SQL generator 继续堆业务 case 分支

### 5.7 执行与结果层

代码入口：

- `backend/app/services/sql_executor.py`
- `backend/app/services/answer_builder.py`

重点看：

- `execution.status` 是什么
- `row_count` 是否异常
- 是否被 truncate
- SQL 在业务库上是否真实能返回结果

典型症状：

- SQL 合法但没有数据
- 数据被截断后回答误导用户
- 数据库慢查询导致 timeout

优先修复方式：

- 补时间范围和默认 limit
- 补更稳定的语义视图，减少直接扫底表
- 补执行结果状态说明，避免把“执行成功但空结果”误读成“回答正确”

## 6. 提高准确度时的优先级顺序

建议严格按这个顺序修：

1. 先确认是不是数据口径或权限问题
2. 再确认语义解析和分类是否正确
3. 再看检索是否命中正确样例和语义视图
4. 再看 Query Plan 是否稳定
5. 再看 SQL 生成和校验
6. 最后才考虑 prompt 或 LLM 参数微调

原因很简单：

- 如果前面层错了，后面层再聪明也只能在错误目标上优化
- 语义层和示例库的修复可以复用到一类问题
- prompt 补丁通常只能救一个问题，且很难维护

## 7. 推荐的提准动作

### 7.1 最优先做的

- 补高频业务问法 example，尤其是真实失败问题
- 补 metric alias、entity alias、domain inference 特征
- 把高频复杂底表逻辑前移到 semantic view
- 用 replay 把失败问题稳定复现，再做回归验证

### 7.2 第二优先做的

- 补 Query Plan 校验与 SQL 校验的一致性检查
- 补权限字段覆盖和时间条件检查
- 为 runtime 日志和 replay 增加更细的 diff 观测

### 7.3 最后再做的

- 调 prompt
- 放宽 LLM hint 接受条件
- 增加临时业务分支逻辑

## 8. 建议的单题调试流程

对于一个不准的问题，建议固定按下面步骤走：

1. 在工作台或 API 里复现问题，拿到 `trace_id`
2. 看 `trace` 和 `sql-audit`，先判断错在分类、检索、规划、SQL 还是执行
3. 在管理端对该 `trace_id` 做 replay，确认是否稳定复现
4. 如果是解析/分类问题，优先改语义层和分类规则
5. 如果是检索问题，补 example、view 文档、metric alias
6. 如果是规划问题，补 query profile / semantic view ranking / plan sanitize
7. 如果是 SQL 问题，先修字段映射和 validator，再看 generator
8. 修复后再次 replay 原问题，确认链路稳定
9. 如果问题具有代表性，把它沉淀成 evaluation case 或 example

## 9. 当前架构下最值得继续补的调试能力

虽然现在已经能调，但还差几项会明显提升提准效率：

- replay 结果和原 trace 的字段级 diff
- 检索 top hits 的管理端可视化
- evaluation case 列表、单 case 复跑和失败筛选
- 下载与 replay 的审计日志
- 更明确的失败样本沉淀流程

## 10. 一句话原则

在当前架构里，提升准确度最有效的方法不是继续把 prompt 写长，而是把失败问题往 `语义层 / 语义视图 / 示例库 / 检索 / Query Plan 治理 / replay 闭环` 这几层持续下沉。
