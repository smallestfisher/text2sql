# Text2SQL 架构说明：LLM-first

## 1. 当前原则

当前工程的主路径已经切换为 LLM-first：

- `tables.json` 是真实数据库表和字段描述的主要来源
- `readme.txt` 是真实业务关系和查询口径的主要补充
- LLM 直接基于真实表结构、业务说明、Query Plan 和少量 few-shot 生成 MySQL SQL
- Python 规则、语义层、检索和 Query Plan 只做辅助约束，不再承担主要 SQL 拼接职责
- SQL validator / AST validator / permission service 负责安全、只读、来源范围、权限过滤和 LIMIT 治理
- SQL 校验或执行失败时，允许一次 LLM repair，再重新校验
- Prompt 上下文必须经过选择和预算控制，不能把所有 schema、业务说明和示例全量塞给 LLM

这意味着系统不要求真实数据库里存在 `semantic_demand_unpivot_view` 或其他 semantic view。复杂横表逻辑应优先由 LLM 在 SQL 中用 `WITH` CTE 展开，再由校验器治理。

## 2. 端到端链路

一次查询的主链路如下：

1. API 接收自然语言问题和用户上下文
2. 分类器判断新问题、追问、澄清或无效问题
3. 语义解析和检索补充 Query Plan 约束
4. 权限服务把用户数据范围注入 Query Plan
5. PromptBuilder 从 `tables.json`、`readme.txt`、Query Plan、业务提示和示例中选择相关上下文，构造 SQL prompt
6. LLMClient 生成一条只读 `SELECT` 或 `WITH ... SELECT`
7. SqlValidator 校验 SQL 来源、风险、权限过滤、时间/版本条件和 LIMIT
8. 校验失败时，LLMClient 使用原 prompt 和错误信息 repair SQL
9. SqlExecutor 在只读业务库执行 SQL
10. AnswerBuilder 组织结果摘要，审计和会话服务落库

## 3. 各层职责

### 3.1 LLM

LLM 是 SQL 生成主角，负责理解业务问题、选择真实表字段、展开横表、生成聚合、排序、TopN 和 CTE。

LLM 不负责绕过治理。它输出的 SQL 必须经过 validator，失败后只能在原始上下文内 repair。

### 3.1.1 Prompt 上下文选择器

LLM-first 不等于无限扩 prompt。当前 SQL prompt 的上下文选择原则是：

- 只发送 Query Plan 命中的真实表结构；没有命中表时才使用主题域候选表
- `readme.txt` 会被切成业务说明片段，并按当前 subject、tables、metrics、dimensions、filters、version field 打分，只发送相关片段
- 业务说明有字符预算，当前由 `PromptBuilder.BUSINESS_NOTES_MAX_CHARS` 控制
- few-shot 按场景选择；例如需求横表专项示例只在需求域或命中 `p_demand/v_demand` 时进入 prompt
- prompt 中会携带 `context_budget`，方便 trace 和调试时判断本次发送了哪种上下文

后续如果 `readme.txt` 继续增长，应把它拆成结构化知识块或接入检索，而不是继续扩大单次 prompt。

### 3.2 Query Plan

Query Plan 是给 LLM 和 validator 的约束，不是 SQL 模板。它用于表达主题域、候选表、指标、维度、过滤、排序、limit、时间和版本上下文。

如果 Query Plan 不完整，LLM 仍可根据真实 schema 和业务说明生成 SQL；validator 负责拦截明显越界或危险 SQL。

### 3.3 语义层

`semantic/semantic_layer.json` 现在是辅助配置：

- 辅助分类和主题域判断
- 辅助检索和 Query Plan 收敛
- 辅助 validator 判断已知表、字段、权限和风险

语义层不是主 SQL 编译器。不要为了一个业务问题继续在语义层里堆完整 SQL 模板。

### 3.4 Semantic View

Semantic view 只保留为 legacy 辅助能力：

- 可以作为文档化的业务口径参考
- 可以作为未来性能优化或稳定口径的候选落库对象
- 不应作为 chat 和 `/api/query/sql` 的主执行依赖
- 不要求在真实业务库里创建

如果未来确实需要落库 semantic view，应先证明某类 SQL 高频、稳定、复杂且影响性能或治理，再单独评审。

### 3.5 SQL 治理

SQL 治理是 LLM-first 的边界：

- 只允许只读 `SELECT` / `WITH ... SELECT`
- 禁止 DDL/DML 和危险关键字
- 校验引用来源是否在真实表、允许的辅助对象或 CTE 范围内
- 校验 Query Plan 关键过滤、维度、排序、版本和 limit
- 校验权限要求的过滤条件
- 对大范围扫描、无时间条件、多源 join 等风险给出 warning 或 error

## 4. 需求横表处理原则

`p_demand` / `v_demand` 是横向需求表，不能简单理解成 `month = 202604`。

正确方向是让 LLM 根据业务说明生成类似如下逻辑：

- `MONTH` 表示当前版本月
- `REQUIREMENT_QTY` 对应当前月需求
- `NEXT_REQUIREMENT` 对应下一个月需求
- `LAST_REQUIREMENT` 和 `MONTH4` 至 `MONTH7` 按业务说明继续展开
- 对“最新 N 版”先按版本字段取最新 N 个版本，再对目标需求月份选择对应列
- 对“需求最多的 fgcode”按 `FGCODE` 聚合并排序

这类逻辑应体现在 prompt 和 few-shot 中，不应依赖数据库预建 `semantic_demand_unpivot_view`。

## 5. 维护优先级

遇到准确率问题时，优先按这个顺序处理：

1. 修 `tables.json` 字段描述和真实表关系
2. 修 `readme.txt` 业务口径
3. 补高质量 few-shot 示例
4. 修 PromptBuilder 的通用指令
5. 修 SQL validator 的边界和误拦截
6. 最后才考虑新增局部规则

局部规则只能用于分类、约束、校验或安全治理，不能重新变成业务 SQL 生成主路径。

## 6. Token 控制策略

为避免 LLM-first 演变成 token 爆炸，维护时遵守以下规则：

- 不把全量 `tables.json` 放入 SQL prompt
- 不把全量 `readme.txt` 放入 SQL prompt
- 不把所有 few-shot 都放入 SQL prompt
- 新增业务说明时优先写成可被关键词命中的短片段
- 高频失败样例进入 eval/few-shot 前要判断是否具有复用价值
- 如果某类问题需要大量背景知识，优先拆知识块和做检索，而不是提高固定 prompt 上限
