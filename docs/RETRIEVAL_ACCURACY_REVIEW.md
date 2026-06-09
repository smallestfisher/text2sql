# 检索准确率与运行效率评审

**评审日期**: 2026-06-09
**代码基线**: 分支 `improve-retrieval-accuracy`（含未提交的 fusion 归一化改动）
**评审方式**: 通读主链路、检索、prompt 组装、LLM 客户端、向量通道、metadata 加载；1.1 / 2.1 / 2.2 结论经第二方（Codex）复核与只读实测校准
**定位**: 聚焦准确率与效率的可执行问题，标注代码位置。与偏结构重构的 [ARCHITECTURE_REVIEW.md](./ARCHITECTURE_REVIEW.md) 互补。

---

## 0. 结论先行

工程的设计理念清晰且贯彻良好：**业务事实沉淀到语义资产，Python 只做加载 / 检索 / 排序 / 裁剪 / 校验**（见 [PROJECT_GUIDE.md](./PROJECT_GUIDE.md) §9）。证据闭合、双通道融合、两段式 QuestionContext、Terminal Gate 都是合理的抽象。

但当前存在一个**贯穿性准确率缺陷**：`improve-retrieval-accuracy` 分支的归一化已经改善了「排序」（向量-only 的 join_pattern 已能通过 opt-in fixture 测试进入 prompt），但「最终入选 SQL prompt 的二次打分」仍存在量纲不一致的残余问题——该层主要消费原始 `hit.score`，而向量 `hit.score` 被硬乘 `0.45`，导致纯向量证据在入选阶段仍被系统性低估。这是本文档的头号问题。

---

## 1. 🔴 准确率问题

### 1.1 融合归一化未完全贯穿到 prompt 二次排序层（已处理）

**现象**：检索排序已改用归一化后的 `fusion_score`（向量-only 证据已能进入排序并通过 opt-in fixture 测试），但 prompt builder 在 example / knowledge / join_pattern **二次入选打分**时仍主要使用原始 `hit.score`，而向量 hit 的 `hit.score` 被硬乘 `0.45`。两层对「分数」的标度理解不一致。

**证据链**：

- 向量 hit 打分：`hit.score = float(item["score"]) * 0.45`
  → `backend/app/services/retrieval_service.py:644`
  cosine 约 0.3–0.9，乘 0.45 后落在 ≈0.15–0.4。
- 排序已改用归一化的 `fusion_score`（按 `(channel, source_type)` 分桶 min-max）
  → `retrieval_service.py:654`（`_apply_fusion_scores`）、`retrieval_service.py:734`（按 `fusion_score` 排序）✅
- 但 prompt builder 选证据进 prompt 时消费的是 `hit.score`，不是 `fusion_score`：
  - example：`score = hit.score`
    → `backend/app/services/prompt_builder.py:575`（`_score_retrieved_example`）
  - knowledge：`score += 6 + knowledge_hit_scores[entry_id]`，该值来自 `hit.score`
    → `prompt_builder.py:477` 与 `prompt_builder.py:498`（`_retrieved_knowledge_hit_scores`）
  - join_pattern：`score = hit.score + ...`，且最终只取 `[:1]`
    → `prompt_builder.py:623` 与 `prompt_builder.py:637`

**后果**：BM25 命中的 `hit.score` 在 5–48 量级，纯向量命中只有 0.15–0.4。归一化已让向量证据进入**排序**，但在 prompt 的**二次入选打分**里，任何「只靠向量召回」的 example / knowledge / join_pattern 仍被系统性低估，要靠结构化重叠分兜底才能入选。**归一化在排序层生效了，在选择层只生效了一半。**

**为什么 opt-in 测试现在能过**：`eval/retrieval_cases.json` 里 `vector_only_expected_join_pattern_ids`（如 `demand_product_attributes_direct_join`）能通过 fixture 测试，部分是靠表重叠 `evidence_score`（`prompt_builder.py:682`）兜底，而非向量分数本身在二次排序里起决定作用。换言之向量证据能进入 prompt，但它在入选竞争中的权重被系统性压低，是脆弱的隐式依赖——一旦表重叠条件变化就可能回归。

