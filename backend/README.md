# Backend

## 运行

安装依赖：

```bash
cp env.example .env
pip install -r backend/requirements.txt
```

启动服务：

```bash
uvicorn backend.app.main:app --reload --app-dir .
```

## 配置读取

- 终端日志默认输出到 stdout
- 日志级别通过 `LOG_LEVEL` 控制，默认 `INFO`
- 每条日志会附带 `request_id` 和 `trace_id`
- 优先读取仓库根目录 `.env`
- 业务查询库读取 `BUSINESS_DATABASE_URL`
- 运行时库读取 `RUNTIME_DATABASE_URL`
- 未显式配置 `RUNTIME_DATABASE_URL` 时，会基于业务库连接自动派生并使用 `manager` 数据库
- 可通过 `RUNTIME_DATABASE_NAME` 修改默认运行时数据库名
- LLM 模型名通过 `LLM_MODEL` 配置
- 向量检索默认使用 `VECTOR_RETRIEVAL_PROVIDER=siliconflow`
- 默认向量模型为 `VECTOR_MODEL=Qwen/Qwen3-Embedding-8B`
- 默认向量维度为 `VECTOR_DIMENSIONS=1024`
- `ENABLE_CHITCHAT_MODE=true` 且当前用户拥有 `chitchat` 角色时，问候/闲聊/无关问题会返回闲聊回复而不是 `invalid`；默认 `false`
- LLM 不可用、调用失败或返回非法 JSON / SQL 时，请求会直接报错，不再静默降级

## 当前范围

当前后端实现的是“LLM-first 的 Text2SQL 主链路 + SQL 治理 + runtime 会话工作台支撑”：

- 加载真实表结构描述、结构化业务知识和辅助语义配置
- `semantic/domain_config.json` 是辅助语义配置的 manifest 入口，实际内容由 `semantic/domain_config/` 下的分片合并得到
- `semantic/join_patterns.json` 用于维护稳定的多表 join 经验，并参与 retrieval / prompt 注入
- 进行语义解析、问题分类和 relevance guard
- 生成 Query Plan 作为 LLM SQL 生成约束
- 由 LLM 直接基于真实表和业务知识生成 MySQL SQL
- PromptBuilder 只选择当前 Query Plan 相关表结构、知识块和少量真实 few-shot，避免 prompt 膨胀
- PromptBuilder 会把命中的 `retrieved_examples`、`business_notes` 和 `join_patterns` 一起带入 SQL prompt
- 对 `oms_inventory` 的常规库存问题，如果用户只说“OMS库存/库存”而没有显式指定 `glass`、`panel` 或具体库龄段，当前默认同时返回 `glass_qty` 和 `panel_qty` 两套口径；只有明确问库龄时才应使用 `ONE_AGE_panel_qty` 到 `EUGHT_AGE_panel_qty`
- SQL 校验器做只读、安全、表字段范围、时间/版本、LIMIT 和风险治理
- SQL 校验或执行失败时，触发一次 LLM SQL repair
- 生成下一轮 `session_state`
- 提供会话仓库、workspace 聚合接口、trace 恢复和 response snapshot
- 提供查询日志、SQL 审计、反馈、replay、eval case 和管理接口
- 检索层当前走 `hybrid retrieval` 方向：关键词 / 向量 / 结构化重排联合召回，规则不再承担过强的 few-shot 门控职责

当前阶段的明确边界：

- 不再要求数据库预建额外分析对象
- `business_knowledge.json` 只参与 prompt 上下文选择，不参与本地 SQL 拼接
- 对横表和复杂口径，优先通过 prompt / retrieved examples / business knowledge / repair loop 驱动 LLM 生成 SQL，再由 validator 治理
- `GET /api/chat/sessions/{session_id}/workspace` 是前端会话恢复的主入口
- `POST /api/chat/query/stream` 是前端默认提问入口；通过 SSE 推送 `accepted`、`planning`、`sql_generation`、`execution`、`completed` 等阶段事件

当前权限模型也需要单独说明：

- 当前没有独立的 permission bitset 或 ACL 表
- 权限本质上是 `users -> user_roles -> roles` 这层角色名集合
- 当前内置且有明确语义的角色主要是 `admin`、`viewer`、`chitchat`
- `chitchat` 不是数据权限，只控制用户在 `ENABLE_CHITCHAT_MODE=true` 时能否收到闲聊回复
- 运行时访问控制主要落在 `admin` 角色校验，以及 session / trace 归属校验

## Runtime 存储与升级

运行时数据默认落到运行时数据库，包括：

