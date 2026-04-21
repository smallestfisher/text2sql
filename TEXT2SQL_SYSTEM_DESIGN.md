# Text2SQL 系统设计方案

## 1. 文档目标

本文档描述一个基于 `tables.json` 中 MySQL 表结构构建的面向业务用户的 Text2SQL 查询系统设计方案。重点覆盖以下问题：

- 多表关联场景下的查询规划
- 上下文关联与不关联问题识别
- 补充问题与追问机制
- 无效输入与非数据问题识别
- `RAG + 例子 + LLM` 的整体技术方案
- 查询安全控制与执行边界
- 工程实现与可维护性设计

本文档不包含“按用户数据域裁剪查询结果”的数据访问权限设计。这里的权限管理仅指系统用户、角色、页面/API 能力权限。

## 2. 背景与约束

### 2.1 当前数据现状

系统当前输入的结构定义来源于 [tables.json](/home/y/llm/new/tables.json)，主要包含以下业务域：

- 需求：`v_demand`、`p_demand`
- 库存：`daily_inventory`、`oms_inventory`
- 计划：`daily_PLAN`、`weekly_rolling_plan`、`monthly_plan_approved`
- 生产实绩：`production_actuals`
- 销售财务：`sales_financial_perf`
- 产品维度：`product_attributes`、`product_mapping`

### 2.2 数据结构特点

该 schema 有如下明显复杂度：

- 多种时间粒度并存：日、月、周版本
- 存在版本字段：`PM_VERSION`
- 需求表为宽表月序列结构：`REQUIREMENT_QTY`、`NEXT_REQUIREMENT`、`LAST_REQUIREMENT`、`MONTH4`...`MONTH7`
- 产品编码存在双体系：`FGCODE` 与 `product_ID`
- 表间关系部分依赖业务映射，不是显式外键
- 不同表对“工厂”“日期”等概念命名不一致

### 2.3 核心设计原则

该系统不应设计为“用户问题 -> LLM 直接输出 SQL”。核心原则如下：

1. LLM 负责理解和规划，不负责直接决定最终 SQL。
2. 多表 join 路径由语义模型和编译器控制，不由模型自由发挥。
3. 上下文不依赖单一状态对象，而依赖可检索的历史查询事实。
4. 补充问题由“缺槽位检查”触发，不由固定规则树驱动。
5. 查询安全通过白名单、编译器和 AST 校验保证，而不是通过 prompt 保证。

## 3. 目标与非目标

### 3.1 项目目标

构建一个支持多轮对话的数据查询系统，具备以下能力：

- 业务用户可用自然语言发起查询
- 支持多表关联与常见业务跨域分析
- 能识别当前输入是新问题、追问、补充条件还是非数据问题
- 支持补充问题、追问、上下文继承与上下文切换
- 在复杂 schema 下保持较稳定的 SQL 生成质量
- 提供系统级用户权限管理、审计与运营能力

### 3.2 非目标

当前方案不覆盖以下内容：

- 行级/列级数据权限控制
- 自动写回数据库或执行更新类 SQL
- 开放式 BI 报表建模平台
- 对任意未知业务表自动零配置支持

## 4. 总体方案

### 4.1 总体架构

系统采用 `RAG + 例子 + LLM + Query Planner + SQL Compiler` 的组合式架构。整体链路如下：

1. 用户输入自然语言问题
2. 检索相关语义对象、join 子图、历史查询事实、相似示例
3. LLM 输出结构化动作 `action` 与查询草案 `QueryDraft`
4. 编译器对 `QueryDraft` 做缺槽位检查、上下文合并、语义约束与 join 路径决策
5. 若关键槽位缺失，则生成补充问题
6. 若可执行，则编译为受控 SQL
7. SQL 经过 AST 校验、只读校验、白名单校验后执行
8. 返回结果、口径说明、本轮继承信息和下一步建议

### 4.2 核心模块

系统分为以下模块：

