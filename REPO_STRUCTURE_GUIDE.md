# Repository Structure Guide

## Purpose

这份文档用于快速说明当前仓库各个目录、分层和关键文件的职责，方便后续补功能、调规则、查链路时快速定位代码。

当前项目仍处于“测试数据 + 规则持续收敛 + 架构骨架优先”的阶段，因此很多对象更偏脚手架和可迭代设计，不以最终生产实现为前提。

## Root Directories

### `backend/`

后端服务代码与运行说明。Text2SQL 主链路、权限、会话、审计、检索、SQL 生成执行都在这里。

关键文件：

- `backend/README.md`：后端启动方式、环境变量、当前能力范围说明。
- `backend/requirements.txt`：后端 Python 依赖。

### `frontend/`

前端工作台，当前主要提供登录、聊天、SQL/Trace/State 查看和部分管理能力。

关键文件：

- `frontend/README.md`：前端说明。
- `frontend/package.json`：前端依赖与脚本。
- `frontend/vite.config.ts`：Vite 构建配置。
- `frontend/src/`：前端实际代码。

### `semantic/`

语义层配置目录，描述业务域、实体、指标、维度、规则、语义视图等，是当前 planner / retrieval / validator 的核心输入来源。

关键文件：

- `semantic/semantic_layer.json`：语义层主配置文件。

### `sql/`

数据库初始化与语义视图草案 SQL。

关键文件：

- `sql/runtime_store.sql`：运行时库建表脚本，包含会话、审计、反馈、评测等持久化对象。
- `sql/semantic_view_drafts.sql`：语义视图草案，当前用于 logical scaffold，不要求已经是最终生产视图。

### `schemas/`

结构化对象的 JSON Schema，主要用于约束 Query Plan / Session State 的结构边界。

关键文件：

- `schemas/query_plan.schema.json`
- `schemas/session_state.schema.json`

### `examples/`

示例问题库模板，主要用于沉淀示例问法、样例 SQL 或后续 few-shot / eval 来源。

关键文件：

- `examples/nl2sql_examples.template.json`

### `eval/`

评测样本与评测运行相关输入。

关键文件：

- `eval/evaluation_cases.json`：当前评测 case 数据。

### `runtime_data/`

本地运行时数据目录。当前项目已经引入运行时数据库，但这个目录仍保留部分本地/历史数据文件，方便开发阶段调试和兼容。

关键文件：

- `runtime_data/sessions.json`
- `runtime_data/audit_traces.json`
- `runtime_data/feedback_records.json`
- `runtime_data/auth_users.json`
- `runtime_data/evaluation_runs.json`

### Root Markdown Docs

根目录下的多个 `.md` 文件主要承担架构说明、开发计划和缺口分析：

- `TEXT2SQL_ARCHITECTURE.md`：总体架构设计说明。
- `DEVELOPMENT_PLAN.md`：当前阶段开发计划与分批推进事项。
- `BACKEND_GAP_ANALYSIS.md`：后端缺失能力和补齐建议。
- `SEMANTIC_VIEW_SCAFFOLD_PLAN.md`：语义视图脚手架设计说明。
- `ACCURACY_DEBUG_GUIDE.md`：准确率调试指南。
- `TODO_BACKLOG.md`：待办列表。

## Backend Structure

后端核心代码位于 `backend/app/`，整体采用“入口层 -> API 层 -> 容器装配 -> service 编排 -> repository 持久化 -> model 结构定义”的组织方式。

### `backend/app/main.py`

FastAPI 应用入口。

职责：

- 初始化日志系统。
- 创建 FastAPI 应用。
- 注册 middleware。
- 注册异常处理。
- 挂载各路由模块。

### `backend/app/logging_config.py`

日志配置入口。

职责：

- 配置 stdout 日志输出。
- 统一日志格式。
- 使用 `contextvars` 注入 `request_id` / `trace_id`。
- 为后端链路调试提供统一日志上下文。

### `backend/app/api/`

HTTP API 层，负责把外部请求接入到后端容器和服务层。

关键文件：

