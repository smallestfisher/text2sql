from __future__ import annotations

import logging

from backend.app.models.classification import QuestionClassification, QueryIntent
from backend.app.models.query_plan import ContextDelta
from backend.app.models.session_state import SessionState
from backend.app.services.llm_client import LLMClient
from backend.app.services.prompt_builder import PromptBuilder
from backend.app.services.semantic_runtime import SemanticRuntime


logger = logging.getLogger(__name__)


class QuestionClassifier:
    def __init__(
        self,
        semantic_runtime: SemanticRuntime | None = None,
        llm_client: LLMClient | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.semantic_runtime = semantic_runtime
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self._last_debug_info: dict = {}

    def last_debug_info(self) -> dict:
        return dict(self._last_debug_info)

    def classify(
        self,
        question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None = None,
    ) -> tuple[QuestionClassification, list[str]]:
        warnings: list[str] = []
        normalized_question = query_intent.normalized_question
        self._last_debug_info = {
            "normalized_question": normalized_question,
            "subject_domain": query_intent.subject_domain,
            "matched_metrics": list(query_intent.matched_metrics),
            "matched_entities": list(query_intent.matched_entities),
            "filter_fields": [item.field for item in query_intent.filters],
            "requested_dimensions": list(query_intent.requested_dimensions),
        }
        if self._is_invalid_smalltalk(normalized_question):
            classification = QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=0.98,
                reason="问题不属于数据查询请求。",
                reason_code="invalid_smalltalk",
                need_clarification=False,
            )
            self._last_debug_info.update(
                {
                    "decision_source": "hard_guard",
                    "decision": classification.question_type,
                    "reason_code": classification.reason_code,
                }
            )
            return classification, warnings

        relevance_hint = self._check_relevance_with_llm(
            original_question=question,
            query_intent=query_intent,
            session_state=session_state,
        )
        if relevance_hint is not None and self._relevance_hint_is_out_of_scope(relevance_hint):
            reason = relevance_hint.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                reason = "当前输入不属于当前系统支持的业务数据查询范围。"
            classification = QuestionClassification(
                question_type="invalid",
                subject_domain="unknown",
                inherit_context=False,
                confidence=self._sanitize_confidence(relevance_hint.get("confidence"), 0.86),
                reason=reason,
                reason_code="llm_out_of_scope",
                need_clarification=False,
            )
            self._last_debug_info.update(
                {
                    "decision_source": "relevance_guard",
                    "relevance_hint": relevance_hint,
                    "decision": classification.question_type,
                    "reason_code": classification.reason_code,
                }
            )
            return classification, warnings

        if not query_intent.matched_metrics and query_intent.subject_domain == "unknown":
            classification = QuestionClassification(
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
            )
            self._last_debug_info.update(
                {
                    "decision_source": "hard_guard",
                    "decision": classification.question_type,
                    "reason_code": classification.reason_code,
                }
            )
            return classification, warnings

        if not query_intent.matched_metrics and session_state is None:
            classification = QuestionClassification(
                question_type="clarification_needed",
                subject_domain=query_intent.subject_domain,
                inherit_context=False,
                confidence=0.9,
                reason="识别到业务域，但缺少稳定执行所需的核心指标。",
                reason_code="missing_metric",
                need_clarification=True,
                clarification_question=self._clarification_message(
                    "missing_metric",
                    "请补充要查询的指标，例如库存量、计划投入量、实际产出或销售业绩。",
                ),
            )
            self._last_debug_info.update(
                {
                    "decision_source": "hard_guard",
                    "decision": classification.question_type,
                    "reason_code": classification.reason_code,
                }
            )
            return classification, warnings

        if session_state is None:
            classification = QuestionClassification(
                question_type="new",
                subject_domain=query_intent.subject_domain,
                inherit_context=False,
                confidence=0.9,
                reason="当前没有可继承会话，按新问题处理。",
                reason_code="no_session_context",
                context_delta=ContextDelta(),
            )
            self._last_debug_info.update(
                {
                    "decision_source": "hard_guard",
                    "decision": classification.question_type,
                    "reason_code": classification.reason_code,
                }
            )
            return classification, warnings

        semantic_diff = self._semantic_diff(query_intent, session_state)
        baseline_classification, score_gap, score_details = self._classify_from_scores(
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        if score_gap < 0.08:
            warnings.append(
                "classification is near boundary: "
                + ", ".join(f"{key}={value:.3f}" for key, value in score_details.items())
            )
            logger.info(
                "classification near boundary question=%s score_gap=%.3f scores=%s",
                question,
                score_gap,
                score_details,
            )

        llm_hint = self._classify_with_llm_primary(
            original_question=question,
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=baseline_classification,
            candidate_scores=score_details,
            ambiguous=score_gap < 0.12,
        )

        classification: QuestionClassification
        if llm_hint is None or llm_hint.get("mode") != "live":
            classification = self._llm_classification_unavailable(baseline_classification)
        else:
            candidate = self._apply_llm_hint(
                hint=llm_hint,
                query_intent=query_intent,
                session_state=session_state,
                base_classification=baseline_classification,
            )
            acceptable, rejection_reasons = self._llm_classification_is_acceptable(
                candidate=candidate,
                query_intent=query_intent,
                session_state=session_state,
            )
            if acceptable:
                classification = candidate
            else:
                warnings.append(
                    "llm classification hint rejected: " + "; ".join(rejection_reasons)
                )
                classification = self._llm_classification_rejected(
                    base_classification=baseline_classification,
                    rejection_reasons=rejection_reasons,
                )

        self._last_debug_info.update(
            {
                "decision_source": self._decision_source(classification, baseline_classification, llm_hint),
                "semantic_diff": semantic_diff,
                "score_gap": score_gap,
                "score_details": score_details,
                "llm_hint": llm_hint,
                "baseline_classification": baseline_classification.model_dump(mode="json"),
                "decision": classification.question_type,
                "reason_code": classification.reason_code,
                "warnings": list(warnings),
            }
        )

        return classification, warnings

    def _classify_from_scores(
        self,
        query_intent: QueryIntent,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> tuple[QuestionClassification, float, dict[str, float]]:
        scores = self._build_baseline_scores(
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_type, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        score_gap = round(top_score - second_score, 6)

        if top_type == "clarification_needed":
            classification = QuestionClassification(
                question_type="clarification_needed",
                subject_domain=query_intent.subject_domain,
                inherit_context=False,
                confidence=self._confidence_from_score(top_score, score_gap),
                reason=self._clarification_reason(query_intent, semantic_diff),
                reason_code=self._clarification_reason_code(query_intent, semantic_diff),
                need_clarification=True,
                clarification_question=self._clarification_question(query_intent, semantic_diff),
            )
            return classification, score_gap, dict(ranked)

        classification = QuestionClassification(
            question_type=top_type,
            subject_domain=self._resolve_subject_domain_for_type(top_type, query_intent, session_state),
            inherit_context=top_type == "follow_up",
            confidence=self._confidence_from_score(top_score, score_gap),
            reason=self._reason_for_type(top_type, query_intent, semantic_diff),
            reason_code=f"baseline_{top_type}",
            context_delta=self._build_context_delta(query_intent) if top_type == "follow_up" else ContextDelta(),
        )
        return classification, score_gap, dict(ranked)

    def _build_baseline_scores(
        self,
        query_intent: QueryIntent,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> dict[str, float]:
        allowed_question_types = self._allowed_question_types(query_intent, session_state)
        scores = {question_type: 0.0 for question_type in allowed_question_types}

        same_domain = query_intent.subject_domain == session_state.subject_domain and query_intent.subject_domain != "unknown"
        strong_follow_up = self._has_strong_follow_up_cue(query_intent.normalized_question)
        explicit_update = any(
            semantic_diff.get(key)
            for key in (
                "only_updates_filters",
                "only_updates_dimensions",
                "only_updates_time",
                "only_updates_version",
                "only_updates_sort",
                "only_updates_limit",
            )
        )

        if "follow_up" in scores:
            scores["follow_up"] += 0.45 if query_intent.has_follow_up_cue else 0.0
            scores["follow_up"] += 0.18 if strong_follow_up else 0.0
            scores["follow_up"] += 0.32 if explicit_update else 0.0
            scores["follow_up"] += 0.22 if semantic_diff.get("metrics_missing_but_context_resolvable") else 0.0
            scores["follow_up"] += 0.12 if semantic_diff.get("is_short_followup_fragment") else 0.0
            scores["follow_up"] += 0.10 if query_intent.subject_domain == "unknown" else 0.0
            scores["follow_up"] += 0.08 if same_domain else 0.0
            scores["follow_up"] -= 0.35 if semantic_diff.get("domain_changed") else 0.0
            scores["follow_up"] -= 0.14 if semantic_diff.get("can_execute_without_context") and not query_intent.has_follow_up_cue else 0.0

        if "new_related" in scores:
            scores["new_related"] += 0.35 if same_domain else 0.0
            scores["new_related"] += 0.30 if semantic_diff.get("can_execute_without_context") else 0.0
            scores["new_related"] += 0.16 if semantic_diff.get("has_independent_target") else 0.0
            scores["new_related"] += 0.08 if query_intent.matched_metrics else 0.0
            scores["new_related"] -= 0.18 if query_intent.has_follow_up_cue else 0.0
            scores["new_related"] -= 0.16 if explicit_update else 0.0
            scores["new_related"] -= 0.16 if semantic_diff.get("metrics_missing_but_context_resolvable") else 0.0

        if "new_unrelated" in scores:
            scores["new_unrelated"] += 0.62 if semantic_diff.get("domain_changed") else 0.0
            scores["new_unrelated"] += 0.14 if semantic_diff.get("introduces_new_topic_signal") else 0.0
            scores["new_unrelated"] += 0.12 if semantic_diff.get("can_execute_without_context") else 0.0
            scores["new_unrelated"] -= 0.16 if query_intent.has_follow_up_cue else 0.0
            scores["new_unrelated"] -= 0.18 if semantic_diff.get("metrics_missing_but_context_resolvable") else 0.0

        if "clarification_needed" in scores:
            scores["clarification_needed"] += 0.48 if not query_intent.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable") else 0.0
            scores["clarification_needed"] += 0.22 if query_intent.subject_domain == "unknown" and not query_intent.matched_metrics else 0.0
            scores["clarification_needed"] += 0.12 if not semantic_diff.get("can_execute_without_context") and not query_intent.has_follow_up_cue else 0.0
            scores["clarification_needed"] -= 0.12 if query_intent.has_follow_up_cue else 0.0
            scores["clarification_needed"] -= 0.16 if semantic_diff.get("metrics_missing_but_context_resolvable") else 0.0

        return scores

    def _semantic_diff(
        self,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict:
        if self.semantic_runtime is None:
            return {}
        return self.semantic_runtime.session_semantic_diff(query_intent, session_state)

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

    def _has_strong_follow_up_cue(self, normalized_question: str) -> bool:
        strong_cues = ("换成", "改成", "只看", "再看", "继续", "然后", "那")
        return normalized_question.startswith(strong_cues) or any(
            cue in normalized_question for cue in strong_cues
        )

    def _build_context_delta(self, query_intent: QueryIntent) -> ContextDelta:
        if self.semantic_runtime is not None:
            return self.semantic_runtime.build_context_delta(query_intent)
        return ContextDelta(
            add_filters=query_intent.filters,
            remove_filters=[],
            replace_entities=query_intent.matched_entities,
            replace_metrics=query_intent.matched_metrics,
            replace_dimensions=[],
            replace_time_context=query_intent.time_context,
            replace_version_context=query_intent.version_context,
        )

    def _check_relevance_with_llm(
        self,
        original_question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> dict | None:
        if (
            self.llm_client is None
            or self.prompt_builder is None
            or not self._should_run_relevance_guard(query_intent, session_state)
        ):
            return None
        prompt_payload = self.prompt_builder.build_relevance_prompt(
            question=original_question,
            query_intent=query_intent,
            session_state=session_state,
        )
        return self.llm_client.check_question_relevance(prompt_payload)

    def _should_run_relevance_guard(
        self,
        query_intent: QueryIntent,
        session_state: SessionState | None,
    ) -> bool:
        if query_intent.subject_domain == "unknown":
            return True
        if query_intent.matched_metrics:
            return False
        if query_intent.has_follow_up_cue and session_state is not None:
            return False
        return True

    def _relevance_hint_is_out_of_scope(self, hint: dict) -> bool:
        if hint.get("mode") != "live":
            return False
        if hint.get("decision") != "out_of_scope":
            return False
        confidence = self._sanitize_confidence(hint.get("confidence"), 0.0)
        return confidence >= 0.7

    def _classify_with_llm_primary(
        self,
        original_question: str,
        query_intent: QueryIntent,
        session_state: SessionState | None,
        semantic_diff: dict,
        base_classification: QuestionClassification,
        candidate_scores: dict[str, float],
        ambiguous: bool,
    ) -> dict | None:
        if (
            self.llm_client is None
            or self.prompt_builder is None
            or session_state is None
        ):
            return None
        allowed_question_types = self._allowed_question_types(query_intent, session_state)
        if len(allowed_question_types) <= 1:
            return None
        arbitration_context = self._classification_arbitration_context(
            candidate_scores=candidate_scores,
            query_intent=query_intent,
            semantic_diff=semantic_diff,
            ambiguous=ambiguous,
        )
        arbitration_context["llm_role"] = "primary_classifier"
        arbitration_context["baseline_classification"] = base_classification.model_dump(mode="json")
        prompt_payload = self.prompt_builder.build_classification_prompt(
            question=original_question,
            query_intent=query_intent,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=base_classification.model_dump(),
            allowed_question_types=allowed_question_types,
            candidate_scores=candidate_scores,
            arbitration_context=arbitration_context,
        )
        return self.llm_client.generate_classification_hint(prompt_payload)

    def _decision_source(
        self,
        final_classification: QuestionClassification,
        baseline_classification: QuestionClassification,
        llm_hint: dict | None,
    ) -> str:
        if llm_hint is None or llm_hint.get("mode") != "live":
            return "llm_unavailable"
        if final_classification.reason_code == "llm_classification_rejected":
            return "llm_rejected"
        if final_classification.model_dump(mode="json") == baseline_classification.model_dump(mode="json"):
            return "llm_aligned_with_baseline"
        return "llm_primary"

    def _llm_classification_unavailable(
        self,
        base_classification: QuestionClassification,
    ) -> QuestionClassification:
        return QuestionClassification(
            question_type="clarification_needed",
            subject_domain=base_classification.subject_domain,
            inherit_context=False,
            confidence=0.55,
            reason="当前分类主链路不可用，暂不继续执行。",
            reason_code="llm_classification_unavailable",
            need_clarification=True,
            clarification_question="请稍后重试，或补充更明确的查询目标、时间范围和统计口径。",
            context_delta=ContextDelta(),
        )

    def _llm_classification_rejected(
        self,
        *,
        base_classification: QuestionClassification,
        rejection_reasons: list[str],
    ) -> QuestionClassification:
        return QuestionClassification(
            question_type="clarification_needed",
            subject_domain=base_classification.subject_domain,
            inherit_context=False,
            confidence=0.58,
            reason="当前分类主链路输出未通过结构校验，暂不继续执行。",
            reason_code="llm_classification_rejected",
            need_clarification=True,
            clarification_question="请补充更明确的查询目标、上下文关系或过滤条件。",
            context_delta=ContextDelta(),
        )

    def _classification_arbitration_context(
        self,
        candidate_scores: dict[str, float],
        query_intent: QueryIntent,
        semantic_diff: dict,
        ambiguous: bool,
    ) -> dict:
        ranked = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
        top_candidates = [
            {"question_type": question_type, "score": round(score, 3)}
            for question_type, score in ranked[:2]
        ]
        conflict_signals: list[str] = []
        if query_intent.has_follow_up_cue and semantic_diff.get("can_execute_without_context"):
            conflict_signals.append("follow_up_cue_but_independent_execution_possible")
        if semantic_diff.get("introduces_new_topic_signal") and not semantic_diff.get("domain_changed"):
            conflict_signals.append("new_topic_signal_inside_same_domain")
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            conflict_signals.append("metric_missing_but_session_can_supply_it")
        if semantic_diff.get("domain_changed") and query_intent.has_follow_up_cue:
            conflict_signals.append("domain_changed_but_user_used_follow_up_language")
        return {
            "needs_arbitration": ambiguous,
            "top_candidates": top_candidates,
            "conflict_signals": conflict_signals,
            "decision_goal": "choose the best classification among the top local candidates and explain why nearby alternatives lose",
        }

    def _allowed_question_types(
        self,
        query_intent: QueryIntent,
        session_state: SessionState,
    ) -> list[str]:
        if query_intent.subject_domain == "unknown":
            return ["follow_up", "clarification_needed"]
        if query_intent.subject_domain != session_state.subject_domain:
            return ["new_unrelated", "clarification_needed"]
        return ["follow_up", "new_related", "clarification_needed"]

    def _apply_llm_hint(
        self,
        hint: dict,
        query_intent: QueryIntent,
        session_state: SessionState,
        base_classification: QuestionClassification,
    ) -> QuestionClassification:
        question_type = hint.get("question_type")
        if not isinstance(question_type, str):
            return base_classification

        subject_domain = self._resolve_llm_subject_domain(
            question_type=question_type,
            query_intent=query_intent,
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
        context_delta = self._context_delta_from_hint(hint, query_intent, inherit_context)

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
            context_delta=context_delta,
            need_clarification=question_type == "clarification_needed",
            clarification_question=clarification_question if question_type == "clarification_needed" else None,
        )

    def _context_delta_from_hint(
        self,
        hint: dict,
        query_intent: QueryIntent,
        inherit_context: bool,
    ) -> ContextDelta:
        if not inherit_context:
            return ContextDelta()
        payload = hint.get("context_delta")
        if isinstance(payload, dict):
            try:
                return ContextDelta(**payload)
            except Exception:
                pass
        return self._build_context_delta(query_intent)

    def _resolve_llm_subject_domain(
        self,
        question_type: str,
        query_intent: QueryIntent,
        session_state: SessionState,
        hinted_domain,
    ) -> str:
        if question_type == "follow_up":
            return session_state.subject_domain
        if question_type == "new_unrelated":
            return query_intent.subject_domain
        if question_type == "new_related":
            return query_intent.subject_domain
        if isinstance(hinted_domain, str) and hinted_domain in {
            query_intent.subject_domain,
            session_state.subject_domain,
            "unknown",
        }:
            return hinted_domain
        if query_intent.subject_domain != "unknown":
            return query_intent.subject_domain
        return session_state.subject_domain

    def _resolve_subject_domain_for_type(
        self,
        question_type: str,
        query_intent: QueryIntent,
        session_state: SessionState,
    ) -> str:
        if question_type == "follow_up":
            return session_state.subject_domain
        return query_intent.subject_domain

    def _confidence_from_score(self, score: float, score_gap: float) -> float:
        return max(0.55, min(0.96, round(0.58 + score * 0.28 + score_gap * 0.45, 3)))

    def _reason_for_type(self, question_type: str, query_intent: QueryIntent, semantic_diff: dict) -> str:
        if question_type == "follow_up":
            if semantic_diff.get("only_updates_time"):
                return "当前问题主要是在延续上一轮主题并调整时间范围。"
            if semantic_diff.get("only_updates_version"):
                return "当前问题主要是在延续上一轮主题并调整版本条件。"
            if semantic_diff.get("only_updates_filters"):
                return "当前问题主要是在延续上一轮主题并补充筛选条件。"
            return "当前问题更像对上一轮查询的追问或条件改写。"
        if question_type == "new_related":
            return "当前问题与上一轮仍属于同一业务主题，但已具备独立执行条件。"
        if question_type == "new_unrelated":
            return "当前问题切换到了新的业务主题，不应继承上一轮上下文。"
        return "当前问题需要补充信息后再生成稳定查询。"

    def _clarification_reason(self, query_intent: QueryIntent, semantic_diff: dict) -> str:
        if not query_intent.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return "识别到部分语义，但缺少稳定执行所需的核心指标。"
        return "当前问题仍缺少足够信息，无法稳定判断是否应继承上下文。"

    def _clarification_reason_code(self, query_intent: QueryIntent, semantic_diff: dict) -> str:
        if not query_intent.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return "missing_metric"
        return "classification_ambiguous"

    def _clarification_question(self, query_intent: QueryIntent, semantic_diff: dict) -> str:
        if not query_intent.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return self._clarification_message(
                "missing_metric",
                "请补充要查询的指标，例如库存量、计划投入量、实际产出或销售业绩。",
            )
        return self._clarification_message(
            "clarification",
            "请补充查询目标、时间范围或统计口径。",
        )

    def _sanitize_confidence(self, value, default_value: float) -> float:
        if isinstance(value, (int, float)):
            return max(0.5, min(float(value), 0.99))
        return default_value

    def _llm_classification_is_acceptable(
        self,
        candidate: QuestionClassification,
        query_intent: QueryIntent,
        session_state: SessionState,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        allowed_question_types = self._allowed_question_types(query_intent, session_state)
        if candidate.question_type not in allowed_question_types:
            reasons.append("question type is outside structured allowed set")

        if candidate.question_type == "follow_up":
            if not candidate.inherit_context:
                reasons.append("follow-up classification must inherit context")
            if candidate.subject_domain != session_state.subject_domain:
                reasons.append("follow-up classification must keep session domain")
            if not self._context_delta_has_updates(candidate.context_delta):
                reasons.append("follow-up classification must provide context delta")

        if candidate.question_type == "new_unrelated":
            if candidate.subject_domain in {"unknown", session_state.subject_domain}:
                reasons.append("new unrelated classification must switch to a new known domain")

        if candidate.question_type == "new_related":
            if candidate.subject_domain != query_intent.subject_domain:
                reasons.append("new related classification must keep parsed domain")

        if candidate.question_type == "clarification_needed" and not candidate.clarification_question:
            reasons.append("clarification classification requires clarification question")

        return not reasons, reasons

    def _context_delta_has_updates(self, context_delta: ContextDelta) -> bool:
        return bool(
            context_delta.add_filters
            or context_delta.remove_filters
            or context_delta.clear_filters
            or context_delta.replace_entities
            or context_delta.replace_metrics
            or context_delta.replace_dimensions
            or context_delta.replace_sort
            or context_delta.replace_version_context is not None
            or context_delta.replace_limit is not None
            or context_delta.replace_analysis_mode is not None
            or context_delta.replace_time_context.grain != "unknown"
        )
