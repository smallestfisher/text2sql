# Text2SQL 架构说明：LLM-first

## 1. 当前原则

当前工程的主路径已经切换为 LLM-first：

- `tables.json` 提供真实数据库表、字段和关系描述
- `business_knowledge.json` 提供结构化业务知识，是主业务说明来源
- LLM 直接基于真实 schema、业务知识、Query Plan 和少量场景 few-shot 生成 MySQL SQL
- 语义层、检索、Query Plan、权限和 validator 负责辅助约束，不再承担主 SQL 拼接职责
- SQL 校验或执行失败时，允许一次基于原上下文的 LLM repair
- Prompt 上下文必须经过选择和预算控制，不能把全量 schema、知识和样例直接塞给模型

这意味着系统不要求真实数据库预建额外分析对象。复杂横表逻辑优先由 LLM 在 SQL 中用 `WITH` CTE 展开，再由校验器治理。

## 2. 核心配置与职责边界

### 2.1 `tables.json`

职责：

- 描述真实表名、字段名、字段含义
- 描述时间字段、版本字段、主业务键、常见 join 关系
- 给 LLM 和调试人员提供真实 schema 依据

不负责：

- 本地拼 SQL
- 存放大段业务规则

### 2.2 `business_knowledge.json`

职责：

- 以 `domain / tables / keywords / notes` 形式维护稳定业务口径
- 让 PromptBuilder 能按当前问题命中相关知识块
- 为 LLM 提供横表映射、版本口径、指标解释、常见过滤约束

不负责：

- 本地 SQL 模板
- 针对单题硬编码业务分支

### 2.3 `semantic/domain_config.json`

当前仅作为辅助配置：

- 辅助语义解析、主题域判断和 follow-up 分类
- 辅助检索和 Query Plan 收敛
- 辅助 validator 判断已知来源、字段和风险

它不是主 SQL 编译器。不要再往里面堆完整 SQL 模板。

## 3. 端到端主链路

一次查询的主链路如下：

1. API 接收自然语言问题和用户上下文
2. `query_intent` 提取指标、实体、时间、版本和 follow-up 信号
3. 分类器判断问题是首轮新问、同域新问、跨域新问、追问、澄清还是无效问题
4. 对低信号问题触发 LLM relevance guard，先判断是否属于业务数据查询范围
5. 如果被判为 `invalid` 或 `clarification_needed`，链路直接在 SQL 生成前终止
6. Query Planner 生成基础 Query Plan
7. 检索层补 example / metric / knowledge 命中结果，辅助收敛 Query Plan
8. 权限服务把用户数据范围过滤条件注入 Query Plan
9. PromptBuilder 从 `tables.json`、`business_knowledge.json`、Query Plan 和 few-shot 里选择相关上下文
10. LLM 生成一条只读 `SELECT` 或 `WITH ... SELECT`
11. SqlValidator / SqlAstValidator 校验来源范围、过滤条件、时间/版本一致性、权限和 LIMIT
12. 如果校验或执行失败，LLM 使用原 prompt 和错误信息做一次 repair
13. SqlExecutor 在只读业务库执行 SQL
14. AnswerBuilder 组织回答摘要
15. 审计、查询日志、会话消息和会话状态落到 runtime 库
16. Trace 在落库前附带 `response_snapshot`，供历史会话恢复 `latest_response`

## 4. Prompt 与 token 控制

### 4.1 当前 prompt 方向

当前 SQL、分类、相关性判断 prompt 统一以中文自然语言指令为主。表名、字段名和数据库对象名仍保持真实英文命名。

### 4.2 上下文选择原则

LLM-first 不等于无限扩 prompt。当前 SQL prompt 的选择规则是：

- 只发送 Query Plan 命中的真实表结构；没有命中表时才使用主题域候选表
- 优先从 `business_knowledge.json` 里按 `domain / tables / keywords` 选择结构化知识块
- few-shot 按场景命中，不做全局注入
- 业务说明受 `PromptBuilder.BUSINESS_NOTES_MAX_CHARS` 等预算控制
- trace 的 `build_sql_prompt.metadata.context_summary` 会记录本次用了哪些来源、知识长度和 few-shot

### 4.3 维护规则

避免 token 爆炸时，遵守以下原则：

- 不把全量 `tables.json` 放进 SQL prompt
- 不把全量 `business_knowledge.json` 放进 SQL prompt
- 不把所有 few-shot 一次性塞进 prompt
- 新增业务知识时，优先写成短小、稳定、可命中的知识块
- 高频失败样例进入 few-shot 或 eval case 前，先判断是否具有复用价值

## 5. 多轮对话与会话恢复

当前系统支持多轮对话，但本质上是“会话状态 + 分类继承 + Query Plan 增量修改”的多轮，不是无限制的自由长对话记忆。

关键机制：

- 分类器会判断当前问题是否需要继承上一轮上下文
- `context_delta` 用于表达时间替换、版本替换、维度替换、筛选追加等最小修改
- `session_state` 记录当前域、指标、维度、过滤、时间、版本和最近一次 Query Plan
- `response_snapshot` 记录安全可恢复的回答快照，供历史会话重开

当前前端会话恢复的标准入口是：

- `GET /api/chat/sessions/{session_id}/workspace`

这个接口一次性返回：

