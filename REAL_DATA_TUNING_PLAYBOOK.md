# 真实数据调优手册

## 1. 目标

本文用于指导当前 Text2SQL 后端从“离线规则和脚手架阶段”进入“真实数据与真实问法联调阶段”。

当前系统已经完成的重点是：

- 结构化语义解析与 follow-up 继承骨架
- 语义视图脚手架与契约校验
- SQL 校验、风险输出与运行时审计骨架
- 离线回归、语义层 lint、配置治理
- 多源检索与基础混排治理

因此，下一阶段的调优重点不再是继续堆纯离线规则，而是用真实数据、真实库结构和真实问题样本收敛系统行为。

## 2. 适用范围

适用于以下场景：

- 已有可读的真实业务库或脱敏测试库
- 可以提供真实字段结构、表关系和典型问法
- 希望评估当前系统是否能从 alpha/beta 骨架进入可用联调阶段

不适用于以下场景：

- 完全没有任何真实表结构和样本问法
- 只能继续基于猜测调整字段和口径
- 希望直接跳过联调进入生产接入

## 3. 进入联调前的最低准备

至少准备以下信息：

- 一份可访问的只读数据库环境，建议独立账号
- 涉及主链路的核心表结构：字段名、类型、主键、时间字段、版本字段
- 每个业务域 5 到 10 条真实高频问题
- 每条问题对应的业务预期：主题域、指标、时间范围、是否需要分组、是否需要澄清
- 当前最关注的 2 到 3 条主线

建议优先选择的主线：

- 库存
- 计划/实际
- 需求/销售财务

## 4. 联调顺序

建议严格按下面顺序推进，而不是同时展开。

### 4.1 第一步：校准真实表结构

先做：

- 核对真实表名是否与 `semantic/semantic_layer.json` 一致
- 核对时间字段、版本字段、主实体字段是否真实存在
- 核对语义视图草案里的底表和字段映射是否成立
- 核对 `permission_scope_fields`、`default_sort`、`time_filter_fields` 是否引用真实字段

输出物：

- 一轮更新后的 [semantic/semantic_layer.json](/home/y/llm/new/semantic/semantic_layer.json)
- 必要时同步更新 [sql/semantic_view_drafts.sql](/home/y/llm/new/sql/semantic_view_drafts.sql)

验收标准：

- `python3 backend/semantic_lint.py` 通过
- 语义视图契约与字段引用不再依赖猜测

### 4.2 第二步：补真实问题样本

先做：

- 每个域收集 5 到 10 条真实问题
- 区分单轮、follow-up、clarification、跨域切换
- 标出每条问题的期望指标、维度、过滤条件、排序/TopN、趋势/对比意图

输出物：

- 扩充 [eval/evaluation_cases.json](/home/y/llm/new/eval/evaluation_cases.json)
- 必要时补 example 库

验收标准：

- 真实样本能进入离线回归
- 可以复现实验前后的回归变化

### 4.3 第三步：调规划与 SQL 生成

先做：

- 用真实问题跑 `classify -> plan -> sql`
- 看 Query Plan 是否落在正确主题域、语义视图、指标和维度上
- 看 SQL 是否引用真实字段、是否出现无意义排序、错误聚合或缺失过滤

重点观察：

- follow-up 继承是否稳定
- 版本过滤与时间过滤是否正确注入
- 趋势/对比类问题是否落到合理结果形态
- SQL validator 是否能识别真实高风险 SQL

输出物：

- 修订 planner、semantic_runtime、sql_generator、sql_validator
- 扩充真实回归 case

验收标准：

- 主线问题能稳定产出可执行 SQL
- 高风险和错误 SQL 能被拦截或打 warning

### 4.4 第四步：调检索

先做：

- 观察 retrieval top hits 是否对真实问题有帮助
- 看 example / semantic_view / knowledge / vector 哪条通道经常排错
- 识别是否存在“clarification 示例排到过前”“knowledge 命中太弱”“semantic view 不稳定上榜”等问题

调优顺序建议：

1. 先调 example 质量和覆盖
2. 再调 semantic view/metric/knowledge 的 rerank 规则
3. 最后再考虑真实 embedding provider 和向量库

原因：

- 如果 example 和结构化通道本身就有噪音，先接真实向量库只会把噪音放大

验收标准：

- 检索结果能解释 planner 的后续选择
- top hits 不再长期被错误 source 吞掉

### 4.5 第五步：调执行治理

先做：

- 用只读环境执行真实 SQL
- 观察慢查询、超时、空结果、截断结果、连接失败
- 记录真实运行时日志和 SQL 审计

重点收敛：

- 大结果集治理
- 缺失时间条件治理
- 可疑 join 和高风险聚合
- 默认 limit、排序、scan 风险

验收标准：

- 运行时风险有日志可查
- 慢查询和高风险模式可稳定复现与收敛

## 5. 最小联调清单

建议第一次真实联调只做以下内容：

1. 准备 1 个只读数据库环境
2. 选 3 张到 6 张主线表
3. 收集 20 条真实问题
4. 先跑离线回归，再跑真实 classify/plan/sql
5. 修字段映射和视图草案
6. 再扩回归 case

不要一上来做：

- 全域全表接入
- 大规模向量库接入
- 物化视图/索引治理
- 复杂权限细化
- 前端大范围交互改造

## 6. 建议记录格式

每条真实问题建议记录：

- `question`
- `business_domain`
- `expected_metrics`
- `expected_dimensions`
- `expected_filter_fields`
- `expected_question_type`
- `expected_status`
- `notes`

每次联调建议记录：

- 问题原文
- semantic parse
- classification
- query plan
- sql
- plan/sql warnings
- 实际执行结果摘要
- 人工判断结论

## 7. 常见误区

### 7.1 还没核对字段，就先调 prompt

这是错误顺序。

如果字段映射本身就是错的，继续调 prompt 只会掩盖配置问题。

### 7.2 还没扩真实样本，就先接大规模向量库

这是高成本低收益操作。

没有真实样本时，很难知道检索是在变好还是变坏。

### 7.3 只看 SQL 能不能跑，不看 Query Plan

这是不够的。

很多问题不是 SQL 跑不跑得通，而是 Query Plan 从一开始就偏了。

### 7.4 只看通过率，不看失败类型分布

建议优先观察：

- `missing_dimensions`
- `missing_filter_fields`
- `unexpected_sort_fields`
- `expected_question_type`
- `plan_validation_failed`
- `sql_validation_failed`

## 8. 当前建议结论

当前仓库已经把“真实联调前的基础设施”补到一个比较合适的程度。

下一阶段的正确方向是：

- 减少继续凭空补规则
- 尽快引入真实字段结构和真实问题样本
- 用真实联调结果反推语义层、检索、规划和 SQL 生成

也就是：

- 先校准真实数据
- 再收敛真实问题
- 再决定哪些规则值得固化
- 最后才做更重的检索和执行优化
