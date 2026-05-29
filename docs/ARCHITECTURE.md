# Text2SQL 架构说明

这份文档描述目标架构：系统从结构化规则驱动的 Text2SQL 收敛为**文本上下文驱动的 Text2SQL**。

核心原则是：程序不再负责理解业务问题、编译业务合同或维护结构化追问继承规则。程序只负责组织上下文、检索可信资料、约束 LLM 使用真实 schema、校验 SQL 安全、执行和审计。

---

## 1. 总览

主链路是：

```text
用户问题 + 会话文本摘要
  -> 追问改写成完整问题
  -> 检索表结构 / 业务知识 / SQL 示例 / join 经验
  -> LLM 直接生成 Oracle SQL
  -> SQL 安全和物理 schema 校验
  -> 执行 SQL
  -> 生成答案、更新会话摘要、落库审计
```

这个架构不是：

- 先把问题解析成 `metrics / dimensions / filters` 再编译 SQL 的规则系统
- 依赖 `QueryIntent`、`QueryPlan`、`domain_config.metrics`、`query_profiles` 决定业务口径的系统
- 通过本地 `context_delta` 合并上一轮结构化状态来处理追问的系统
- 命中某条 Python 规则后套 SQL 模板的系统

业务理解、追问补全、字段选择、计算口径选择、SQL 组织，交给：

```text
LLM + 当前完整问题 + 会话摘要 + 业务知识文本 + SQL 示例 + 真实表结构
```

本地代码保留强工程边界：

- 认证和会话
- 请求取消和 SSE 进度
- metadata 读取和 reload
- retrieval corpus 构建
- SQL 只读、安全、表字段、方言和风险校验
- 业务库执行
- runtime 审计、trace、workspace 恢复

---

## 2. 数据资产

主链路直接依赖的资产只保留这些：

- `semantic/tables.json`
  - 真实表结构、字段说明、关系、时间字段格式
  - 是 SQL 生成和 SQL 校验的物理 schema 边界
- `semantic/business_knowledge.json`
  - 自然语言业务知识
  - 承载计算口径、默认规则、禁忌、字段使用说明
- `examples/nl2sql_examples.template.json`
  - 人工确认过的自然语言到 SQL 样例
  - 承载 SQL 组织方式、聚合方式、join 方式、Oracle 写法
- `semantic/join_patterns.json`
  - 稳定多表 join 经验
- `sql/runtime_store.sql`
  - runtime MySQL 表结构
- `sql/oracle_business_schema.sql`
  - 业务 Oracle 表结构

不再作为主链路业务决策来源的资产：

- `semantic/domain_config/metrics/*`
- `semantic/domain_config/query_profiles/*`
- `semantic/domain_config/base/domain_inference.json`
- `semantic/domain_config/base/extractors.json`
- 结构化 `prompt_assets` 中面向 intent/plan 的规则

这些文件如果暂时存在，只能是迁移期遗留，不应继续新增业务规则。

---

## 3. 核心对象

### 3.1 Session

`session` 是一段会话容器，保存消息和一份文本型会话记忆。

目标状态里，session memory 不再以结构化 `metrics / filters / dimensions` 为主，而是以自然语言表达：

```json
{
  "conversation_summary": "用户先查询了 2024 年 3 月各品牌销售额，随后追问按区域拆分。最近一次查询返回了按品牌和区域分组的销售额。",
  "recent_turns": [
    "用户：查询 2024 年 3 月各品牌销售额；助手：返回按品牌统计的销售额。",
    "用户：那按区域拆一下呢？；改写后：查询 2024 年 3 月按品牌和区域拆分的销售额；助手：返回按品牌和区域分组的销售额。"
  ]
}
```

### 3.2 QuestionContext

`QuestionContext` 是每轮请求进入 SQL 生成前的文本上下文。
它由一次受约束的 LLM 调用生成，不由本地 Python 规则推断。

```python
class QuestionContext(BaseModel):
    original_question: str
    effective_question: str
    context_relation: Literal["new", "follow_up", "ambiguous"]
    decision: Literal["answerable", "clarification_needed", "invalid"]
    conversation_summary: str = ""
    semantic_brief: str = ""
    clarification_question: str | None = None
```

它只回答三个问题：

- 当前问题是否可回答
- 当前问题是否依赖上下文
- 如果是追问，完整问题是什么

它不输出业务结构化合同，不输出 SQL。本地代码只负责提供会话摘要和最近轮次、校验 JSON shape、根据 `decision` 决定是否继续进入 SQL 生成。

这个阶段和 SQL 生成阶段是两个不同的 LLM 任务：

