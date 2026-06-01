# 调试手册

这份手册只回答一个问题：一条真实业务问题答错时，应该先看哪里、怎么定位、怎么把修复沉淀成长期资产。

## 基本原则

- 先复现，再看 `trace_id`，不要凭感觉改 prompt 或规则。
- 先判断错在哪一层：QuestionContext、Retrieval、SQL Prompt、SQL Validator、Execution、Workspace。
- 准确率修复优先沉淀到 `semantic/tables.json`、`semantic/business_knowledge.json`、`examples/nl2sql_examples.template.json`、`semantic/join_patterns.json`、retrieval、prompt 或 validator。
- `context_summary`、`SqlGenerationContext` 和 `evidence_context` 是 SQL 生成的主要可观测输入；业务约束应来自语义资产、检索证据和 few-shot。
- 修完后 replay 原 trace；高价值问题再物化成 eval case 或 example。

## 5 分钟排查

1. 在工作台或 `POST /api/chat/query/stream` 复现问题。
2. 记录 `session_id` 和 `trace_id`。
3. 打开 `GET /api/chat/sessions/{session_id}/workspace`。
4. 先看 `question_context.decision`、`question_context.effective_question`、`retrieval.hit_count_by_source`、`sql_validation.valid`、`execution.status`、`answer.status`。
5. 再看 `GET /api/chat/traces/{trace_id}`、`GET /api/chat/traces/{trace_id}/retrieval`、`GET /api/chat/traces/{trace_id}/sql-audit`。
6. 修复后执行 `POST /api/admin/runtime/query-logs/{trace_id}/replay`。

最短分流：

- 没理解用户问题或追问：看 QuestionContext。
- 关键表、知识或样例没进上下文：看 Retrieval。
- SQL 结构、字段、时间、口径错：看 SQL Prompt 和语义资产。
- SQL 被拦或没拦住：看 SQL Validator。
- SQL 对但结果错：看 Oracle 数据、时间、版本、过滤条件。
- 后端结果对但界面错：看 Workspace 和 response restore。

## 常用入口

用户侧：