- 用户、角色和 `user_roles`
- 会话、消息和状态快照
- query log、trace、SQL audit、feedback
- evaluation runs

运行时表定义见 [sql/runtime_store.sql](../sql/runtime_store.sql)。

首次启动时，服务会尝试：

- 建库
- 建表
- 补增量列
- 补常用索引

### 老 runtime 库升级提示

如果你复用的是旧 runtime 库，且运行账号没有 `ALTER TABLE` 权限，启动后可能不会自动补齐新列。常见症状是启动或登录时报：

```text
Unknown column '...'
```

这说明 runtime 表结构还是旧版本。处理方式：

1. 优先用有 `CREATE/ALTER` 权限的账号重启服务，让 `RuntimeStoreInitializer` 自动补表结构。
2. 如果运行账号不允许改表，就手动执行 [sql/runtime_store.sql](../sql/runtime_store.sql)，并补齐当前增量列：
   - `query_logs.plan_risk_level`
   - `query_logs.plan_risk_flags_json`
   - `query_logs.sql_risk_level`
   - `query_logs.sql_risk_flags_json`
   - `sql_audit_logs.plan_risk_level`
   - `sql_audit_logs.plan_risk_flags_json`
   - `sql_audit_logs.sql_risk_level`
   - `sql_audit_logs.sql_risk_flags_json`

## API

### Health

- `GET /health`

### Semantic

- `GET /api/semantic/summary`
- `POST /api/semantic/retrieve-preview`

### Auth

- `GET /api/auth/bootstrap-status`
- `POST /api/auth/bootstrap-admin`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/change-password`

### Admin Metadata

- `GET /api/admin/metadata/overview`
- `GET /api/admin/metadata/documents`
- `GET /api/admin/metadata/documents/{name}`
- `PUT /api/admin/metadata/documents/{name}`
- `POST /api/admin/metadata/reload`

### Admin Examples / Trace / Feedback

- `GET /api/admin/examples`
- `POST /api/admin/examples`
- `PUT /api/admin/examples/{example_id}`
- `POST /api/admin/examples/bulk`
- `GET /api/admin/traces`
- `GET /api/admin/traces/{trace_id}`
- `GET /api/admin/feedbacks`
- `GET /api/admin/feedbacks/summary`

### Admin Runtime

- `GET /api/admin/runtime/status`
- `GET /api/admin/runtime/sessions`
- `GET /api/admin/runtime/sessions/{session_id}/history`
- `GET /api/admin/runtime/sessions/{session_id}/snapshots`
- `GET /api/admin/runtime/query-logs`
- `GET /api/admin/runtime/query-logs/risk-summary`
- `POST /api/admin/runtime/retention/purge`
- `GET /api/admin/runtime/query-logs/{trace_id}`
- `GET /api/admin/runtime/query-logs/{trace_id}/retrieval`
- `GET /api/admin/runtime/query-logs/{trace_id}/sql-audit`
- `POST /api/admin/runtime/query-logs/{trace_id}/replay`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
- `POST /api/admin/runtime/query-logs/{trace_id}/materialize-example`

说明：

- `examples/nl2sql_examples.template.json` 现在默认可以为空。
- 在线样例只应从真实调试链路通过 `materialize-example` 物化进入，不再手写假设样例。
- example 会参与 RetrievalService 检索，并在命中时以 `retrieved_examples` 形式进入 SQL prompt。
- `materialize-example` 或 examples 管理接口写入后会立即刷新 retrieval 索引；当前不需要重启服务才能让新样例生效。
- `eval/evaluation_cases.json` 也只应保留真实问题或真实 trace 物化出的回归样本，不再维护假设 case。

### Admin Users / Roles

- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PUT /api/admin/users/{user_id}`
- `POST /api/admin/users/{user_id}/reset-password`
- `DELETE /api/admin/users/{user_id}`
- `GET /api/admin/roles`
- `PUT /api/admin/roles/{role_name}`

说明：

- `GET /api/admin/roles` 返回值会把内置角色说明和数据库里的自定义角色合并起来
- 管理员可以直接通过用户编辑接口授予或移除 `chitchat` 角色
- `viewer` 是基础查询角色，`admin` 控制管理台访问，`chitchat` 只控制闲聊回复能力

### Admin Eval

- `GET /api/admin/eval/cases`
- `POST /api/admin/eval/cases`
- `POST /api/admin/eval/cases/{case_id}/replay`
- `GET /api/admin/eval/runs`
- `GET /api/admin/eval/summary`
- `POST /api/admin/eval/run`

### Chat / Sessions