**修复方向（已校准，不做简单替换）**：

不要把三处 `hit.score` 直接替换成 `fusion_score`——那会把 keyword/BM25 强命中的 boost 从十几/几十压到 1–2 分，削弱当前稳定的 keyword 行为。

正确做法是设计一个 **bounded retrieval boost**：

- 由归一化的 `fusion_score`（`[0,1]`）映射到一个**固定分值区间**（例如 `0 ~ B`，`B` 取一个能与结构化 evidence 分相称的常量）。
- 再把这个 bounded boost 叠加到原有的结构化 evidence 分（domain / table / metric 重叠等）之上。
- 这样既让向量证据拿到稳定、不被量纲淹没的检索加成，又不让任一通道的原始量级反向压制对方。

三处入选打分（`_score_retrieved_example` / `_score_business_knowledge_entry` 经由 `_retrieved_knowledge_hit_scores` / `_score_join_pattern_payload`）统一切到这个 bounded boost 标度。需同步重跑 `tests.test_retrieval_eval` 和 vector fusion 测试，并用 `probe_retrieval_scores.py` 确认两通道分布。

---

**实施结果**

已按保守 bounded boost 方案处理，保留结构化 evidence 分，只替换 prompt 二次排序里的检索分量：

- `RETRIEVAL_BOOST_WEIGHT = 4.0`
- `RETRIEVAL_FUSION_SCORE_CAP = 1.5`
- `RETRIEVAL_PRESENCE_BONUS = 2.0`
- `KNOWLEDGE_PRESENCE_BONUS = 3.0`

example / join_pattern 的 retrieved 分支使用 `self._retrieval_boost(hit)`，即 `RETRIEVAL_PRESENCE_BONUS + bounded boost`；knowledge 使用命中的最大 `fusion_score`，再映射为 `KNOWLEDGE_PRESENCE_BONUS + bounded boost`。`join_pattern` 仍保持每个问题只选择 1 条主规则；一条 join pattern 可以覆盖多张表和多段 join path。

**补充修复：example 准入闸门也切到 fusion 标度。** 三处打分修好后 review 发现 example 还有一个被遗漏的缺口：example 的真正闸门是 `_retrieved_example_matches_context`，而非 score——它在打分之前决定 example 能否进入排序。该方法末位兜底原本用 `hit.score >= 2.0`，但向量 hit 的 `hit.score = cosine * 0.45 ≈ 0.15–0.4`，结构上永远 < 2.0，导致「无任何结构重叠、纯向量召回」的 example 在打分之前就被丢弃，presence bonus 与 bounded boost 对它完全无效。已将该闸门改为 `_hit_fusion_score(hit) > 0`（桶内非最弱命中）配合既有的语义 `matched_feature` 条件。这样 example 与 knowledge / join_pattern 一样，对「被向量召回」处理一致，1.1 才对三类证据真正闭合。

新增回归测试覆盖：

- example 排序使用 bounded fusion boost，而非 raw `hit.score`。
- join pattern 排序使用 bounded fusion boost，且仍只返回 1 条主 join pattern。
- example / join_pattern 的 retrieved 分支具备 presence floor：`fusion_score=0` 时检索分量仍 > 0。
- vector-only example（与 context 零结构重叠）经 fusion 标度闸门放行；而桶内最弱命中（`fusion_score=0`）仍被闸门拦截，不会仅凭 `matched_features` 混入。

---

**实施方案（已执行）**

前置事实（已核对代码）：

- `RetrievalHit.fusion_score` 字段已存在（`backend/app/models/retrieval.py:13`），`retrieve_text` 在 rerank 前已对所有 hit 调用 `_apply_fusion_scores` 填充，prompt builder 拿到的 `retrieval.hits` 都带 `fusion_score`。
- 单通道命中 `fusion_score ∈ [0, 1]`（桶内 min-max）；**hybrid 命中（两通道都命中同一文档）的 `fusion_score` 是两通道归一化分之和**，可达 ≈2（`retrieval_service.py:727` 相加）。这正是我们想奖励的「双通道共识」证据。
- 向量关闭时 `fusion_score` 仍按桶内 min-max 计算，是 BM25 的保序变换，所以**切到 fusion 标度不会打乱 keyword-only 场景下同 source_type 桶内的相对顺序**——这是本方案安全的根据。