- `backend/app/api/dependencies.py`：容器获取、当前用户解析、管理员权限依赖。
- `backend/app/api/middleware.py`：请求级 trace / request_id 中间件。

### `backend/app/api/routes/`

接口分组路由目录。

- `health.py`：健康检查。
- `semantic.py`：语义层摘要、检索预览等接口。
- `query.py`：分类、规划、SQL 生成、执行等单步接口。
- `chat.py`：完整聊天编排接口、trace / query log / feedback 相关接口。
- `sessions.py`：会话列表、详情、状态、快照、history 等。
- `auth.py`：管理员初始化、登录、当前用户、改密等。
- `admin.py`：管理员元数据、示例、用户、角色、runtime 状态、评测等接口。

### `backend/app/core/`

应用装配与全局基础设施层。

- `backend/app/core/container.py`：依赖注入容器，负责装配 semantic loader、planner、retrieval、LLM、SQL executor、repositories、orchestrator 等核心对象。
- `backend/app/core/settings.py`：环境变量读取与统一 settings 定义。
- `backend/app/core/error_handlers.py`：全局异常处理和错误日志。
- `backend/app/core/exceptions.py`：统一业务异常定义。

### `backend/app/models/`

结构化数据模型层，使用 Pydantic 描述请求、响应、内部语义对象和运行时对象。

关键文件：

- `backend/app/models/api.py`：对外 API request/response 模型。
- `backend/app/models/classification.py`：语义解析与问题分类模型。
- `backend/app/models/query_plan.py`：Query Plan 主模型，是 planner / compiler / validator / SQL generator 的核心结构。
- `backend/app/models/session_state.py`：多轮上下文状态模型。
- `backend/app/models/retrieval.py`：检索命中与检索摘要模型。
- `backend/app/models/answer.py`：回答结构模型。
- `backend/app/models/trace.py`：审计 trace 结构。
- `backend/app/models/auth.py`：用户、角色、token 上下文。
- `backend/app/models/admin.py`：管理员页元数据摘要模型。
- `backend/app/models/evaluation.py`：评测 run / case 相关模型。
- `backend/app/models/conversation.py`、`backend/app/models/feedback.py`、`backend/app/models/example_library.py`：会话、反馈、示例库对象。

### `backend/app/repositories/`

持久化适配层，负责把审计、会话、反馈、用户、runtime log 等写入数据库或文件仓库。

关键文件：

- `backend/app/repositories/db_session_repository.py`：会话与状态持久化。
- `backend/app/repositories/db_audit_repository.py`：审计 trace 持久化。
- `backend/app/repositories/db_feedback_repository.py`：反馈记录持久化。
- `backend/app/repositories/db_auth_repository.py`：认证用户与角色持久化。
- `backend/app/repositories/db_runtime_log_repository.py`：检索日志、SQL 审计、query log 持久化。
- `backend/app/repositories/db_evaluation_run_repository.py`：评测运行结果持久化。
- `backend/app/repositories/metadata_repository.py`：语义配置、文档、示例等元数据读取。
- `backend/app/repositories/db_repository_utils.py`：repository 公共辅助逻辑。

### `backend/app/services/`

业务服务层，是当前项目最核心的目录。绝大多数 Text2SQL 能力都在这里实现。

#### 1. 语义层与解析

- `backend/app/services/semantic_loader.py`：加载 `semantic_layer.json`，并汇总语义对象状态。
- `backend/app/services/semantic_runtime.py`：把语义层配置转成运行时可查询能力，提供字段解析、语义规则匹配、上下文差异分析等能力。
- `backend/app/services/semantic_parser.py`：基础语义解析能力。

#### 2. 分类与规划

- `backend/app/services/question_classifier.py`：问题分类器。当前已演进为“语义特征分析 + LLM 仲裁”的结构。
- `backend/app/services/query_planner.py`：从问题和 session state 生成初步 Query Plan。
- `backend/app/services/query_plan_compiler.py`：把规划结果补全为可执行计划，补 limit / sort / 视图 / 表等。
- `backend/app/services/query_plan_validator.py`：对 Query Plan 做结构和语义校验。

#### 3. 检索与提示增强

