# Text2SQL 架构设计评审

**评审日期**: 2026-06-05
**代码基线**: commit f6146ef

> **现状校准（2026-06-09）**：本文是一份**结构性重构评审快照**，记录评审当日的结论，不随代码逐行更新。基线 commit 之后分支已推进，下方「技术债务量化」表的行数为评审当日值，最新实测见表内括注。本文聚焦类职责与分层（God Object、解耦、Pipeline 化），与聚焦检索准确率/效率的 [RETRIEVAL_ACCURACY_REVIEW.md](./RETRIEVAL_ACCURACY_REVIEW.md)、聚焦 PromptBuilder 拆分的 [TODO.md](./TODO.md) 互补：
> - PromptBuilder 第一阶段 facade 拆分已落地（见 TODO.md），但行数未降，结构债仍在。
> - RetrievalService 的检索质量问题已在 RETRIEVAL_ACCURACY_REVIEW.md 单独处理，但本文关注的「分层」结构债未动。
> - 其余 CRITICAL/HIGH 项（Orchestrator、LLMClient 分层，测试覆盖）尚未开始。

---

## 🔴 CRITICAL 级别问题

### 1. Orchestrator 职责过载 (God Object)

**现状**: `ConversationOrchestrator` 1652 行，14 个依赖注入

**问题**:
- 单个类包含：问题分析、检索、SQL生成、验证、执行、状态管理、进度通知、错误处理
- 60+ 个方法混在一起
- 无法并行开发不同流程
- 几乎无法编写单元测试

**建议**: 采用 Pipeline Pattern

```python
class ConversationPipeline:
    stages = [
        SessionLoadStage(),
        QuestionAnalysisStage(),
        RetrievalStage(),
        SQLGenerationStage(),
        ValidationStage(),
        ExecutionStage()
    ]

    def execute(self, request, context):
        for stage in self.stages:
            context = stage.process(context)
            if context.should_terminate:
                break
        return context.build_response()
```

**优先级**: P0
**工作量**: 5-7 天

---

### 2. RetrievalService 职责混乱

**现状**: 859 行，包含文档构建、BM25、向量索引、结果排序

**问题**:
- 检索算法无法独立测试
- 向量故障会阻塞整个检索
- 修改文档结构需要改检索服务

**建议**: 分层架构

```
DocumentBuilder → BM25Retriever ↘
                                   HybridRetriever
VectorIndexer   → VectorRetriever ↗
```

**优先级**: P0
**工作量**: 4-5 天

---

### 3. LLMClient 耦合过紧

**现状**: 606 行，混合了 HTTP、业务逻辑、缓存、重试、SQL 提取

**问题**:
- 无法切换 LLM Provider
- 测试必须 Mock OpenAI SDK
- 缓存和业务逻辑耦合

**建议**: 分层

```
LLMTransport (HTTP wrapper)
↓
LLMProvider (OpenAI/Anthropic adapter)
↓
PromptExecutor (Retry + Cache)
↓
Domain Services
```

**优先级**: P0
**工作量**: 3-4 天

---

### 4. 测试覆盖不足

**现状**:
- Orchestrator: 无单元测试
- RetrievalService: 无检索质量测试
- LLMClient: 无 Mock 测试

**问题**: 重构无法保证不退化，Bug 修复容易引入新 Bug

**建议**: 测试金字塔
- E2E 10%: 典型用户流程
- 集成 30%: Orchestrator + Services
- 单元 60%: Prompt/SQL/BM25

**目标**: 核心服务 80%+ 覆盖率

**优先级**: P0
**工作量**: 8-10 天

---

## 🟠 HIGH 级别问题

### 5. 错误处理策略分散

**问题**: 异常处理散布各处，未区分可重试/不可重试错误

**建议**: 定义 `ErrorCategory` 枚举和统一的 `ErrorRecoveryPolicy`

---

### 6. 向量检索无降级

**问题**: `_ensure_vector_ready()` 阻塞整个流程

**建议**: 实现超时降级到纯 BM25

---

### 7. SessionState 状态机不清晰

**问题**: 20+ 字段，状态转换逻辑分散

**建议**: 引入显式状态机模式

---

### 8. 缺乏可观测性

**问题**: Trace 格式不一致，缺少结构化 Metrics

**建议**: 引入 OpenTelemetry 或统一的 Telemetry Layer

---

### 9. Prompt 缺乏版本管理

**问题**:
- 手工拼接字符串，难以 A/B 测试
- 无法追踪 prompt 变更历史
- token 使用效率无法度量

**建议**: Prompt Template Registry + Jinja2

```python
class PromptRegistry:
    def get_template(name: str, version: str = "latest")
    def render(template: PromptTemplate, **kwargs)
    def track_usage(template, actual_tokens)
```

---

## 🟡 MEDIUM 级别问题

### 10. Domain 边界不清晰

**问题**: `SqlGenerationContext` 和 `ContextSummary` 职责重叠

**建议**: 引入 Bounded Context 和 Aggregate Root

---

### 11. 配置管理分散

**问题**: 配置散落在多处，缺少统一的配置校验

**建议**: 集中配置管理 + Schema 校验

---

### 12. 缺少性能监控

**问题**: 无法识别慢查询、慢 LLM 调用

**建议**: 添加 APM 和性能日志

---

## 📊 技术债务量化

| 模块 | 行数 | 复杂度 | 职责数 | 紧迫度 |
|------|------|--------|--------|--------|
| Orchestrator | 1652（最新 1677） | 极高 | 14+ | 🔴 Critical |
| RetrievalService | 859（最新 1058） | 高 | 8 | 🔴 Critical |
| LLMClient | 606（最新 635） | 高 | 6 | 🔴 Critical |
| PromptBuilder | 1312（最新 1391） | 中 | 5 | 🟠 High |

> 行数为评审当日值；括注为 2026-06-09 实测。各模块行数均有增长，结构债未减——facade 拆分（PromptBuilder）和检索质量修复（RetrievalService）都是在原类内/旁路推进，未做本文主张的分层。

---

## 🎯 修复路线图

### 第一阶段 (Week 1-2): 测试 + 解耦基础
1. 为 Orchestrator 添加集成测试
2. 拆分 LLMClient 三层架构
3. 为 Prompt Builders 添加单元测试

### 第二阶段 (Week 3-4): 核心重构
4. 实现 Pipeline 替代 Orchestrator
5. RetrievalService 分层重构
6. 引入 Prompt Template Registry

### 第三阶段 (Week 5-6): 质量提升
7. 统一错误处理
8. 添加 Telemetry
9. 测试覆盖率达 80%+

---

## 💡 快速改进建议（低成本）

1. **添加 Protocol 定义**: 为核心服务定义抽象接口
2. **提取 Value Objects**: 将字段组合为领域对象
3. **Metrics 装饰器**: 在现有代码加性能监控
4. **烟雾测试**: 覆盖主要用户路径
5. **统一日志格式**: 结构化日志 + trace_id

---

**维护者**: Architecture Team
**下次评审**: 2026-09-05