**第 1 步：新增统一的 bounded boost 工具与常量**（`PromptBuilder` 类内）

```python
# 检索 boost 的标度常量（需用 eval 调参，下方为建议起点）
RETRIEVAL_BOOST_WEIGHT = 4.0   # fusion_score=1.0 时的加成，保守对齐一次表重叠(+4)
RETRIEVAL_FUSION_SCORE_CAP = 1.5  # 限制 hybrid 的 fusion_score 上限，单通道 boost≤4.0，hybrid boost 最多 6.0
RETRIEVAL_PRESENCE_BONUS = 2.0    # example / join_pattern 被检索命中的固定到场分
KNOWLEDGE_PRESENCE_BONUS = 3.0 # 「被检索命中过」的固定到场分（保守替代原先隐式的 +6）

def _retrieval_boost_from_fusion(self, fusion_score: float | None) -> float:
    if fusion_score is None:
        return 0.0
    return self.RETRIEVAL_BOOST_WEIGHT * min(fusion_score, self.RETRIEVAL_FUSION_SCORE_CAP)

def _retrieval_boost(self, hit: RetrievalHit) -> float:
    fusion = hit.fusion_score
    if fusion is None:  # 仅合成 hit（测试）会缺失；真实链路一定有值
        fusion = 1.0 if hit.score > 0 else 0.0
    return self.RETRIEVAL_PRESENCE_BONUS + self._retrieval_boost_from_fusion(fusion)
```

**第 2 步：三处入选打分替换**（只动「检索分量」，结构化 evidence 分原样保留）

- example（`prompt_builder.py:575`）：
  `score = hit.score` → `score = self._retrieval_boost(hit)`
- join_pattern（`prompt_builder.py:623`，`_selected_join_patterns` 内 retrieved 分支）：
  `score = hit.score + self._score_join_pattern_payload(...)`
  → `score = self._retrieval_boost(hit) + self._score_join_pattern_payload(...)`
  （全量扫描分支仍只用 `_score_join_pattern_payload`，保持「未被检索命中也能靠结构化重叠入选」）
- knowledge：
  - `_retrieved_knowledge_hit_scores`（`prompt_builder.py:491`）改为返回 `entry_id -> max(fusion_score)`，而非 `max(hit.score)`（缺失 fusion 时用 `1.0 if hit.score>0 else 0.0` 兜底）。
  - 消费处（`prompt_builder.py:487-488`）：
    `score += 6 + knowledge_hit_scores[entry_id]`
    → `score += self.KNOWLEDGE_PRESENCE_BONUS + self._retrieval_boost_from_fusion(knowledge_fusion[entry_id])`

**第 3 步：标度自检（替代量级，确认未削弱 keyword）**

切换后各 source_type 的「检索分量」上限：example/join_pattern 单通道 6.0、hybrid 9.0；knowledge 到场 4.0 + 单通道 6.0 = 10.0、hybrid 13.0。与结构化 evidence（domain +5、每表重叠 +4、metric +2…）量级相称，既不被 BM25 原始量级（5–48）压制，也不会把 keyword 强命中削到 1–2 分（Codex 担心的点）。

**第 4 步：验证**

1. `python3 -m unittest tests.test_retrieval_eval.RetrievalEvalTests`（vector-off，确认 keyword 行为不回归）。
2. `RUN_VECTOR_FUSION_TESTS=1 python3 -m unittest tests.test_retrieval_vector_fusion`（确认向量-only 证据**靠 boost** 而非表重叠兜底入选——必要时在 `retrieval_cases.json` 增设一个无表重叠的向量-only case 来真正卡住这条路径）。
3. `.venv/bin/python scripts/probe_retrieval_scores.py` 看两通道分布，回填校准 `RETRIEVAL_BOOST_WEIGHT` / `RETRIEVAL_FUSION_SCORE_CAP`。
4. `python3 -m unittest discover -s tests` + `python3 backend/domain_config_lint.py`。