- `question_context_generation`：只做追问识别、问题改写和澄清判断
- `sql_generation`：只基于完整问题、检索上下文和表结构生成 SQL

不能把这两个任务合并成“一次直接生成 SQL”，否则追问、澄清、审计和 retrieval query 都会失去稳定输入。

### 3.3 RetrievalContext

`RetrievalContext` 是 SQL 生成可用的证据集合。

```python
class RetrievalContext(BaseModel):
    table_schemas: dict[str, Any]
    business_knowledge: list[str]
    examples: list[dict]
    join_patterns: list[str]
    retrieval_hits: list[dict]
```

检索输入是文本：

- `effective_question`
- `semantic_brief`
- `conversation_summary`
- 最近几轮文本

检索输出仍然可以带 score、source、channel 供 trace 使用，但 SQL prompt 消费的是表结构、知识、样例和 join 说明。

### 3.4 SqlGenerationPrompt

SQL prompt 是主生成上下文，内容应接近：

```json
{
  "task": "oracle_text2sql",
  "question": "查询 2024 年 3 月按品牌和区域拆分的销售额。",
  "conversation_summary": "上一轮用户查询了 2024 年 3 月各品牌销售额，结果按品牌展示。",
  "semantic_brief": "用户想在上一轮基础上增加区域维度。",
  "available_tables": {
    "SALES_ORDER": {
      "description": "...",
      "columns": ["BIZ_DATE", "BRAND_NAME", "REGION_NAME", "SALES_AMOUNT"]
    }
  },
  "business_knowledge": ["销售额默认使用 SALES_AMOUNT。"],
  "examples": [{"question": "查询 2024 年 3 月各品牌销售额", "sql": "..."}],
  "join_patterns": [],
  "oracle_rules": [
    "只生成 SELECT 或 WITH ... SELECT",
    "只能使用 available_tables 中的表和字段",
    "必须使用 Oracle 语法",
    "必须限制结果行数",
    "不要解释，不要 markdown"
  ]
}
```

prompt 不应再把 `query_contract.metrics / dimensions / filters` 当硬约束。

### 3.5 Trace

`trace` 是单次请求的完整执行记录。它应记录：

- `original_question`
- `effective_question`
- `context_relation`
- `semantic_brief`
- retrieval 命中
- SQL prompt 摘要
- 生成 SQL
- SQL 校验结果
- 执行结果摘要
- answer 摘要

Trace 是排查对象，不是下一轮业务理解的结构化状态来源。

---

## 4. 追问处理

追问不通过结构化状态继承处理，不使用 `context_delta` 合并上一轮 filters、metrics 或 dimensions。

追问处理由 LLM 完成，只有一个目标：**把当前问题改写成不依赖上下文也能理解的完整问题**。

这一阶段的输入是：

- 当前用户问题
- 会话摘要
- 最近几轮用户问题、助手结果摘要和上一轮改写问题
- 可选的系统边界说明，例如“只能回答业务数据查询问题”

这一阶段的输出必须是 JSON，且只能包含：

- `decision`
- `context_relation`
- `effective_question`
- `semantic_brief`
- `clarification_question`
- `reason`

不能包含 SQL，不能包含 `metrics / dimensions / filters / calculation_contract` 这类业务结构化合同。

示例：

```text
上一轮：查询 2024 年 3 月各品牌销售额
用户追问：那按区域拆一下呢？
```

`QuestionContextService` 输出：

```json
{
  "decision": "answerable",
  "context_relation": "follow_up",
  "effective_question": "查询 2024 年 3 月按品牌和区域拆分的销售额。",
  "semantic_brief": "用户想在上一轮 2024 年 3 月品牌销售额查询基础上，增加区域维度拆分销售额。",
  "clarification_question": null
}
```

如果上下文不足：

```json
{
  "decision": "clarification_needed",
  "context_relation": "ambiguous",
  "effective_question": "",
  "semantic_brief": "用户使用了指代词，但无法确定指代对象。",
  "clarification_question": "你想基于上一轮的哪个指标或范围继续分析？"
}
```

改写规则：

- 如果是追问，必须输出完整问题
- 只能继承会话摘要或最近轮次中明确出现的信息
- “换成数量”“不要品牌”“只看华东”这类表达要体现为替换、删除或新增条件
- “这个/那个/它”无法确定时，返回澄清
- 不输出 SQL
- 不输出结构化业务合同

后续 retrieval 和 SQL generation 只处理 `effective_question`。

本地代码不尝试用关键词规则判断“是否追问”。即使可以用简单规则做快速提示，也只能作为 prompt 里的辅助观察，不能直接决定上下文继承。

