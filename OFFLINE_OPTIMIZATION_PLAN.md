# 无真实数据阶段优化计划

## 1. 目标

在没有真实数据和真实问题的阶段，不能证明业务准确率，但可以先把工程基础打牢：

- LLM SQL 生成上下文可控
- 失败能定位到具体层
- 合成问题能覆盖主要交互形态
- validator 能兜住明显风险
- 真实问题到来后能快速沉淀为 replay / eval case

## 2. 执行顺序

### P0 Prompt 上下文治理

- SQL prompt 只带 Query Plan 命中的真实表结构
- `readme.txt` 按业务片段选择，不全量进入 prompt
- few-shot 按场景进入 prompt，不全局注入
- trace 里记录本次 prompt 选了哪些表、业务说明长度、是否使用 few-shot

验收：

- `build_sql_prompt` payload 带 `context_budget` 和 `context_summary`
- chat trace 的 `build_sql_prompt` 步骤能看到上下文摘要

### P1 合成评测覆盖

补充不依赖真实数据的合成 case：

- 多轮追问时间替换
- 多轮追问版本替换
- 需求横表目标月份
- TopN 排序
- 澄清问题
- 无效问题
- 权限过滤模拟

验收：

- `eval/evaluation_cases.json` 有明确 `scenario` 和 `coverage_tags`
- 离线回归能跑分类、计划和权限层

### P2 Validator 和 Repair 可调试性

- validator 错误信息要可读、可被 LLM repair 使用
- SQL validation trace 记录错误、warning、risk flags
- repair 成功/失败要在 trace 中可见

验收：

- `validate_sql` trace metadata 包含 errors / warnings / risk_flags
- repair 结果可从 warnings 或 trace metadata 观察

### P3 Replay / Eval 闭环

- 失败 trace 可物化为 eval case
- replay 后能看到分类、计划、SQL、执行差异
- 管理端能查看最近失败并复跑
- 查询日志能看到 prompt 上下文摘要，包括选中数据源、业务说明长度、few-shot 使用情况
- replay diff 能比较 prompt 上下文是否变化

验收：

- 真实问题进入后，能按 `trace_id -> materialize-case -> replay` 闭环
- 管理端最近查询日志展示 prompt context summary
- 复跑结果展示 prompt context 是否变化

### P4 文档和使用边界

- 文档明确不能天然覆盖所有真实场景
- 文档说明真实场景覆盖依赖样本矩阵和持续回归
- 文档说明不能通过无限加 prompt 解决所有问题

验收：

- `REAL_SCENARIO_DEBUG_GUIDE.md`
- `REAL_DATA_TUNING_PLAYBOOK.md`
- `TEXT2SQL_ARCHITECTURE.md`

## 3. 不做事项

当前阶段不做：

- 重新引入本地 SQL 模板生成器
- 创建真实数据库 semantic view
- 为单个假问题写业务 SQL 分支
- 在没有真实样本时接入重型向量库
- 承诺覆盖全部长尾业务问题

## 4. 当前优先执行项

1. Prompt 上下文摘要进入 payload 和 trace
2. 补合成 eval case
3. 增强 SQL validation trace metadata
4. 更新 README 文档入口
5. 跑 `compileall`、前端 build、离线回归