- 会话消息
- 当前 `session_state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- 当前会话内每个 `trace_id` 对应的 `trace_artifacts`

前端消息流里的结果卡和右侧详情面板都基于这份 `workspace` 数据，而不是再自己拼 `history + state + query_logs + trace + sql_audit`。这样可以避免“左边消息正确，右边详情像没执行过”的状态漂移。

## 6. 前端工作台与权限脱敏

### 6.1 用户工作台

当前用户工作台是三栏结构：

- 左侧：会话列表
- 中间：消息流和助手结果卡
- 右侧：详情侧栏，包含 `结果 / SQL / Trace / 状态`

快捷问题卡片只在空会话时显示；发送第一条消息后，消息流改为展示真实历史记录。

### 6.2 结果卡与详情面板

每条 assistant 消息如果关联 `trace_id`，会在消息下方展示结果卡：

- 状态
- 返回行数
- 结果预览
- `查看详情`
- `下载`

点击 `查看详情` 后，右侧详情面板会切换到对应 `trace_id` 的 `trace_artifact`。这意味着“详情”是按轮次切换，不只是看会话的最后一次结果。

### 6.3 权限脱敏

普通登录用户也可以打开详情面板，但权限仍然分层：

- 没有 `can_view_sql` 时，`latest_response.sql`、`sql_audit.sql_text` 和 `session_state.last_sql` 会被清空
- 没有 `can_download_results` 时，结果下载按钮不会可用
- 会话与 trace 读取都经过权限服务脱敏处理

因此，用户可以看回答、状态、trace 摘要，但不会因为 UI 保留 SQL/State 面板就自动拿到受限 SQL。

## 7. demand 横表处理原则

`p_demand` / `v_demand` 是横向需求表，不能简单理解成 `MONTH = 202604`。

正确方向是让 LLM 根据业务说明生成如下逻辑：

- `MONTH` 表示起始需求月份，通常以紧凑 `YYYYMM` 存储
- `REQUIREMENT_QTY` 对应 base `MONTH`
- `NEXT_REQUIREMENT` 对应 base `MONTH + 1`
- `LAST_REQUIREMENT` 对应 base `MONTH + 2`
- `MONTH4` 到 `MONTH7` 对应 base `MONTH + 3` 到 `+6`
- “最新 N 版”要先按版本字段取最新 N 个版本
- 如果外层还要按 `PM_VERSION` 过滤，展开后的 CTE 必须显式投影 `PM_VERSION`
- 针对 `YYYYMM` 紧凑月份，不能直接对原始字符串做错误的日期函数运算

当前这类逻辑主要通过：

- `business_knowledge.json`
- PromptBuilder 的 demand 专项指令
- demand few-shot
- SqlValidator 的 demand 月份映射和 `PM_VERSION` 投影校验

来共同保证，而不是依赖数据库预建展开对象。

## 8. 历史语义设计的当前位置

历史语义设计现在只是辅助参考，不是运行时主依赖：

- 可以作为业务口径参考
- 可以继续留在语义配置和管理元数据里
- 可以作为未来性能优化或稳定口径落库的候选
- 不应作为 chat、`/api/query/sql` 或前端工作台的必需执行对象

只有在真实联调证明某类逻辑高频、稳定、复杂且确实需要数据库侧固化时，才考虑单独评审是否创建视图或物化表。

## 9. 维护优先级

遇到准确率问题时，优先按这个顺序处理：

1. 修 `tables.json` 的字段描述和真实表关系
2. 修 `business_knowledge.json` 的业务口径
3. 补高质量 few-shot 和 eval case
4. 修 PromptBuilder 的通用指令和上下文选择
5. 修 SQL validator 的边界和误拦截
6. 最后才考虑新增局部规则

局部规则只能用于分类、约束、校验或安全治理，不能重新变成业务 SQL 生成主路径。


## 10. 配置规则治理边界

当前工程允许存在少量配置规则，但这些规则只能服务于**理解问题**，不能替代 LLM **生成 SQL**。

### 10.1 什么可以进配置

适合进入配置的内容：

- 时间解析，例如 `26年 -> 2026-01-01 ~ 2026-12-31`
- 稳定枚举过滤，例如 `投入 / 产出 / 报废 -> act_type`
- 权限字段和稳定别名
- 已被多个真实问题验证过的稳定 metric 消歧

### 10.2 什么不该进配置

不适合进入配置的内容：

- 某一道题对应哪条固定 SQL
- 某个场景必须怎么 join
- 横表必须怎么展开成 CTE
- `UNION ALL`、投影、聚合和 SQL 模板细节

这些应该优先放到：

- `business_knowledge.json`
- `PromptBuilder`
- `example`
- `replay / eval case`

### 10.3 一个最简单的判断法

- 如果规则回答的是“用户这句话是什么意思”，可以考虑进配置
- 如果规则回答的是“SQL 具体应该怎么写”，就不该进配置

### 10.4 一个通俗例子

以 `26年MDL工厂top10投入型号及其物量` 为例：

- `26年` 是时间解析问题，可以进配置
- `MDL工厂` 是过滤提取问题，可以进配置
- `投入` 是枚举过滤问题，可以进配置
- `物量` 在工厂语境下默认指 panel，是业务口径问题，优先放业务知识；如果多条真实问题都验证稳定，再谨慎放配置消歧
- 最终 SQL 如何写，仍然交给 LLM

### 10.5 治理要求

为了避免系统滑回规则驱动，建议遵守：

- 同类规则至少命中多个真实问题后再进入配置
- 定期清理只服务单题的规则
- 新增规则必须配真实 `eval case` 或 replay 验证
- 如果规则已经在决定 SQL 结构，就必须退回 prompt / knowledge / example 路径