---

## 5. 一次完整请求

### 5.1 路由层

主入口：

- `POST /api/chat/query/stream`
- `POST /api/chat/query`

流式入口职责：

1. 解析请求
2. 解析用户上下文
3. 创建 `trace_id`
4. 订阅进度事件
5. 创建 cancellation token
6. 后台执行 orchestrator
7. 客户端断开时取消请求
8. 通过 SSE 推送阶段事件

### 5.2 Orchestrator

目标主流程：

```text
1. create trace
2. load session memory
3. call LLM to build QuestionContext
4. terminal gate
5. retrieve context
6. build SQL prompt
7. call LLM to generate SQL
8. validate SQL
9. repair SQL if needed
10. execute SQL
11. build answer
12. update conversation memory
13. persist runtime artifacts
14. publish completed / failed
```

其中 `terminal gate` 处理：

- `clarification_needed`
- `invalid`
- 客户端取消
- LLM / metadata / retrieval 的显式失败

如果 `QuestionContext` 阶段返回 `clarification_needed` 或 `invalid`，请求不会进入 retrieval 和 SQL generation。这样可以避免在上下文不明确时强行生成 SQL。

### 5.3 SQL 生成

SQL 生成阶段只传：

- 当前完整问题
- 会话摘要
- 语义摘要
- 检索到的业务知识
- 检索到的 SQL 示例
- 检索到的 join pattern
- 相关表结构和字段说明
- Oracle 语法约束

SQL 生成阶段不再接收结构化 `QueryPlan` 作为业务合同。即使迁移期 API 仍然返回 `query_plan` 字段，它也只能是审计或兼容壳，不能作为 prompt 的硬约束来源。

LLM 返回一条 SQL：

```sql
SELECT
  BRAND_NAME,
  REGION_NAME,
  SUM(SALES_AMOUNT) AS SALES_AMOUNT
FROM SALES_ORDER
WHERE BIZ_DATE >= DATE '2024-03-01'
  AND BIZ_DATE < DATE '2024-04-01'
GROUP BY BRAND_NAME, REGION_NAME
ORDER BY SALES_AMOUNT DESC
FETCH FIRST 200 ROWS ONLY
```

### 5.4 SQL 校验

SQL validator 保留强约束：

- 只允许 `SELECT` 或 `WITH ... SELECT`
- 禁止 DDL / DML / 权限操作 / 存储过程执行
- 只能引用真实表
- 只能引用真实字段
- 必须符合 Oracle 方言
- 必须限制结果行数
- 多表查询必须有明确 join 条件
- 对大扫描、笛卡尔积、无限制结果给出 warning 或 error

SQL validator 不再校验：

- SQL 是否覆盖 `query_plan.metrics`
- SQL 是否覆盖 `query_plan.dimensions`
- SQL 是否覆盖 `query_plan.filters`
- SQL 是否满足本地结构化业务合同

这些检查会把系统重新拉回结构化 plan 主导，应移除。

### 5.5 SQL Repair

repair 是失败恢复路径，不是业务规则补丁。

输入：

- 原始 SQL prompt
- 失败 SQL
- validator 错误
- executor 错误

输出：

- 修正后的一条只读 Oracle SQL

repair 不接收结构化 query plan，不做业务特化分支。

### 5.6 会话记忆更新

每轮结束后更新文本记忆：

```json
{
  "conversation_summary": "用户先查询了 2024 年 3 月各品牌销售额，随后追问按区域拆分；最近一次查询返回了按品牌和区域分组的销售额。",
  "recent_turns": [
    "用户：那按区域拆一下呢？；改写后：查询 2024 年 3 月按品牌和区域拆分的销售额；助手：返回按品牌和区域分组的销售额。"
  ]
}
```

这份记忆是下一轮追问改写的输入。它不是结构化继承合同。

---

## 6. 组件职责

### 6.1 ConversationOrchestrator

负责串联完整请求生命周期。

它不负责业务理解，不编译结构化 plan，不做本地 SQL 模板选择。

### 6.2 QuestionContextService

负责：

- 读取当前问题和 session memory
- 判断 `answerable / clarification_needed / invalid`
- 判断 `new / follow_up / ambiguous`
- 将追问改写成 `effective_question`
- 生成自然语言 `semantic_brief`

它的输出是文本上下文，不是结构化业务合同。

### 6.3 RetrievalService

负责根据文本上下文检索：

- 相关表结构
- 业务知识
- SQL 示例
- join pattern

检索可以内部使用关键词、BM25、向量、source score，但对 SQL 生成输出的是可读上下文。

