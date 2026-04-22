from __future__ import annotations

from backend.app.models.classification import QuestionClassification, SemanticParse
from backend.app.models.query_plan import ContextDelta
from backend.app.models.session_state import SessionState
from backend.app.services.semantic_runtime import SemanticRuntime


class QuestionClassifier:
    def __init__(self, semantic_runtime: SemanticRuntime | None = None) -> None:
        self.semantic_runtime = semantic_runtime

    def classify(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState | None = None,
    ) -> QuestionClassification:
        question = semantic_parse.normalized_question
        if self._is_invalid_smalltalk(question):
            return QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.98,
                reason="问题不属于数据查询请求。",
                need_clarification=False,
            )

        if not semantic_parse.matched_metrics and semantic_parse.subject_domain == "unknown":
            return QuestionClassification(
                question_type="clarification_needed",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.92,
                reason="未识别出明确的业务域和指标。",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "unknown_request",
                    "请补充你要查询的主题、指标和时间范围，例如库存、计划投入或实际产出。",
                ),
            )

        if session_state is None:
            return QuestionClassification(
                question_type="new",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.9,
                reason="当前没有可继承会话，按新问题处理。",
                context_delta=ContextDelta(),
            )

        if semantic_parse.subject_domain == "unknown" and (
            semantic_parse.has_follow_up_cue or semantic_parse.has_explicit_slots
        ):
            return QuestionClassification(
                question_type="follow_up",
                subject_domain=session_state.subject_domain,
                inherit_context=True,
                confidence=0.84,
                reason="当前问题缺少完整主体，但明显在延续上一轮查询。",
                context_delta=self._build_context_delta(semantic_parse),
            )

        if semantic_parse.subject_domain == session_state.subject_domain:
            if semantic_parse.has_follow_up_cue or not semantic_parse.matched_metrics:
                return QuestionClassification(
                    question_type="follow_up",
                    subject_domain=semantic_parse.subject_domain,
                    inherit_context=True,
                    confidence=0.88,
                    reason="当前问题延续了上一轮主题，仅变更指标、过滤或粒度。",
                    context_delta=self._build_context_delta(semantic_parse),
                )
            return QuestionClassification(
                question_type="new_related",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.8,
                reason="当前问题与上一轮属于同一主题域，但可独立执行。",
                context_delta=ContextDelta(),
            )

        if semantic_parse.subject_domain != "unknown" and semantic_parse.subject_domain != session_state.subject_domain:
            return QuestionClassification(
                question_type="new_unrelated",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.9,
                reason="当前问题切换到了新的业务主题域，不应继承上一轮上下文。",
                context_delta=ContextDelta(),
            )

        if not semantic_parse.matched_metrics:
            return QuestionClassification(
                question_type="clarification_needed",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.8,
                reason="识别到业务域，但缺少明确指标。",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "missing_metric",
                    "请补充要查询的指标，例如库存量、计划投入量、实际产出或销售业绩。",
                ),
            )

        return QuestionClassification(
            question_type="clarification_needed",
            subject_domain=semantic_parse.subject_domain,
            inherit_context=False,
            confidence=0.6,
            reason="当前问题无法稳定分类，建议澄清。",
            need_clarification=True,
            clarification_question=self._clarification_message(
                "fallback",
                "请补充查询目标、时间范围或统计口径。",
            ),
        )

    def _is_invalid_smalltalk(self, question: str) -> bool:
        stripped = question.strip()
        return stripped in self._invalid_patterns()

    def _invalid_patterns(self) -> set[str]:
        if self.semantic_runtime is None:
            return {"你好", "hello", "hi", "谢谢", "thanks"}
        return self.semantic_runtime.invalid_patterns()

    def _clarification_message(self, key: str, default: str) -> str:
        if self.semantic_runtime is None:
            return default
        return self.semantic_runtime.clarification_message(key, default)

    def _build_context_delta(self, semantic_parse: SemanticParse) -> ContextDelta:
        if self.semantic_runtime is not None:
            return self.semantic_runtime.build_context_delta(semantic_parse)
        return ContextDelta(
            add_filters=semantic_parse.filters,
            remove_filters=[],
            replace_metrics=semantic_parse.matched_metrics,
            replace_dimensions=[],
            replace_time_context=semantic_parse.time_context,
        )
