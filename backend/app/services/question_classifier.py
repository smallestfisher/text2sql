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
        classification, score_gap, score_details = self._classify_from_scores(
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
        )
        if score_gap < 0.08:
            warnings.append(
                "classification is near boundary: "
                + ", ".join(f"{key}={value:.3f}" for key, value in score_details.items())
            )

        llm_hint = self._classify_with_llm(
            original_question=question,
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=classification,
            candidate_scores=score_details,
            ambiguous=score_gap < 0.12,
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

    def _classify_from_scores(
        self,
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> tuple[QuestionClassification, float, dict[str, float]]:
        scores = {
            question_type: 0.0
            for question_type in self._allowed_question_types(semantic_parse, session_state)
        }

        self._score_follow_up(scores, semantic_parse, session_state, semantic_diff)
        self._score_new_related(scores, semantic_parse, session_state, semantic_diff)
        self._score_new_unrelated(scores, semantic_parse, session_state, semantic_diff)
        self._score_clarification(scores, semantic_parse, semantic_diff)
        self._apply_rule_bonuses(scores, semantic_parse, session_state, semantic_diff)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_type, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        score_gap = round(top_score - second_score, 6)

        if top_type == "clarification_needed":
            classification = QuestionClassification(
                question_type="clarification_needed",
                subject_domain=semantic_parse.subject_domain,
                inherit_context=False,
                confidence=self._confidence_from_score(top_score, score_gap),
                reason=self._clarification_reason(semantic_parse, semantic_diff),
                reason_code=self._clarification_reason_code(semantic_parse, semantic_diff),
                need_clarification=True,
                clarification_question=self._clarification_question(semantic_parse, semantic_diff),
            )
            return classification, score_gap, dict(ranked)

        classification = QuestionClassification(
            question_type=top_type,
            subject_domain=self._resolve_subject_domain_for_type(top_type, semantic_parse, session_state),
            inherit_context=top_type == "follow_up",
            confidence=self._confidence_from_score(top_score, score_gap),
            reason=self._reason_for_type(top_type, semantic_parse, semantic_diff),
            reason_code=f"score_{top_type}",
            context_delta=self._build_context_delta(semantic_parse) if top_type == "follow_up" else ContextDelta(),
        )
        return classification, score_gap, dict(ranked)

    def _score_follow_up(
        self,
        scores: dict[str, float],
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> None:
        if "follow_up" not in scores:
            return

        score = 0.0
        if semantic_parse.has_follow_up_cue:
            score += 0.28
        if semantic_diff.get("is_short_followup_fragment"):
            score += 0.22
        if semantic_parse.has_follow_up_cue and semantic_diff.get("has_explicit_slots"):
            score += 0.1
        if semantic_diff.get("only_updates_filters"):
            score += 0.22
        if semantic_diff.get("only_updates_time"):
            score += 0.2
        if semantic_diff.get("only_updates_version"):
            score += 0.2
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            score += 0.24
        if semantic_parse.subject_domain == "unknown":
            score += 0.18
        if semantic_parse.subject_domain == session_state.subject_domain:
            score += 0.12
        if semantic_diff.get("new_filter_fields"):
            score += 0.08
        if semantic_diff.get("reused_filter_fields"):
            score += 0.06
        if semantic_diff.get("time_grain_changed") or semantic_diff.get("version_changed"):
            score += 0.1
        if semantic_parse.has_follow_up_cue and semantic_diff.get("can_execute_without_context"):
            score += 0.08
        if semantic_diff.get("can_execute_without_context"):
            score -= 0.12
        if semantic_diff.get("domain_changed"):
            score -= 0.3
        if semantic_diff.get("introduces_new_topic_signal") and not semantic_parse.has_follow_up_cue:
            score -= 0.15
        scores["follow_up"] += score

    def _score_new_related(
        self,
        scores: dict[str, float],
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> None:
        if "new_related" not in scores:
            return

        score = 0.0
        if semantic_parse.subject_domain == session_state.subject_domain and semantic_parse.subject_domain != "unknown":
            score += 0.3
        if semantic_diff.get("can_execute_without_context"):
            score += 0.26
        if semantic_diff.get("has_independent_target"):
            score += 0.14
        if semantic_parse.matched_metrics:
            score += 0.08
        if semantic_diff.get("introduces_new_topic_signal") and not semantic_diff.get("domain_changed"):
            score += 0.12
        if semantic_parse.has_follow_up_cue:
            score -= 0.08
        if semantic_diff.get("is_short_followup_fragment"):
            score -= 0.12
        if semantic_diff.get("only_updates_filters") or semantic_diff.get("only_updates_time") or semantic_diff.get("only_updates_version"):
            score -= 0.18
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            score -= 0.2
        scores["new_related"] += score

    def _score_new_unrelated(
        self,
        scores: dict[str, float],
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> None:
        if "new_unrelated" not in scores:
            return

        score = 0.0
        if semantic_diff.get("domain_changed"):
            score += 0.56
        if semantic_diff.get("introduces_new_topic_signal"):
            score += 0.16
        if semantic_diff.get("can_execute_without_context"):
            score += 0.12
        if semantic_parse.subject_domain not in {"unknown", session_state.subject_domain}:
            score += 0.08
        if semantic_parse.has_follow_up_cue:
            score -= 0.1
        if semantic_diff.get("is_short_followup_fragment"):
            score -= 0.16
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            score -= 0.24
        if semantic_parse.has_follow_up_cue and not semantic_diff.get("can_execute_without_context"):
            score -= 0.18
        scores["new_unrelated"] += score

    def _score_clarification(
        self,
        scores: dict[str, float],
        semantic_parse: SemanticParse,
        semantic_diff: dict,
    ) -> None:
        if "clarification_needed" not in scores:
            return

        score = 0.0
        if not semantic_parse.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            score += 0.42
        if not semantic_diff.get("parsed_domain_known") and not semantic_parse.matched_metrics:
            score += 0.18
        if not semantic_diff.get("can_execute_without_context") and not semantic_parse.has_follow_up_cue:
            score += 0.16
        if semantic_parse.subject_domain == "unknown" and not semantic_diff.get("new_filter_fields") and semantic_parse.time_context.grain == "unknown":
            score += 0.12
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            score -= 0.18
        if semantic_parse.has_follow_up_cue:
            score -= 0.08
        scores["clarification_needed"] += score

    def _apply_rule_bonuses(
        self,
        scores: dict[str, float],
        semantic_parse: SemanticParse,
        session_state: SessionState,
        semantic_diff: dict,
    ) -> None:
        if self.semantic_runtime is None:
            return
        for rule in self.semantic_runtime.classification_rules():
            question_type = rule.get("question_type")
            if question_type not in scores:
                continue
            if not self._rule_matches(rule, semantic_parse, session_state, semantic_diff):
                continue
            confidence = float(rule.get("confidence", 0.7))
            scores[question_type] += max(0.04, min(confidence - 0.45, 0.24))

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
            replace_entities=semantic_parse.matched_entities,
            replace_metrics=semantic_parse.matched_metrics,
            replace_dimensions=[],
            replace_time_context=semantic_parse.time_context,
            replace_version_context=semantic_parse.version_context,
        )

    def _classify_with_llm(
        self,
        original_question: str,
        semantic_parse: SemanticParse,
        session_state: SessionState | None,
        semantic_diff: dict,
        base_classification: QuestionClassification,
        candidate_scores: dict[str, float],
        ambiguous: bool,
    ) -> dict | None:
        if (
            not ambiguous
            or not self.classification_llm_enabled
            or self.llm_client is None
            or self.prompt_builder is None
            or session_state is None
        ):
            return None
        allowed_question_types = self._allowed_question_types(semantic_parse, session_state)
        if len(allowed_question_types) <= 1:
            return None
        arbitration_context = self._classification_arbitration_context(
            candidate_scores=candidate_scores,
            semantic_parse=semantic_parse,
            semantic_diff=semantic_diff,
            ambiguous=ambiguous,
        )
        prompt_payload = self.prompt_builder.build_classification_prompt(
            question=original_question,
            semantic_parse=semantic_parse,
            session_state=session_state,
            semantic_diff=semantic_diff,
            base_classification=base_classification.model_dump(),
            allowed_question_types=allowed_question_types,
            candidate_scores=candidate_scores,
            arbitration_context=arbitration_context,
        )
        return self.llm_client.generate_classification_hint(prompt_payload)

    def _classification_arbitration_context(
        self,
        candidate_scores: dict[str, float],
        semantic_parse: SemanticParse,
        semantic_diff: dict,
        ambiguous: bool,
    ) -> dict:
        ranked = sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)
        top_candidates = [
            {"question_type": question_type, "score": round(score, 3)}
            for question_type, score in ranked[:2]
        ]
        conflict_signals: list[str] = []
        if semantic_parse.has_follow_up_cue and semantic_diff.get("can_execute_without_context"):
            conflict_signals.append("follow_up_cue_but_independent_execution_possible")
        if semantic_diff.get("introduces_new_topic_signal") and not semantic_diff.get("domain_changed"):
            conflict_signals.append("new_topic_signal_inside_same_domain")
        if semantic_diff.get("metrics_missing_but_context_resolvable"):
            conflict_signals.append("metric_missing_but_session_can_supply_it")
        if semantic_diff.get("domain_changed") and semantic_parse.has_follow_up_cue:
            conflict_signals.append("domain_changed_but_user_used_follow_up_language")
        return {
            "needs_arbitration": ambiguous,
            "top_candidates": top_candidates,
            "conflict_signals": conflict_signals,
            "decision_goal": "choose the best classification among the top local candidates and explain why nearby alternatives lose",
        }

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
        context_delta = self._context_delta_from_hint(hint, semantic_parse, inherit_context)

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
        semantic_parse: SemanticParse,
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
        return self._build_context_delta(semantic_parse)

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

    def _resolve_subject_domain_for_type(
        self,
        question_type: str,
        semantic_parse: SemanticParse,
        session_state: SessionState,
    ) -> str:
        if question_type == "follow_up":
            return session_state.subject_domain
        return semantic_parse.subject_domain

    def _confidence_from_score(self, score: float, score_gap: float) -> float:
        return max(0.55, min(0.96, round(0.58 + score * 0.28 + score_gap * 0.45, 3)))

    def _reason_for_type(self, question_type: str, semantic_parse: SemanticParse, semantic_diff: dict) -> str:
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

    def _clarification_reason(self, semantic_parse: SemanticParse, semantic_diff: dict) -> str:
        if not semantic_parse.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return "识别到部分语义，但缺少稳定执行所需的核心指标。"
        return "当前问题仍缺少足够信息，无法稳定判断是否应继承上下文。"

    def _clarification_reason_code(self, semantic_parse: SemanticParse, semantic_diff: dict) -> str:
        if not semantic_parse.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return "missing_metric"
        return "classification_fallback"

    def _clarification_question(self, semantic_parse: SemanticParse, semantic_diff: dict) -> str:
        if not semantic_parse.matched_metrics and not semantic_diff.get("metrics_missing_but_context_resolvable"):
            return self._clarification_message(
                "missing_metric",
                "请补充要查询的指标，例如库存量、计划投入量、实际产出或销售业绩。",
            )
        return self._clarification_message(
            "fallback",
            "请补充查询目标、时间范围或统计口径。",
        )

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
            if not self._context_delta_has_updates(candidate.context_delta):
                reasons.append("follow-up classification must provide context delta")

        if candidate.question_type == "new_unrelated":
            if candidate.subject_domain in {"unknown", session_state.subject_domain}:
                reasons.append("new unrelated classification must switch to a new known domain")

        if candidate.question_type == "new_related":
            if candidate.subject_domain != semantic_parse.subject_domain:
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
            or context_delta.replace_time_context.grain != "unknown"
        )