- 用户与权限模块
- 语义知识层
- 检索增强层
- LLM 解释层
- 查询规划与编译层
- SQL 校验与执行层
- 会话事实与上下文管理层
- 审计与运营层

## 5. 语义知识层设计

### 5.1 设计目标

语义知识层负责把底层物理表抽象成可查询的业务对象，降低模型直接面对底表结构的复杂度。

### 5.2 语义对象划分

建议将当前 schema 抽象成以下语义对象：

- `Demand.V`
- `Demand.P`
- `Inventory.Daily`
- `Inventory.OMS`
- `Plan.Daily`
- `Plan.Weekly`
- `Plan.MonthlyApproved`
- `Production.Actual`
- `Sales.Financial`
- `Product.Attributes`
- `Product.Mapping`

### 5.3 语义对象定义内容

每个语义对象需要定义：

- 业务描述
- 来源表或语义视图
- 可用指标
- 可用维度
- 时间字段
- 版本字段
- 默认粒度
- 同义词与常见问法
- 必填槽位
- 可选槽位
- 支持的 join 关系

### 5.4 针对当前 schema 的关键语义改造

#### 5.4.1 需求宽表标准化

`v_demand` 与 `p_demand` 必须先转成长表语义视图，否则后续时间过滤和聚合非常脆弱。

建议构建：

- `semantic_demand_v`
- `semantic_demand_p`

统一字段建议如下：

- `pm_version`
- `demand_type`
- `fg_code`
- `sbu`
- `bu`
- `customer`
- `demand_month`
- `demand_qty`

#### 5.4.2 产品编码统一

跨域分析时需要明确：

- 需求/销售财务优先以 `FGCODE` 为主键
- 库存/计划/生产优先以 `product_ID` 为主键
- 跨域联动必须通过 `product_mapping` 或明确映射关系完成

### 5.5 建议产物

建议维护如下配置文件：

- `semantic_model.yaml`
- `slot_requirements.yaml`
- `metric_catalog.yaml`
- `dimension_catalog.yaml`

## 6. Join Graph 与多表关联设计

### 6.1 问题定义

多表关联是该系统最容易产生错误的环节。主要风险不是“找不到相关表”，而是：

- join 路径选错
- 一对多导致重复计数
- 先聚合后 join 与先 join 后聚合混淆
- `FGCODE` 与 `product_ID` 误直接关联

### 6.2 设计原则

系统不允许 LLM 直接生成底层 join 逻辑。join 决策必须由编译器在白名单图谱内完成。

### 6.3 Join Graph

建议维护 `join_graph.yaml`，包含：

- 左右语义对象/字段
- 关系类型：直接、桥接、维度扩展
- 基数：一对一、一对多、多对一
- join 条件模板
- 使用限制
- 是否需要先聚合

示例：

```yaml
relations:
  - left: Demand.V.fg_code
    right: ProductMapping.fg_code
    relation_type: bridge
    cardinality: many_to_one

  - left: Inventory.Daily.product_id
    right: Product.Attributes.product_id
    relation_type: direct
    cardinality: many_to_one

  - left: Sales.Financial.fg_code
    right: ProductMapping.fg_code
    relation_type: bridge
    cardinality: many_to_one
```

### 6.4 Join Template

在部分语义组合下，仅定义关系还不够，还需要模板化约束，例如：

- 需求与产品属性联动时，先按 `FGCODE` 聚合，再映射到产品属性
- 库存明细与产品属性联动时，可直接按 `product_ID` 关联
- 多事实表之间原则上不直接 join，优先各自聚合后再按共享维度对齐

### 6.5 事实表跨域规则

建议定义规则：

- 事实表与维表可以直接关联
- 事实表与事实表默认不直接关联
- 若确需跨事实分析，必须通过预定义语义模板或统一粒度中间层

## 7. 检索增强层设计

### 7.1 设计目标

检索层用于减少模型对上下文、schema 和记忆的裸推理依赖。它不直接回答数据结果，而是为查询规划提供依据。