**调参说明**：四个常量是这套方案的「旋钮」。当前采用保守起点 `WEIGHT=4.0 / CAP=1.5 / RETRIEVAL_PRESENCE=2.0 / KNOWLEDGE_PRESENCE=3.0`，按 `test_retrieval_eval` 的命中结果微调。`WEIGHT` 调大→更偏检索证据，调小→更偏结构化重叠；`CAP` 控制 hybrid 溢价幅度。

---

### 1.2 top hits 硬截断为 5，配额总和需要 7（已处理）

**证据**：

- `retrieve_text` 调 `_select_top_hits(hits, limit=5)`
  → `retrieval_service.py:126`
- 但 `_rerank_hits` 的配额是 example=2 + table_schema=2 + knowledge=2 + join_pattern=1 = **7**
  → `retrieval_service.py:740-745`

**后果**：期望 3 个 knowledge id 的 case（如 `retrieval_demand_latest_p_oxide_..._001`），检索本身最多透出 2 个 knowledge hit。第三个目前靠 prompt builder 从**全量** knowledge 重新打分补救（`_select_business_knowledge_entries` 不受 5-hit 限制，`prompt_builder.py:406`），但补救只能靠 domain/table/keyword 重叠，拿不到检索 boost，命中不稳定。

**处理结果**：`retrieve_text` 已改为按 `_rerank_hits` 的 source-type 配额总和计算 top hit limit，避免 rerank 努力被下游截断丢掉；新增回归测试覆盖 quota 总和 7 个 hit 均能透出。

---

### 1.3 向量通道无降级，故障即阻断整条链路（暂缓）

> **定位**：这是可用性策略问题，不属于本轮检索准确率修复的核心闭环。先保留当前 fail-fast 行为，等产品侧明确「向量不可用时是否允许降级作答」后再做。下文记录现状与方向，供后续决策。

**证据**：

- `_ensure_vector_ready` 在 pending_rebuild / error / not-ready 时直接 `raise RuntimeError`
  → `retrieval_service.py:292-301`
- 该方法在每次 `retrieve_text` 开头被调用
  → `retrieval_service.py:112`
- embedding 远程调用 timeout 20s，无降级路径
  → `backend/app/services/vector_retriever.py:39`、`vector_retriever.py:251`（`_remote_embed`）

**后果**：embedding API 慢一次或临时不可用，整个 query 失败，而不是退回到纯 BM25 仍能作答。可用性风险。与 [ARCHITECTURE_REVIEW.md](./ARCHITECTURE_REVIEW.md) HIGH #6 同源。

**修复方向**：向量不可用时记录降级原因、退回纯 keyword 通道继续，而非 raise。降级状态进 trace 便于排查。

---

## 2. 🟠 运行效率问题

### 2.1 每请求重新 normalize 全部样例（含 sqlglot 解析）（已处理）

**证据**：

- `PromptBuilder._load_examples()` 对每个样例调 `example_factory.normalize()`
  → `prompt_builder.py:168-177`
- 每请求触发：`_select_retrieved_examples`（`prompt_builder.py:538`）、`_supported_domains`（`prompt_builder.py:1193`）都调 `_load_examples()`
- 同一批样例的 sqlglot parse 在检索建引时已做过一遍（`retrieval_service._example_sql_features`，`retrieval_service.py:355`）

**后果**：同样的 SQL AST 解析每请求重做，是热路径上最大的可避免 CPU 开销。Codex 只读实测：**100 次 `_load_examples()` ≈ 71.9s**（约 0.72s/次），证实 repeated normalize / sqlglot parse 成本显著。

**处理结果**：`PromptBuilder._load_examples()` 已按 examples template 内容签名缓存 normalized records；同一内容重复调用复用缓存，template 内容变化时自动失效。新增回归测试覆盖复用与失效行为。本项收益在三个效率项中最高。

---

### 2.2 MetadataRegistry 每次属性访问都 deepcopy 整份 JSON

**证据**：

