# 后端差距分析

## 1. 当前状态

后端主链路已经从“语义层/模板优先”切到 LLM-first：

- `POST /api/chat/query` 使用 LLM 直接生成 SQL
- `POST /api/query/sql` 使用同一套 LLM-first SQL prompt 和 validator
- `tables.json` 和 `readme.txt` 是 SQL 生成主上下文
- semantic view 草案和管理接口已移除；semantic view 配置仅作为 legacy 辅助语义材料保留
- 本地 `sql_generator.py` 已移除，避免旧模板路径继续误导主链路

当前系统更像可联调的 alpha/beta 后端，不是最终生产治理平台。

## 2. 仍需完善

### 2.1 LLM SQL 稳定性

当前已能让 LLM 基于真实表生成 SQL，但仍需要真实问题集持续验证：

- 横表需求字段展开是否稳定
- 最新版本、目标月份、TopN、聚合维度是否稳定
- CTE、GROUP BY、ORDER BY、LIMIT 是否符合业务预期
- repair 是否能修复字段名、来源、过滤条件和语法错误

优先改进点：

- 完善 `tables.json` 字段说明
- 完善 `readme.txt` 业务口径
- 补真实 few-shot 示例
- 增强 SQL validator 的误拦截和漏拦截治理

### 2.2 Query Plan 约束质量

Query Plan 现在是 LLM SQL 的约束输入，不再是 SQL 模板输入。仍需完善：

- 主题域和候选表选择
- 时间、版本、维度、排序和 limit 提取
- 追问继承和上下文覆盖
- 权限过滤注入后的计划一致性

目标不是把所有场景写死，而是给 LLM 更稳定的边界。

### 2.3 SQL 治理

当前 validator 已有只读、来源、LIMIT、权限过滤、CTE 识别等能力，但还需要继续增强：

- 更精确的字段级校验
- 更强的 join 风险识别
- 更准确的时间/版本过滤校验
- 更好的错误信息，方便 LLM repair
- 慢查询、高风险扫描和大结果集治理

### 2.4 真实检索和示例库

检索现在是辅助上下文，不应重新变成主决策来源。仍需完善：

- 高质量真实 example 的沉淀
- 失败 trace 到 example/eval case 的闭环
- 真实 embedding provider 或向量库接入
- 检索结果对 SQL prompt 的可解释性

### 2.5 运行时治理

已有会话、审计、反馈、评测和管理端接口，但生产级治理还不足：

- API 限流
- 慢查询告警
- 异常告警
- 运行时表索引和归档策略
- replay 差异对比
- 下载和敏感字段审计

## 3. 不再作为当前缺口

以下内容不再视为当前主路径缺口：

- 把 semantic view 全部落成真实数据库对象
- 继续扩展本地 SQL 模板生成器
- 为每个业务问法新增 Python 分支
- 依赖 `semantic_demand_unpivot_view` 才能回答需求横表问题

这些能力只有在真实联调证明必要时，才作为局部优化重新评审。

## 4. 后续优先级

1. 用真实问题验证 LLM-first SQL 的执行正确性
2. 把失败问题沉淀到 `readme.txt`、`tables.json`、few-shot 和 eval case
3. 收紧 SQL validator 和 repair loop
4. 再考虑检索增强和平台治理
5. 最后评估是否需要把少量稳定复杂逻辑落成数据库视图