- `backend/app/services/retrieval_service.py`：统一检索入口，汇总规则命中、文档命中、语义视图命中等结果。
- `backend/app/services/vector_retriever.py`：向量检索适配层，目前支持轻量接入和后续扩展。
- `backend/app/services/prompt_builder.py`：构建分类、Query Plan、SQL 等提示词，是当前 LLM 接入的重要枢纽。
- `backend/app/services/llm_client.py`：OpenAI-compatible LLM 客户端，负责调用分类仲裁、规划 hint、SQL hint 等能力。

#### 4. SQL 生成与执行

- `backend/app/services/sql_generator.py`：根据 Query Plan 生成 SQL。
- `backend/app/services/sql_validator.py`：SQL 结构与语义校验。
- `backend/app/services/sql_ast_validator.py`：AST 级只读和安全约束校验。
- `backend/app/services/sql_executor.py`：执行只读 SQL，并返回结构化结果。
- `backend/app/services/database_connector.py`：数据库连接和只读查询封装。

#### 5. 会话、权限与回答

- `backend/app/services/session_service.py`：会话增删改查、消息追加、状态更新。
- `backend/app/services/session_state_service.py`：根据当前 Query Plan 生成下一轮 `session_state`。
- `backend/app/services/permission_service.py`：把用户权限约束映射到 Query Plan / SQL / 执行结果。
- `backend/app/services/policy_engine.py`：权限策略底层逻辑。
- `backend/app/services/answer_builder.py`：把执行结果、校验结果和分类结果整理成最终回答。

#### 6. 编排、审计与后台能力

- `backend/app/services/orchestrator.py`：完整 chat 主链路编排器，串联分类、检索、规划、权限、SQL、执行、answer、trace、runtime log。
- `backend/app/services/audit_service.py`：trace 步骤记录和收口。
- `backend/app/services/metadata_service.py`：管理台元数据、语义文档、示例配置等汇总服务。
- `backend/app/services/runtime_admin_service.py`：运行时状态、日志和后台管理接口相关服务。
- `backend/app/services/runtime_store_initializer.py`：运行时数据库初始化。
- `backend/app/services/auth_service.py`：登录、token、用户上下文解析。
- `backend/app/services/feedback_service.py`：用户反馈写入与查询。
- `backend/app/services/evaluation_service.py`：评测运行与结果汇总。

## Frontend Structure

前端核心代码位于 `frontend/src/`。

- `frontend/src/main.tsx`：前端入口。
- `frontend/src/App.tsx`：主应用页面，当前工作台的主要界面逻辑都在这里。
- `frontend/src/api.ts`：前后端接口封装。
- `frontend/src/types.ts`：前端使用的数据结构定义。
- `frontend/src/styles.css`：全局样式。

当前前端更偏“工作台 / 调试台”，重点是配合后端链路验证，而不是完整产品化界面。

## Recommended Reading Order

如果要快速理解当前项目，建议按下面顺序阅读：

1. `TEXT2SQL_ARCHITECTURE.md`
2. `DEVELOPMENT_PLAN.md`
3. `semantic/semantic_layer.json`
4. `backend/app/core/container.py`
5. `backend/app/services/orchestrator.py`
6. `backend/app/services/query_planner.py`
7. `backend/app/services/question_classifier.py`
8. `backend/app/services/prompt_builder.py`
9. `backend/app/services/sql_generator.py`
10. `backend/app/services/session_state_service.py`

## Files Most Likely To Change Frequently

按当前阶段判断，后续最容易持续迭代的是这些文件：

- `semantic/semantic_layer.json`
- `backend/app/services/question_classifier.py`
- `backend/app/services/prompt_builder.py`
- `backend/app/services/query_planner.py`
- `backend/app/services/semantic_runtime.py`
- `backend/app/services/session_state_service.py`
- `sql/semantic_view_drafts.sql`
- `TEXT2SQL_ARCHITECTURE.md`
- `DEVELOPMENT_PLAN.md`

这些文件基本对应当前项目最核心的三件事：规则继续收敛、语义对象继续补全、链路调试继续加深。