- `POST /api/chat/query/stream`
- `POST /api/chat/query`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`

管理员侧：

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`
- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`

## Workspace

`GET /api/chat/sessions/{session_id}/workspace` 是前端恢复会话的主入口，返回：

- `messages`
- `state`
- `latest_response`
- `latest_trace`
- `latest_sql_audit`
- `latest_query_logs`
- `trace_artifacts`

判断方式：

- `messages` 正确但 `latest_response` 错：查 response restore 和 workspace 聚合。
- 详情面板 trace 与消息上的 `trace_id` 不一致：查 query log、trace artifact 关联。
- 某轮结果缺 SQL audit 或 retrieval：查该轮 runtime 落库是否失败。
- 后端 trace 正确但前端显示错：优先查 workspace 响应和前端 `activeTraceId`。

## 分层排查

### QuestionContext

先看：

- `question_context.context_relation`
- `question_context.decision`
- `question_context.effective_question`
- `question_context.semantic_brief`
- `question_context.subject_domain`
- `question_context.clarification_question`

典型问题：

- 追问没有补全成完整问题。
- `semantic_brief` 描述了上一轮对象。
- 信息足够却返回 `clarification_needed`。
- 非业务问题没有被识别为 `invalid` 或 `chat`。

优先修：

- `semantic/business_knowledge.json`
- `semantic/tables.json`
- `PromptBuilder.build_question_context_prompt`
- `QuestionContextService` 输出兜底和字段校验

### Retrieval

先看：

- `retrieval_terms`
- `retrieval_channels`
- `hit_count_by_source`
- `hit_count_by_channel`
- top hits 的 `source_type`、`source_id`、`score`、`matched_features`

检索来源包括 `example`、`knowledge`、`table_schema`、`join_pattern` 和 `vector`。

典型问题：

- 关键表结构没命中。
- 业务知识存在但没进入 prompt。
- 样例或 join pattern 没进 top hits。
- 向量索引未预热或同步失败。

优先修：

- `semantic/tables.json`
- `semantic/business_knowledge.json`
- `examples/nl2sql_examples.template.json`
- `semantic/join_patterns.json`
- `RetrievalService`
- `PromptBuilder`

向量状态看 `GET /api/admin/runtime/status` 里的 `vector_retrieval` 和 `retrieval_corpus`。如果启用了向量检索但索引未就绪，请执行 `POST /api/admin/runtime/vector/prewarm`。

### SQL Prompt

先看 trace 中 `build_sql_prompt` 的 `context_summary`：

- `selected_sources`
- `table_schemas_count`
- `business_knowledge_entry_ids`
- `retrieved_example_ids`
- `join_pattern_ids`
- `time_resolution_count`

再看生成的 `sql`。

典型问题：

- SQL 使用不存在的表或字段。
- 逻辑字段名直接进入 SQL。
- 时间字段格式不匹配真实存储。
- 对比类问题没有先聚合再 join。
- 关键业务公式或默认口径没有进入 prompt。

优先修：

- 表字段说明和 `time_fields.format`。
- 业务知识中的公式、默认口径和禁忌。
- 高质量 few-shot 样例。
- join pattern。
- `PromptBuilder` 的上下文选择和压缩。

### SQL Validator / Repair

先看：

- `sql_validation.valid`
- `sql_validation.errors`
- `sql_validation.warnings`
- `sql_validation.risk_flags`

典型问题：

- 合理 SQL 被误拦。
- 不安全 SQL 没被拦。
- repair 后 SQL 变差。
- validator 的表字段认知和 metadata 不一致。

优先修：

- `SqlValidator`
- `SqlAstValidator`
- `semantic/tables.json`
- SQL 生成约束

Repair 是通用纠错，不负责弥补业务知识缺失。业务理解错时应修语义资产、检索或 prompt。

### Execution

先看：

- `execution.status`
- `row_count`
- `columns`
- `rows`
- `elapsed_ms`
- `errors`

典型问题：

- `db_error`：检查 Oracle 语法、字段、权限和连接。
- `timeout`：检查过滤条件、join 粒度和结果限制。
- `empty_result`：检查时间、版本、枚举值、真实数据是否为空。
- 结果能出但业务不对：对照真实表数据和业务口径。

### Answer / Workspace

如果 SQL 和执行结果都对，但用户看到的状态或详情不对，看：

- `answer.status`
- `answer.summary`
- `workspace.latest_response`
- `workspace.latest_trace`
- `workspace.trace_artifacts`
- `sql_audit`

这类问题通常是响应构造、消息保存或工作台恢复问题，不是 SQL 生成问题。

## LLM Cache

`LLMClient` 默认启用进程内 prompt cache：

- `LLM_CACHE_TTL_SECONDS`，默认 `300`
- `LLM_CACHE_MAX_ENTRIES`，默认 `256`

排查复现抖动时，可以临时把 `LLM_CACHE_TTL_SECONDS=0` 或重启服务。Admin runtime status 的 `llm.metrics` 可查看 `requests`、`provider_calls`、`cache_hits`、`prompt_chars` 和 `response_chars`。

## Replay / Materialize / Eval

Replay 用于验证修复是否生效：

- `POST /api/admin/runtime/query-logs/{trace_id}/replay`

Materialize case 用于沉淀回归样本：

- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`

Materialize example 用于沉淀可进入 prompt 的 few-shot：

- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`

Eval 用于批量验收：

- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/run`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`

样例必须是真实问题或真实 trace，SQL 和业务结果都经过人工确认。

## Demand 横表专项

`p_demand` / `v_demand` 这类横表问题重点检查：

- `MONTH` 是起始月份，不是所有需求列的实际月份。
- `REQUIREMENT_QTY`、`NEXT_REQUIREMENT`、`LAST_REQUIREMENT`、`MONTH4~7` 是偏移列。
- “最新 N 版”要先确定版本集合。
- “需求最多的 fgcode”要先按 `FGCODE` 聚合再排序。
- 时间过滤必须和字段真实格式一致。

优先通过业务知识、样例、表字段说明、prompt 和 validator 修复。

## 提交前检查

- 复现问题有 `trace_id`。
- 已定位到具体层，而不是泛泛修改 prompt。
- JSON 资产通过语义配置检查：`python3 backend/domain_config_lint.py`。
- 修复后 replay 原 trace。
- 高价值真实问题已补 eval case。