- `tables_metadata` / `business_knowledge_entries` / `examples_template` / `join_patterns` 均为 property，每次访问 `deepcopy` 整个 payload
  → `backend/app/services/metadata_registry.py:43-72`
- `PromptBuilder._tables_metadata` 也是 property（`prompt_builder.py:182`），单次 prompt 构建里在多个循环中被访问数十次：`_compact_table_schema`、`_relevant_table_columns`、`_field_belongs_to_horizontal_source`、`_context_table_fields` 等

**后果**：每次访问 deepcopy ≈17KB 的 `tables.json`，单请求内重复几十次。Codex 只读实测：**1000 次 `_tables_metadata` ≈ 0.62s、1000 次 `_business_knowledge` ≈ 1.41s**。开销真实存在，但绝对量级低于 2.1，优先级随之降低。

**修复方向**：PromptBuilder 内对 `_tables_metadata` / `_business_knowledge` 做请求内或 reload 级缓存；或 registry 提供只读视图避免无脑 deepcopy。

---

### 2.3 BM25 全语料线性扫描，无倒排索引（暂缓）

> **定位**：当前语料规模小，线性扫描不是主要瓶颈。等语料规模明显扩大后再做。下文记录现状与方向。

**证据**：

- `_retrieve_text_document_hits` 每 query 遍历全部 `corpus_documents` 逐个 `_bm25_score`
  → `retrieval_service.py:608-626`

**后果**：当前语料小可接受，语料规模上升后线性退化。优先级低于上面两项。

**修复方向（暂缓）**：按 token 建倒排索引，只对命中 token 的文档打分。当前语料规模下线性扫描不是主要瓶颈，等语料规模明显扩大后再做。

---

## 3. 优先级与建议顺序

优先级经 Codex 复核重排：先做高收益低风险、判断明确的项，再做需要设计权衡的项。

| 优先级 | 改动 | 影响 | 风险 | 对应章节 |
|---|---|---|---|---|
| **已处理** | `_select_top_hits` limit ≥ 配额总和（≥7） | 让 rerank 选出的期望证据透出，不被截断 | 低 | 1.2 |
| **已处理** | 样例 normalize 结果缓存（reload 级或内容签名） | 显著降延迟（实测 ≈0.72s/次） | 低 | 2.1 |
| **已处理** | 重新设计 prompt 二次排序的 retrieval boost 标度，让 `fusion_score` 以 **bounded boost** 形式叠加，而非简单替换 `hit.score` | 提准确率，让向量证据不被低估、也不过度削弱 keyword | 中（需设计 + 重测 eval） | 1.1 |
| **P2** | PromptBuilder 缓存 metadata，消除 deepcopy（实测 0.6–1.4s/千次） | 降延迟，量级低于 2.1 | 低 | 2.2 |
| **暂缓** | 向量通道超时降级到纯 BM25 | 可用性策略，待产品决策 | 中 | 1.3 |
| **暂缓** | BM25 倒排索引 | 规模化后才有收益 | 中 | 2.3 |

**下一步建议**：如继续优化效率，可做 **2.2** 的 PromptBuilder metadata 缓存；准确率侧先观察 bounded boost 在 eval/probe 中的表现，再决定是否调参。

---

## 4. 验证方式

改完后用以下命令守护（来自 [PROJECT_GUIDE.md](./PROJECT_GUIDE.md) §8、§11）：

```bash
# 检索命中回归（vector 关闭，校验 keyword 通道稳定证据）
python3 -m unittest tests.test_retrieval_eval.RetrievalEvalTests

# 全量测试
python3 -m unittest discover -s tests

# 语义资产 lint
python3 backend/domain_config_lint.py

# 向量融合（opt-in，需 fixture）
RUN_VECTOR_FUSION_TESTS=1 python3 -m unittest tests.test_retrieval_vector_fusion

# 真实双通道分数分布探针（只读，需 VECTOR_API_KEY）
.venv/bin/python scripts/probe_retrieval_scores.py
```

> 注意：1.1 的修复会改变向量证据的入选行为，`vector_only_expected_join_pattern_ids` 类 case 必须在 vector 启用下验证，不能只靠默认 vector-off 的 eval。