- `POST /api/chat/sessions`
- `GET /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}`
- `PUT /api/chat/sessions/{session_id}/status`
- `DELETE /api/chat/sessions/{session_id}`
- `GET /api/chat/history/{session_id}`
- `GET /api/chat/state/{session_id}`
- `GET /api/chat/sessions/{session_id}/workspace`
- `GET /api/chat/snapshots/{session_id}`
- `POST /api/chat/query`
- `POST /api/chat/query/stream`
- `POST /api/chat/feedback`
- `GET /api/chat/feedbacks`
- `GET /api/chat/feedbacks/summary`
- `GET /api/chat/query-logs`
- `GET /api/chat/traces/{trace_id}`
- `GET /api/chat/traces/{trace_id}/retrieval`
- `GET /api/chat/traces/{trace_id}/sql-audit`
- `GET /api/chat/traces/{trace_id}/export`

### Query

- `POST /api/query/classify`
- `POST /api/query/plan`
- `POST /api/query/plan/validate`
- `POST /api/query/sql`
- `POST /api/query/execute`

说明：

- `POST /api/query/sql` 现在建议同时传 `question` 或 `query_intent`，这样 retrieval 命中的 example / knowledge 更接近主聊天链路
- 如果只传 `query_plan`，系统仍可生成 SQL，但 retrieval 只能基于 `query_plan` 反推一个简化意图

## 示例

```bash
curl -X POST http://127.0.0.1:8000/api/query/classify \
  -H "Content-Type: application/json" \
  -d '{"question":"查询2026年4月CELL工厂计划投入量"}'
```

## 当前调试主路径

推荐按这条路径调问题；更完整的调试与联调说明见 `../DEBUG_PLAYBOOK.md`：

1. 在前端工作台或 `POST /api/chat/query/stream` 复现问题
2. 用 `GET /api/chat/sessions/{session_id}/workspace` 看消息、状态、`latest_response` 和 `trace_artifacts`
3. 看 `GET /api/chat/traces/{trace_id}`、`/sql-audit`、`/retrieval`
4. 对历史问题优先走 `POST /api/admin/runtime/query-logs/{trace_id}/replay`
5. 有代表性的失败样本再物化为 eval case 或 example

## Eval / Replay

当前不再维护离线 planner-only 回归脚本。`eval/evaluation_cases.json` 的定位是：

- 在线 `eval` 的 case 源
- runtime `replay` 的补充回归样本
- 只保留真实问题或真实 trace 物化出的 case

推荐入口：

1. 在真实链路复现问题并拿到 `trace_id`
2. 优先走 `POST /api/admin/runtime/query-logs/{trace_id}/replay`
3. 对稳定且有代表性的真实问题，再走 `POST /api/admin/runtime/query-logs/{trace_id}/materialize-case`
4. 通过管理接口 `POST /api/admin/eval/run` 批量执行当前 case 集

语义层配置 lint：

```bash
python3 backend/domain_config_lint.py
```

说明：

- `eval/evaluation_cases.json` 仍然保留，但用于在线 eval / replay，不再作为离线回归脚本输入
- LLM intent、SQL 生成、SQL 校验和执行结果应优先在 live / replay / eval 链路验证
- 如需新增 case，优先从真实 trace 物化，而不是手写假设样本

### Example 约束

`examples/nl2sql_examples.template.json` 当前建议遵守下面规则：

- 只保留真实问题、真实 trace、且 SQL 与业务结果都人工确认过的样例
- `coverage_tags` 建议至少包含 `real`、业务域和关键口径标签
- `result_shape` 建议使用结构语义，而不是随意命名：
  - 单维分组：直接写维度名，例如 `biz_month`、`stage_product_id`
  - 多维分组：使用 `_by_` 连接，例如 `factory_by_biz_month`
  - 无维度但有指标：`metric_only`
- 优先通过 `materialize-example` 生成样例，再按需要补充 `coverage_tags` 和 `notes`
- example 被命中后会进入 SQL prompt，因此保留下来的样例应能代表一类稳定问法，而不是单次偶然修通结果

## 相关阅读

- [TEXT2SQL_ARCHITECTURE.md](../TEXT2SQL_ARCHITECTURE.md)
- [DEBUG_PLAYBOOK.md](../DEBUG_PLAYBOOK.md)
- [frontend/README.md](../frontend/README.md)

## 目录结构

- `app/api/routes`：HTTP 路由层
- `app/core`：应用装配、settings、异常处理
- `app/models`：请求、响应、会话、检索、追踪、workspace 模型
- `app/repositories`：运行时数据库仓库、metadata 仓库
- `app/services`：语义解析、分类、规划、执行、会话、审计、prompt、LLM、answer、evaluation、auth
