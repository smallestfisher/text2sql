# 无真实数据阶段优化计划

## 1. 目标

在没有真实数据和真实问题的阶段，不能证明最终业务准确率，但可以先把工程基础打牢：

- LLM SQL 生成上下文可控
- 失败能定位到具体层
- 合成问题能覆盖主要交互形态
- validator 能兜住明显风险
- 真实问题到来后能快速沉淀为 replay / eval case / example

## 2. 当前已经完成的基础

下面这些基础能力已经落到当前实现：

- SQL prompt 只带 Query Plan 命中的真实表结构
- `business_knowledge.json` 是主业务知识来源，不做全量注入
- few-shot 按场景命中，不做全局注入
- `build_sql_prompt` trace 已带 `context_budget` 和 `context_summary`
- query log 和 replay diff 已能看到 prompt context summary
- `workspace` 聚合接口已成为前端会话恢复主入口
- `response_snapshot` 已支撑历史会话恢复
- demand 横表已补专项 prompt 指令和 validator 校验

## 3. 后续执行顺序

### P0 合成评测覆盖

继续补不依赖真实数据的合成 case：

- 多轮追问时间替换
- 多轮追问版本替换
- demand 横表目标月份
- latest N 版本
- TopN 排序
- 澄清问题
- 无效问题
- 权限过滤模拟

验收：

- `eval/evaluation_cases.json` 有明确 `scenario` 和 `coverage_tags`
- 离线回归能稳定覆盖分类、规划和权限层

### P1 Validator 和 Repair 可调试性

继续增强：

- validator 错误信息的可读性
- SQL validation trace 对 `errors / warnings / risk_flags` 的可观察性
- repair 成功 / 失败差异的可追踪性

验收：

- `validate_sql` trace metadata 能稳定带上错误、warning 和风险标记
- replay diff 能清晰看到 SQL validation 和执行状态差异

### P2 Replay / Eval 闭环

继续增强：

- 失败 trace 更方便物化为 eval case 或 example
- replay 后更明确展示分类、计划、SQL、执行差异
- 管理端更容易筛选失败样本

验收：

- 真实问题进入后，能按 `trace_id -> materialize-case -> replay` 闭环
- 管理端和日志里都能看到 prompt context summary

### P3 文档与边界治理

继续保持：

- 文档明确当前是 LLM-first，不回退到规则主驱动
- 文档明确不能天然覆盖全部真实场景
- 文档明确不能靠无限加 prompt 解决所有问题

验收：

- `README.md`
- `TEXT2SQL_ARCHITECTURE.md`
- `REAL_SCENARIO_DEBUG_GUIDE.md`
- `REAL_DATA_TUNING_PLAYBOOK.md`

## 4. 不做事项

当前阶段不做：

- 重新引入本地 SQL 模板生成器
- 创建真实数据库额外分析对象作为运行时前置条件
- 为单个假问题写业务 SQL 分支
- 在没有真实样本时接入重型向量库
- 承诺覆盖全部长尾业务问题

## 5. 当前优先执行项

1. 继续补合成 eval case
2. 继续增强 replay diff 和 validator 观测
3. 继续优化 prompt 预算和上下文选择
4. 在 CI 中稳定跑 `compileall`、前端 build 和离线回归