### 7.2 检索对象分类

建议建立四类索引：

#### 7.2.1 语义对象索引

检索语义对象定义，例如：

- `Demand.V`
- `Inventory.Daily`
- `Plan.Weekly`

每个文档包含：

- 业务描述
- 可用指标
- 可用维度
- 时间字段
- 常见问法
- 典型限制

#### 7.2.2 Join 子图索引

检索“当前问题涉及哪些语义对象，以及它们可通过哪些关系连通”。

检索结果不是表列表，而是候选 join 子图。

#### 7.2.3 会话事实索引

检索最近历史中与当前输入最相关的已确认查询事实，用于判断：

- 当前是追问还是新问题
- 哪些旧条件可继承
- 哪些旧条件必须失效

#### 7.2.4 示例索引

存放人工验证过的：

- 问题到 `action` 的示例
- 问题到 `QueryDraft` 的示例
- 多表场景下的 `QueryPlan` 示例

### 7.3 检索策略

建议使用混合检索：

- 关键词/BM25：适合字段名、业务名、指标名
- 向量检索：适合自然语言相似问法
- 图检索：适合 join 路径与语义关系

### 7.4 检索层定位

检索层不是最终决策者，它负责提供“候选知识”，真正的动作输出仍然由 LLM 和编译器共同决定。

## 8. LLM 解释层设计

### 8.1 设计目标

LLM 层不直接生成 SQL，而是负责将“当前输入 + 检索结果 + 历史事实”解释为一个结构化动作。

### 8.2 Action Schema

建议定义统一动作：

- `NEW_QUERY`
- `PATCH_QUERY`
- `ANSWER_CLARIFICATION`
- `CONTINUE_RESULT`
- `NON_DATA_REQUEST`
- `UNSUPPORTED_REQUEST`
- `ASK_CLARIFICATION`

### 8.3 QueryDraft Schema

LLM 需要输出结构化查询草案，例如：

```json
{
  "entity": "Demand.V",
  "metrics": [{"name": "demand_qty", "agg": "sum"}],
  "dimensions": ["customer"],
  "filters": {
    "demand_month": {"eq": "2026-04"},
    "application": {"eq": "TV"}
  },
  "version": "latest",
  "sort": [{"field": "demand_qty", "direction": "desc"}],
  "limit": 10
}
```

### 8.4 置信度输出

LLM 需要输出：

- `confidence`
- `reasoning_summary`
- 本轮是否依赖历史上下文
- 若为 patch，修改的是哪些字段

### 8.5 低置信度处理

当 `confidence` 低于阈值时，不应强执行，应触发：

- 保守解释
- 补充问题
- 或明确说明理解不充分

## 9. 会话事实与上下文管理设计

### 9.1 设计原则

系统不维护单一、长期可变的 `active_state` 作为真相来源。会话采用“事实存储 + 检索重建”的方式。

### 9.2 每轮事实内容

建议每轮保存：

- 用户原始输入
- LLM 动作输出
- `QueryDraft`
- 缺槽位检查结果
- 最终 `QueryPlan`
- 执行时采用的默认值
- 补充问题及回答
- 结果摘要
- 失败原因或拒绝原因

### 9.3 上下文重建原则

下一轮处理时：

1. 检索最近相关历史事实
2. 选取最近一次成功执行且语义相关的 `QueryPlan`
3. 由 LLM 判断当前输入是新问题还是 patch
4. 若为 patch，重新构造本轮候选上下文
5. 对字段逐项进行继承有效性检查

### 9.4 字段来源管理

每个字段都建议记录来源：

- `user_explicit`
- `clarification_confirmed`
- `system_default`
- `planner_inferred`

字段来源决定其后续继承策略。例如：

- 用户明确指定的时间范围可高优先级继承
- 系统默认的版本号在实体切换后应失效
- 某次临时推断出的粒度不应长期继承

## 10. 补充问题与追问机制

### 10.1 设计原则