### 6.4 PromptBuilder

负责把 `QuestionContext + RetrievalContext` 组装成 SQL prompt。

它不负责编译业务规则，不根据本地 metrics/dimensions 生成 SQL shape contract。

### 6.5 LLMClient

负责：

- `question_context_generation`
  - 输入当前问题、会话摘要和最近轮次
  - 输出 JSON 格式的 `QuestionContext`
  - 用于追问改写、澄清和 invalid 判断
- `sql_generation`
  - 输入完整问题、retrieval context、表结构、知识和样例
  - 输出一条只读 Oracle SQL
- `sql_repair`
  - 输入原 SQL prompt、失败 SQL 和错误反馈
  - 输出修正后的一条只读 Oracle SQL

如果 LLM 不可用或返回非法结果，主链路显式失败，不静默降级成规则模板。

### 6.6 SqlValidator

负责安全和物理 schema 边界，不负责业务口径正确性。

### 6.7 SqlExecutor

负责在业务 Oracle 库执行只读 SQL，并返回结果摘要。

### 6.8 ConversationMemoryService

负责把本轮问题、改写问题、结果摘要压缩成下一轮可用的文本记忆。

---

## 7. Runtime 与恢复

runtime MySQL 保存：

- 用户和角色
- session
- messages
- conversation memory
- trace
- query log
- SQL audit
- retrieval log
- feedback
- eval / replay 记录
- vector corpus documents

前端 workspace 恢复接口仍然可以聚合：

- messages
- conversation memory
- latest response
- latest trace
- latest SQL audit
- latest query logs
- trace artifacts

但 workspace 中的“状态”应展示：

- 当前会话摘要
- 最近轮次
- 最近一次 `effective_question`
- 最近一次 SQL 和结果摘要

不再重点展示结构化 query plan。

---

## 8. 向量与检索持久化

向量检索只服务 retrieval，不改变主架构。

corpus 来源：

- examples
- business knowledge
- table schema descriptions
- join patterns

向量持久化位置仍然是 runtime MySQL 的 `vector_corpus_documents`。

应用内存负责实际相似度搜索。metadata reload 后，需要重新同步 corpus 和向量。

如果启用了向量检索但 embedding client 不可用，服务应 fail-fast，而不是静默降级。

---

## 9. 删除和降级边界

应从主链路删除或降级的对象：

- `QueryIntentParser`
  - 删除，或仅作为非常薄的检索关键词辅助，不参与业务决策
- `IntentNormalizer`
  - 删除
- `StructuredIntent`
  - 删除
- `QueryPlanner`
  - 改名/改造为 `QuestionContextService`，不再 build plan
- `QueryPlanCompiler`
  - 删除
- `QueryPlanValidator`
  - 删除，或只保留迁移期 API 壳
- `SemanticRuntime` 的 metrics/entities/query_profiles 业务规则
  - 从主链路移除
- `SessionStateService` 的结构化继承逻辑
  - 改成文本 memory 更新
- `SqlValidator` 中依赖 query plan 的 shape/filter/time/version 校验
  - 移除

可以保留的最小结构化对象：

- 用户、session、message、trace、audit 等 runtime 模型
- SQL validation result
- execution result
- answer status
- retrieval hit metadata

---

## 10. 准确率修复原则

准确率问题不要通过新增 Python 业务规则修。

优先级：

1. 补充或修正 `semantic/tables.json` 字段说明、关系、时间字段
2. 补充 `semantic/business_knowledge.json` 的自然语言业务口径
3. 补充人工确认过的 `examples/nl2sql_examples.template.json`
4. 补充 `semantic/join_patterns.json`
5. 调整 retrieval 排序和上下文预算
6. 调整 SQL prompt 通用约束
7. 调整 SQL validator 的安全和物理边界

不建议：

- 新增本地 metric 规则
- 新增本地 query profile 规则
- 新增结构化 calculation contract
- 新增业务特化 SQL repair 分支
- 新增 SQL 模板

---

## 11. 目标目录形态

目标服务分层可以收敛为：

```text
backend/app/services/
  orchestrator.py
  question_context_service.py
  conversation_memory_service.py
  retrieval_service.py
  prompt_builder.py
  llm_client.py
  sql_validator.py
  sql_executor.py
  answer_builder.py
  session_service.py
  audit_service.py
  runtime_admin_service.py
```

目标模型可以收敛为：

```text
backend/app/models/
  question_context.py
  retrieval.py
  answer.py
  trace.py
  conversation.py
  workspace.py
  auth.py
  feedback.py
```

结构化 intent/plan 相关模型不再作为主链路模型存在。
