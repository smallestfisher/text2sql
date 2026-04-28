# LLM Intent Migration Summary

## 1. 目的

本文档不再记录完整迁移过程，而是作为 **LLM intent 理解链路迁移的收官说明**。

当前工程已经完成从：

- `parser/classifier/planner-first`

迁移到：

- `shallow parse + LLM intent + normalizer + LLM-primary classifier + deterministic planner`

如果需要看完整运行架构，优先阅读：

- `TEXT2SQL_ARCHITECTURE.md`

如果需要看调试方法，阅读：

- `DEBUG_PLAYBOOK.md`

---

## 2. 最终架构结论

当前理解链路已经收敛为下面五层：

1. `QueryIntentParser`
   - 只做 shallow parse
   - 只保留显式、高确定性信号抽取

2. `LLMIntentService`
   - 负责主链路意图理解
   - 输出结构化 `StructuredIntent`

3. `IntentNormalizer`
   - 对 LLM intent 做字段合法化、domain 合法化、metric/field 收口

4. `QuestionClassifier`
   - 采用 `LLM-primary + baseline heuristic + hard guard`
   - 本地 baseline 只保留轻量仲裁证据与 trace 对照用途，不再作为最终执行分类结果

5. `QueryPlanner` / `QueryPlanCompiler`
   - 只做 deterministic planning / compile / sanitize
   - 不再承担高层语义猜测职责

补充说明：

- 当前 retrieval / example 资产仍然存在，但它们不改变上述理解主链路
- example 会参与检索，并在 SQL 生成阶段以 `retrieved_examples` 形式进入 prompt；同时保留内置场景模板 few-shot

---

## 3. 已完成项

### 3.1 Parser 收缩完成

`QueryIntentParser` 已降为更纯的 shallow extractor：

- 保留：
  - metric / entity alias 命中
  - time / version
  - dimensions
  - filters
  - sort / limit
  - analysis_mode
  - follow-up cue
- 已移除：
  - parser 层 metric resolve
  - parser 层 demand shortcut
  - parser 层 session domain 兜底

### 3.2 Intent 主链路完成

`LLMIntentService` + `IntentNormalizer` 已进入主链路：

- 不再保留 `shadow / primary / fallback` 过渡开关
- `normalized intent` 已成为默认高层理解输入
- 当 LLM intent 不可用时，主链路不再继续执行；parser 只保留为 trace 对照基线

### 3.3 Classifier 迁移完成

`QuestionClassifier` 已完成迁移：

- 保留：
  - invalid / smalltalk hard guard
  - relevance guard
  - allowed question types check
  - follow-up 安全边界
- 主判定：
  - LLM classification
- 本地基线：
  - 仅保留轻量 baseline heuristic
  - 只提供 `candidate_scores / score_gap / baseline_classification`
  - 不再承担大量规则评分职责

### 3.4 Planner 迁移完成

`QueryPlanner` / `QueryPlanCompiler` 已回归 deterministic：

- planner 只负责：
  - context merge
  - dimension suggestion
  - table selection
  - sort / limit default
  - domain constraints
- 已移除：
  - query plan hint 二次改写链路
  - planner 层高层语义补猜
  - 旧理解链路兼容分支

### 3.5 命名和配置清理完成

迁移过程中遗留的 `fallback` 命名已基本清理：

- API 依赖参数改为 `default_user_context`
- replay / restore 内部变量改为 `default_* / restored_*`
- query profile 配置改为 `use_domain_tables_when_metric_tables_missing`
- 澄清文案键改为 `clarification`

---

## 4. 当前仍保留的基线

这里的这些产物不是执行回退，而是 trace-only 对照输入：

- `parser_intent`
- `llm_intent`
- `normalized_intent`
- `baseline_classification`

保留这些信息的目的只有两个：

1. 便于 trace 调试
2. 便于判断 LLM 理解与本地结构约束之间的差异

这不再表示系统存在一条单独维护的旧理解链路。

---

## 5. 当前代码边界

### 理解层

- `backend/app/services/query_intent_parser.py`
- `backend/app/services/intent_service.py`
- `backend/app/services/intent_normalizer.py`
- `backend/app/services/question_classifier.py`
- `backend/app/services/query_planner.py`

### 规划与治理层

- `backend/app/services/query_plan_compiler.py`
- `backend/app/services/query_plan_validator.py`
- `backend/app/services/sql_validator.py`

### 编排层

- `backend/app/services/orchestrator.py`

---

## 6. 后续优化项

迁移主体已经完成，后续主要是优化，不再是架构切换：

1. 继续用真实问题校准 `intent prompt / normalizer / classifier`
2. 继续压缩不再必要的本地启发式
3. 继续减少 `domain_config` 中非稳定语义配置
4. 补充真实问题样例，而不是假设样例
5. 持续验证 trace / replay / eval 闭环是否稳定

---

## 7. 判断标准

如果要判断这次迁移是否完成，标准不是“是否一行旧逻辑都没了”，而是下面四条：

- 高层问题理解是否已经由 LLM 主导
- 本地代码是否已经回到结构约束与治理职责
- parser/classifier/planner 是否不再继续向规则引擎膨胀
- 主链路是否不再依赖 parser/baseline 作为执行回退

按这个标准，当前迁移已经完成。