补充问题不靠规则树硬编码，而由编译器的缺槽位检查驱动。

### 10.2 缺槽位检查

编译器在处理 `QueryDraft` 时检查：

- 当前实体是否已明确
- 时间范围是否足够落地
- 版本字段是否必填
- 多个候选语义对象是否仍无法区分
- 指标、维度、过滤条件是否有冲突

### 10.3 补充问题生成

当存在关键缺槽位时，编译器输出：

- `missing_slots`
- 缺失原因
- 建议补问方向

再由 LLM 将其翻译成简洁、聚焦的业务语言。

### 10.4 示例

用户输入：`查计划`

编译器可返回：

```json
{
  "missing_slots": [
    {
      "slot": "plan_type",
      "reason": "Plan 语义域存在 Daily、Weekly、MonthlyApproved 三种候选来源"
    }
  ]
}
```

LLM 再生成补问：

`你要查日计划、周滚动计划，还是月度审批计划？`

## 11. 无效输入、非数据问题与不支持问题识别

### 11.1 分类原则

不要将所有“当前无法直接执行”的问题都归为无效输入。建议拆成以下类别：

- `needs_clarification`
- `unsupported`
- `non_data_request`
- `uninterpretable`

### 11.2 定义

#### 11.2.1 needs_clarification

问题可理解，但缺关键槽位，例如：

- `查计划`
- `查需求`

#### 11.2.2 unsupported

当前 schema 不支持该指标、维度或业务对象，例如：

- `查毛利率`
- `按区域经理看`

#### 11.2.3 non_data_request

不是数据查询任务，例如：

- `解释一下 OLED`
- `帮我写周报`

#### 11.2.4 uninterpretable

系统无法形成稳定解释，例如纯乱码、极度残缺输入。

### 11.3 不关联问题识别

系统不通过硬规则判断“是否不关联”，而是由 LLM 根据：

- 当前输入
- 会话事实检索结果
- 相似 follow-up 示例
- 当前候选语义对象

输出 `NEW_QUERY` 或 `PATCH_QUERY`。

## 12. 查询规划、编译与 SQL 安全

### 12.1 查询规划

`QueryDraft` 不是最终执行对象，必须进一步编译成 `QueryPlan`。`QueryPlan` 需要明确：

- 使用的语义对象
- 使用的事实表与维表
- join 子图
- 聚合顺序
- 字段映射
- 过滤条件
- 排序与 limit

### 12.2 SQL 编译边界

SQL 编译器负责：

- 将语义字段映射到物理字段
- 按 join graph 生成 join
- 应用聚合模板
- 统一时间和版本过滤
- 输出受控 SQL

### 12.3 安全控制

虽然本项目不做数据权限裁剪，但仍需严格的查询安全控制：

- 仅允许 `SELECT`
- 仅允许单语句
- 仅允许白名单语义视图/表
- 自动追加默认 `LIMIT`
- 设置执行超时
- 设置返回行数上限
- 禁止未定义 join 路径

### 12.4 AST 校验

SQL 执行前应进行 AST 级校验，重点校验：

- 是否为只读查询
- 是否访问白名单对象
- 是否包含禁用函数
- 是否包含危险子查询或系统表引用

## 13. 用户权限管理设计

### 13.1 范围说明

权限管理只负责“谁能使用哪些系统功能”，不负责按用户裁剪查询结果。

### 13.2 推荐权限模型

采用标准 RBAC 即可。

角色建议：

- `admin`
- `analyst`
- `viewer`
- `ops`

权限点建议：

- `chat.query.use`
- `chat.history.view`
- `query.export.csv`
- `query.sql.view`
- `semantic.manage`
- `audit.log.view`
- `user.manage`
- `role.manage`

### 13.3 建议数据表

- `users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`
- `sessions`
- `audit_logs`

## 14. 技术方案

### 14.1 推荐技术栈

建议采用以下技术组合：

