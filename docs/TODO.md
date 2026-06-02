# 待办项

这里记录尚未实现、但已经形成方向约束的工程待办。待办不等同于当前架构事实；落地前需要按风险补测试和 trace 验证。

## PromptBuilder 供应链拆分

状态：待实现。

目标：降低 `PromptBuilder` 膨胀风险，把 prompt 相关的工程职责拆开，但保持当前 LLM-first 架构，不把业务理解改造成代码规则引擎。

### 背景

当前链路已经是检索必要证据后再组装 prompt，不是把全部语义资产无差别塞给大模型。风险点不在 token 是否全量输入，而在 `PromptBuilder` 容易继续承载过多职责：任务规则、上下文选择、上下文渲染、方言约束、预算裁剪和最终 payload 组装。

### 非目标

- 不新增按业务关键词分支的 Python 规则，例如基于“库存”“账龄”“周转率”等自然语言关键词强制选择表、字段、公式或过滤条件。
- 不把业务口径、指标定义、默认过滤、禁忌、join 方式写进代码。
- 不把 `SqlValidator` 扩展成业务正确性裁判；validator 继续只负责只读、安全、真实表字段、Oracle 语法和结果限制等硬边界。
- 不引入 `InventoryPromptBuilder`、`DemandPromptBuilder` 这类按业务域硬拆的 prompt builder，避免演化为场景化规则引擎。

### 原则

- 业务事实继续来自 `semantic/tables.json`、`semantic/business_knowledge.json`、`semantic/join_patterns.json`、`examples/`、检索结果和向量语料。
- 代码只负责加载、检索、排序、裁剪、渲染、组装、安全校验和审计。
- selector 只能基于 retrieval score、source type、表覆盖度和上下文预算等证据排序，不能发明业务语义。
- renderer 只做机械渲染，不判断具体业务问题应该怎么查。
- 业务修复优先沉淀到语义资产、样例、join pattern、retrieval 和 prompt asset，而不是新增 Python if/else。

### 建议拆分

第一阶段保持对外接口不变，只做无行为变化拆分：

```text
PromptBuilder  # facade，保留现有调用入口
  -> QuestionContextPromptBuilder
  -> SqlGenerationPromptBuilder
```

第二阶段再拆通用渲染和证据组装模块：

```text
SqlGenerationPromptBuilder
  -> EvidenceSelector
  -> TableSchemaRenderer
  -> BusinessKnowledgeRenderer
  -> ExampleRenderer
  -> JoinPatternRenderer
  -> RulePackLoader
  -> PromptPayloadAssembler
```

这些模块必须保持通用，不能变成业务域规则模块。

### Trace 与测试要求

落地时需要补充或调整测试，至少覆盖：

- 重构前后关键 SQL prompt payload 行为不变。
- `QuestionContext` prompt 不包含 SQL 生成指令。
- SQL prompt 只渲染选中表和检索证据。
- business knowledge、few-shot、join pattern 的进入 prompt 过程可在 trace 中定位。
- prompt metadata 记录 task、版本、命中的 knowledge/example/join pattern id 和上下文预算。

验收标准：拆分后线上排错能更清楚地回答“哪些证据进了 prompt、每块证据从哪里来、为什么被选中”，同时没有新增业务场景硬编码。
