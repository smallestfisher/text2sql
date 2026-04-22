from __future__ import annotations

from backend.app.models.classification import QuestionClassification, SemanticParse
from backend.app.models.query_plan import ContextDelta
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime


class QuestionClassifier:
    def __init__(
        self,
        semantic_runtime: SemanticRuntime | None = None,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
        classification_llm_enabled: bool = False,
    ) -> None:
        self.semantic_runtime = semantic_runtime
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self.classification_llm_enabled = classification_llm_enabled

    def classify(
        self,
        question: str,
        semantic_parse: SemanticParse,
        session_state: SessionState | None = None,
    ) -> tuple[QuestionClassification, list[str]]:
        warnings: list[str] = []
        normalized_question = semantic_parse.normalized_question
        if self._is_invalid_smalltalk(normalized_question):
            return QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.98,
                reason="问题不属于数据查询请求。",
                reason_code="invalid_smalltalk",
                need_clarification=False,
            ), warnings

        if not semantic_parse.matched_metrics and semantic_parse.subject_domain == "unknown":
            return QuestionClassification(
                question_type="clarification_needed",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.92,
                reason="未识别出明确的业务域和指标。",
                reason_code="unknown_request",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "unknown_request",
                    "请补充你要查询的主题、指标和时间范围，例如库存、计划投入或实际产出。",
                ),
            ), warnings

        if session_state is None:
            return QuestionClassification(
                question_type="new",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.9,
                reason="当前没有可继承会话，按新问题处理。",
                reason_code="no_session_context",
                context_delta=ContextDelta(),
            ), warnings

        semantic_diff = self._semantic_diff(semantic_parse, session_state)
        configured = self._classify_from_rules(
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        if configured is not None:
            classification = configured
        elif not semantic_parse.matched_metrics:
            classification = QuestionClassification(
                question_type="clarification_needed",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.8,
                reason="识别到业务域，但缺少明确指标。",
                reason_code="missing_metric",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "missing_metric",
                    "请补充要查询的指标，例如库存量、计划投入量、实际产出或销售业绩。",
                ),
            )
        else:
            classification = QuestionClassification(
                question_type="clarification_needed",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=0.6,
                reason="当前问题无法稳定分类，建议澄清。",
                reason_code="classification_fallback",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "fallback",
                    "请补充查询目标、时间范围或统计口径。",
                ),
            )

        llm_hint = self._classify_with_llm(
            original_question=question,
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=classification,
        )
        if llm_hint is not None and llm_hint.get("mode") == "live":
            candidate = self._apply_llm_hint(
                hint=llm_hint,
                semantic_parse=semantic_parse,
                session_state=session_state,
                base_classification=classification,
            )
            acceptable, rejection_reasons = self._llm_classification_is_acceptable(
                candidate=candidate,
                semantic_parse=semantic_parse,
                session_state=session_state,
            )
            if acceptable:
                classification = candidate
            else:
                warnings.append(
                    "llm classification hint rejected: " + "; ".join(rejection_reasons)
                )

        return classification, warnings

    def _classify_from_rules(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> QuestionClassification | None:
        if self.semantic_runtime is None:
            return None
        for rule in self.semantic_runtime.classification_rules():
            if not self._rule_matches(rule, semantic_parse, session_state, semantic_diff):
                continue
            subject_domain = self._resolve_subject_domain(rule, semantic_parse, session_state)
            return QuestionClassification(
                question_type=rule.get("question_type", "clarification_needed"),
                subject_domain=subject_domain,
                inherit_context=bool(rule.get("inherit_context", False)),
                confidence=float(rule.get("confidence", 0.7)),
                reason=rule.get("reason"),
                reason_code=rule.get("reason_code") or rule.get("name"),
                context_delta=self._build_context_delta(semantic_parse)
                if rule.get("inherit_context")
                else ContextDelta(),
            )
        return None

    def _rule_matches(
        self,
        rule: dict,
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> bool:
        when = rule.get("when", {})
        if when.get("session_required") and session_state is None:
            return False
        if when.get("subject_domain_equals_session") and semantic_parse.subject_domain != session_state.subject_domain:
            return False
        subject_domain_in = set(when.get("subject_domain_in", []))
        if subject_domain_in and semantic_parse.subject_domain not in subject_domain_in:
            return False
        if "subject_domain_known" in when:
            is_known = semantic_parse.subject_domain != "unknown"
            if bool(when.get("subject_domain_known")) != is_known:
                return False
        if "domain_changed" in when and bool(semantic_diff.get("domain_changed")) != bool(when.get("domain_changed")):
            return False
        all_signals = when.get("all_signals", [])
        if all_signals and not all(self._signal_active(name, semantic_parse, semantic_diff) for name in all_signals):
            return False
        any_signals = when.get("any_signals", [])
        if any_signals and not any(self._signal_active(name, semantic_parse, semantic_diff) for name in any_signals):
            return False
        none_signals = when.get("none_signals", [])
        if none_signals and any(self._signal_active(name, semantic_parse, semantic_diff) for name in none_signals):
            return False
        return True

    def _resolve_subject_domain(
        self,
        rule: dict,
        semantic_parse: SemanticParse,
        session_state: SessionState,
    ) -> str:
        subject_domain = rule.get("subject_domain")
        if subject_domain == "session_subject_domain":
            return session_state.subject_domain
        if subject_domain == "parse_subject_domain" or not subject_domain:
            return semantic_parse.subject_domain
        return subject_domain

    def _signal_active(self, signal: str, semantic_parse: SemanticParse, semantic_diff: dict) -> bool:
        if signal == "has_follow_up_cue":
            return semantic_parse.has_follow_up_cue
        if signal == "has_explicit_slots":
            return semantic_parse.has_explicit_slots
        if signal == "missing_metrics":
            return not semantic_parse.matched_metrics
        value = semantic_diff.get(signal)
        if isinstance(value, list):
            return bool(value)
        return bool(value)

    def _semantic_diff(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
    ) -> dict:
        if self.semantic_runtime is None:
            return {}
        return self.semantic_runtime.session_semantic_diff(semantic_parse, session_state)

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

    def _classify_with_llm(
        self,
        original_question: str,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
        semantic_diff: dict,
        base_classification: QuestionClassification,
    ) -> dict | None:
        if (
            not self.classification_llm_enabled
            or self.llm_client is None
            or self.prompt_builder is None
            or session_state is None
        ):
            return None
        allowed_question_types = self._allowed_question_types(semantic_parse, session_state)
        if len(allowed_question_types) <= 1:
            return None
        prompt_payload = self.prompt_builder.build_classification_prompt(
            question=original_question,
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=base_classification.model_dump(),
            allowed_question_types=allowed_question_types,
        )
        return self.llm_client.generate_classification_hint(prompt_payload)

    def _allowed_question_types(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState,
    ) -> list[str]:
        if semantic_parse.subject_domain == "unknown":
            return ["follow_up", "clarification_needed"]
        if semantic_parse.subject_domain != session_state.subject_domain:
            return ["new_unrelated", "clarification_needed"]
        return ["follow_up", "new_related", "clarification_needed"]

    def _apply_llm_hint(
        self,
        hint: dict,
        semantic_parse: SemanticParse,
        session_state: SessionState,
        base_classification: QuestionClassification,
    ) -> QuestionClassification:
        question_type = hint.get("question_type")
        if not isinstance(question_type, str):
            return base_classification

        subject_domain = self._resolve_llm_subject_domain(
            question_type=question_type,
            semantic_parse=semantic_parse,
            session_state=session_state,
            hinted_domain=hint.get("subject_domain"),
        )
        confidence = hint.get("confidence")
        reason = hint.get("reason") if isinstance(hint.get("reason"), str) else base_classification.reason
        clarification_question = (
            hint.get("clarification_question")
            if isinstance(hint.get("clarification_question"), str)
            else base_classification.clarification_question
        )
        inherit_context = bool(hint.get("inherit_context", question_type == "follow_up"))
        if question_type != "follow_up":
            inherit_context = False

        return QuestionClassification(
            question_type=question_type,
            subject_domain=subject_domain,
            inherit_context=inherit_context,
            confidence=self._sanitize_confidence(confidence, base_classification.confidence),
            reason=reason,
            reason_code=(
                hint.get("reason_code")
                if isinstance(hint.get("reason_code"), str)
                else base_classification.reason_code
            ),
            context_delta=self._build_context_delta(semantic_parse) if inherit_context else ContextDelta(),
            need_clarification=question_type == "clarification_needed",
            clarification_question=clarification_question if question_type == "clarification_needed" else None,
        )

    def _resolve_llm_subject_domain(
        self,
        question_type: str,
        semantic_parse: SemanticParse,
        session_state: SessionState,
        hinted_domain,
    ) -> str:
        if question_type == "follow_up":
            return session_state.subject_domain
        if question_type == "new_unrelated":
            return semantic_parse.subject_domain
        if question_type == "new_related":
            return semantic_parse.subject_domain
        if isinstance(hinted_domain, str) and hinted_domain in {
            semantic_parse.subject_domain,
            session_state.subject_domain,
            "unknown",
        }:
            return hinted_domain
        if semantic_parse.subject_domain != "unknown":
            return semantic_parse.subject_domain
        return session_state.subject_domain

    def _sanitize_confidence(self, value, fallback: float) -> float:
        if isinstance(value, (int, float)):
            return max(0.5, min(float(value), 0.99))
        return fallback

    def _llm_classification_is_acceptable(
        self,
        candidate: QuestionClassification,
        semantic_parse: SemanticParse,
        session_state: SessionState,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        allowed_question_types = self._allowed_question_types(semantic_parse, session_state)
        if candidate.question_type not in allowed_question_types:
            reasons.append("question type is outside structured allowed set")

        if candidate.question_type == "follow_up":
            if not candidate.inherit_context:
                reasons.append("follow-up classification must inherit context")
            if candidate.subject_domain != session_state.subject_domain:
                reasons.append("follow-up classification must keep session domain")

        if candidate.question_type == "new_unrelated":
            if candidate.subject_domain in {"unknown", session_state.subject_domain}:
                reasons.append("new unrelated classification must switch to a new known domain")

        if candidate.question_type == "new_related":
            if candidate.subject_domain != semantic_parse.subject_domain:
                reasons.append("new related classification must keep parsed domain")

        if candidate.question_type == "clarification_needed" and not candidate.clarification_question:
            reasons.append("clarification classification requires clarification question")

        return not reasons, reasons