- 后端框架：`Python + FastAPI`
- 数据库访问：`SQLAlchemy Core` 或轻量 SQL 生成器
- 检索：`PostgreSQL pgvector` 或向量数据库 + 图关系存储
- 元数据配置：`YAML + JSON Schema`
- SQL 解析校验：`sqlglot` 或等价 AST 工具
- 缓存：`Redis`
- 任务编排与异步处理：`Celery` 或轻量队列
- 前端：`React` 或现有内部前端技术栈

### 14.2 推荐服务划分

可按以下服务或模块组织：

- `auth-service`
- `chat-service`
- `semantic-registry`
- `retrieval-service`
- `planner-service`
- `sql-executor`
- `audit-service`

若初期团队规模有限，也可先以单体服务实现，再逐步拆分。

### 14.3 核心数据结构建议

建议优先定义以下 schema：

- `action_schema.json`
- `query_draft.schema.json`
- `query_plan.schema.json`
- `conversation_fact.schema.json`
- `clarification.schema.json`

## 15. API 设计建议

### 15.1 会话与查询接口

- `POST /chat/query`
- `POST /chat/followup`
- `GET /chat/sessions`
- `GET /chat/sessions/{id}`
- `DELETE /chat/sessions/{id}`

### 15.2 用户权限接口

- `POST /auth/login`
- `POST /auth/logout`
- `GET /me`
- `GET /users`
- `POST /users`
- `PUT /users/{id}`
- `GET /roles`
- `PUT /users/{id}/roles`

### 15.3 管理与审计接口

- `GET /semantic-model`
- `PUT /semantic-model`
- `GET /examples`
- `PUT /examples`
- `GET /audit/logs`

## 16. 审计、可观测性与运营

### 16.1 审计日志

每轮查询建议记录：

- 原始用户输入
- 检索结果摘要
- LLM 输出的 `action` 与 `QueryDraft`
- 缺槽位检查结果
- 最终 `QueryPlan`
- SQL 摘要
- 执行耗时
- 返回条数
- 是否命中缓存
- 是否触发补问
- 失败原因

### 16.2 关键指标

建议持续跟踪：

- 首问可执行率
- 补问率
- 无效/不支持问题比例
- 追问命中率
- SQL 编译失败率
- 查询平均时延
- 用户人工纠正率

### 16.3 评测机制

系统优化不应以补规则为主，而应以评测集驱动：

- 多轮 follow-up 场景
- 新问题切换场景
- 不支持问题识别场景
- 多表关联场景
- 需求宽表时间展开场景
- 事实表跨域分析场景

## 17. 风险与设计应对

### 17.1 风险：RAG 检索结果本身不准确

应对：

- 混合检索
- 结果重排序
- 检索结果数量受控
- 关键场景下引入人工审核示例库

### 17.2 风险：LLM 将新问题误判为追问

应对：

- 输出结构化 `action + confidence`
- 低置信度场景保守处理
- 响应中回显本轮继承内容

### 17.3 风险：复杂 join 导致错误聚合

应对：

- join graph 白名单
- 事实表跨域模板
- 聚合前后顺序约束
- 引入 SQL 验证测试集

### 17.4 风险：会话上下文漂移

应对：

- 不依赖单一 active state
- 使用会话事实检索重建
- 保留字段来源与默认值来源

## 18. 最终结论

该 Text2SQL 系统的正确建设方向不是“规则树 + 状态机 + 直接生成 SQL”，而是：

- 用语义层稳定业务口径
- 用 RAG 和示例增强问题理解
- 用会话事实检索替代脆弱的单状态继承
- 用 `Action + QueryDraft + QueryPlan` 中间协议解耦模型与执行
- 用编译器、join graph 和 AST 校验保障 SQL 的可执行性与正确性

从可维护性角度看，后续迭代应主要围绕以下对象进行：

- 语义模型
- join graph
- 示例库
- 评测集
- 查询编译器

而不是持续为每个场景补状态机分支或 if/else 规则。
