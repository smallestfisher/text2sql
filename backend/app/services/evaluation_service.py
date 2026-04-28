from __future__ import annotations

import json
from pathlib import Path
import uuid
from collections import Counter

from backend.app.config import EVAL_CASES_PATH
from backend.app.models.api import PlanRequest
from backend.app.models.conversation import ChatMessage
from backend.app.models.example_library import ExampleRecord
from backend.app.models.auth import UserContext
from backend.app.models.evaluation import (
    EvaluationCase,
    EvaluationCaseCollection,
    EvaluationDimensionSummary,
    EvaluationReplayDiff,
    EvaluationResultItem,
    EvaluationReplayRequest,
    EvaluationReplayResult,
    EvaluationRunRecord,
    EvaluationRunRequest,
    EvaluationSummary,
    RuntimeQueryLogMaterializeCaseRequest,
)


class EvaluationService:
    def __init__(
        self,
        orchestrator,
        eval_cases_path: Path = EVAL_CASES_PATH,
        evaluation_run_repository=None,
        session_repository=None,
        runtime_log_repository=None,
        auth_service=None,
        response_restore_service=None,
    ) -> None:
        self.orchestrator = orchestrator
        self.eval_cases_path = eval_cases_path
        self.evaluation_run_repository = evaluation_run_repository
        self.session_repository = session_repository
        self.runtime_log_repository = runtime_log_repository
        self.auth_service = auth_service
        self.response_restore_service = response_restore_service
        self.eval_cases_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.eval_cases_path.exists():
            self.eval_cases_path.write_text('[]\n', encoding='utf-8')

    def list_cases(self) -> EvaluationCaseCollection:
        cases = self._load_cases()
        return EvaluationCaseCollection(cases=cases, count=len(cases))

    def create_case(self, payload: dict | EvaluationCase) -> EvaluationCase:
        case = payload if isinstance(payload, EvaluationCase) else EvaluationCase(**payload)
        cases = self._load_cases()
        if any(item.id == case.id for item in cases):
            raise ValueError(f"evaluation case id already exists: {case.id}")
        cases.append(case)
        self._save_cases(cases)
        return case

    def list_runs(self) -> list[EvaluationRunRecord]:
        if self.evaluation_run_repository is not None:
            return self.evaluation_run_repository.list_runs()
        return []

    def summarize_runs(self, limit: int = 50) -> EvaluationSummary:
        runs = self.list_runs()[:limit]
        by_domain: dict[str, Counter[str]] = {}
        by_question_type: dict[str, Counter[str]] = {}
        by_answer_status: dict[str, Counter[str]] = {}

        case_count = 0
        passed_count = 0
        failed_count = 0
        for run in runs:
            case_count += run.case_count
            passed_count += run.passed_count
            failed_count += run.failed_count
            for item in run.items:
                self._accumulate_dimension(by_domain, item.classification_domain or "unknown", item.passed)
                self._accumulate_dimension(
                    by_question_type,
                    item.classification_question_type or "unknown",
                    item.passed,
                )
                self._accumulate_dimension(
                    by_answer_status,
                    item.answer_status or "unknown",
                    item.passed,
                )

        return EvaluationSummary(
            run_count=len(runs),
            case_count=case_count,
            passed_count=passed_count,
            failed_count=failed_count,
            by_domain=self._materialize_dimension(by_domain),
            by_question_type=self._materialize_dimension(by_question_type),
            by_answer_status=self._materialize_dimension(by_answer_status),
        )

    def replay_case(self, case_id: str, request: EvaluationReplayRequest) -> EvaluationReplayResult:
        case = self._get_case(case_id)
        replay_user = self._resolve_replay_user(request.user_id) or case.user_context
        response = self._run_question(
            question=case.question,
            session_questions=case.session_questions,
            user_context=replay_user,
        )
        return EvaluationReplayResult(
            source_type="evaluation_case",
            source_id=case.id,
            question=case.question,
            session_questions=list(case.session_questions),
            replay_user=replay_user,
            original_response=None,
            diff=None,
            response=response,
        )

    def replay_trace(self, trace_id: str, request: EvaluationReplayRequest) -> EvaluationReplayResult:
        if self.runtime_log_repository is None:
            raise RuntimeError("runtime log repository is not configured")
        record = self.runtime_log_repository.get_query_log(trace_id)
        if record is None:
            raise KeyError(trace_id)
        if not record.question:
            raise ValueError("query log does not contain question")

        session_questions: list[str] = []
        if request.include_prior_context and record.session_id:
            session_questions = self._load_prior_session_questions(record.session_id, trace_id)

        original_user_id = record.user_id if request.reuse_original_user else None
        replay_user = self._resolve_replay_user(request.user_id, default_user_id=original_user_id)
        response = self._run_question(
            question=record.question,
            session_questions=session_questions,
            user_context=replay_user,
        )
        original_response = self._build_original_response_snapshot(trace_id)
        return EvaluationReplayResult(
            source_type="runtime_query_log",
            source_id=trace_id,
            question=record.question,
            session_questions=session_questions,
            replay_user=replay_user,
            original_trace_id=trace_id,
            original_session_id=record.session_id,
            original_user_id=record.user_id,
            original_response=original_response,
            diff=self._build_replay_diff(original_response, response),
            response=response,
        )

    def materialize_trace_as_case(
        self,
        trace_id: str,
        request: RuntimeQueryLogMaterializeCaseRequest,
    ) -> EvaluationCase:
        if self.runtime_log_repository is None:
            raise RuntimeError("runtime log repository is not configured")
        record = self.runtime_log_repository.get_query_log(trace_id)
        if record is None:
            raise KeyError(trace_id)
        if not record.question:
            raise ValueError("query log does not contain question")

        session_questions: list[str] = []
        if request.include_prior_context and record.session_id:
            session_questions = self._load_prior_session_questions(record.session_id, trace_id)

        original_user_id = record.user_id if request.reuse_original_user else None
        effective_user = self._resolve_replay_user(request.user_id, default_user_id=original_user_id)
        snapshot = self._build_original_response_snapshot(trace_id)
        if snapshot is None:
            raise ValueError("query log does not contain enough trace data to materialize an evaluation case")

        case = EvaluationCase(
            id=request.case_id or self._generate_case_id(trace_id, snapshot.classification.subject_domain),
            question=record.question,
            session_questions=session_questions,
            scenario=request.scenario,
            coverage_tags=list(dict.fromkeys(request.coverage_tags)),
            expected_domain=snapshot.classification.subject_domain,
            expected_question_type=snapshot.classification.question_type,
            expected_metrics=list(snapshot.query_plan.metrics),
            expected_dimensions=list(snapshot.query_plan.dimensions),
            expected_sort_fields=[item.field for item in snapshot.query_plan.sort],
            expected_filter_fields=self._extract_filter_fields(snapshot),
            expected_status=snapshot.answer.status if snapshot.answer is not None else None,
            expected_reason_code=snapshot.classification.reason_code,
            expected_warnings_contains=self._collect_response_warnings(snapshot),
            user_context=effective_user,
            notes=request.notes or f"materialized from runtime query log {trace_id}",
        )
        return self.create_case(case)

    def materialize_trace_as_example(
        self,
        trace_id: str,
        *,
        example_id: str | None = None,
        scenario: str | None = None,
        coverage_tags: list[str] | None = None,
        notes: str | None = None,
    ) -> ExampleRecord:
        if self.runtime_log_repository is None:
            raise RuntimeError("runtime log repository is not configured")
        record = self.runtime_log_repository.get_query_log(trace_id)
        if record is None:
            raise KeyError(trace_id)
        if not record.question:
            raise ValueError("query log does not contain question")

        snapshot = self._build_original_response_snapshot(trace_id)
        if snapshot is None:
            raise ValueError("query log does not contain enough trace data to materialize an example")
        if not snapshot.sql:
            raise ValueError("query log does not contain SQL, cannot materialize example")

        normalized_question = (snapshot.query_intent.normalized_question or record.question).strip()
        effective_scenario = scenario or self._default_example_scenario(snapshot.classification.question_type)
        merged_tags = list(dict.fromkeys([
            snapshot.classification.subject_domain,
            snapshot.classification.question_type,
            *(coverage_tags or []),
        ]))
        return ExampleRecord(
            id=example_id or self._generate_example_id(
                trace_id,
                snapshot.classification.subject_domain,
                snapshot.classification.question_type,
            ),
            question=record.question,
            normalized_question=normalized_question,
            intent=self._generate_example_intent(
                snapshot.classification.subject_domain,
                snapshot.classification.question_type,
                effective_scenario,
            ),
            scenario=effective_scenario,
            coverage_tags=merged_tags,
            subject_domain=snapshot.classification.subject_domain,
            question_type=snapshot.classification.question_type,
            tables=list(snapshot.query_plan.tables),
            entities=list(snapshot.query_plan.entities),
            metrics=list(snapshot.query_plan.metrics),
            dimensions=list(snapshot.query_plan.dimensions),
            filters=list(snapshot.query_plan.filters),
            join_path=list(snapshot.query_plan.join_path),
            sql=snapshot.sql,
            result_shape=snapshot.answer.status if snapshot.answer is not None else None,
            notes=notes or f"materialized from runtime query log {trace_id}",
        )

    def run(self, request: EvaluationRunRequest) -> EvaluationRunRecord:
        cases = self._load_cases()
        if request.case_ids:
            case_lookup = {item.id: item for item in cases}
            selected = [case_lookup[item] for item in request.case_ids if item in case_lookup]
        else:
            selected = cases

        items: list[EvaluationResultItem] = []
        for case in selected:
            effective_user_context = request.user_context if request.user_context is not None else case.user_context
            response = self._run_case(case, request, user_context=effective_user_context)
            failures = self._evaluate_case(case, response)
            actual_warnings = self._collect_response_warnings(response)
            items.append(
                EvaluationResultItem(
                    case_id=case.id,
                    question=case.question,
                    scenario=case.scenario,
                    coverage_tags=list(case.coverage_tags),
                    effective_user_id=effective_user_context.user_id if effective_user_context else None,
                    classification_question_type=response.classification.question_type,
                    classification_domain=response.classification.subject_domain,
                    answer_status=response.answer.status if response.answer else None,
                    actual_reason_code=response.classification.reason_code,
                    actual_metrics=list(response.query_plan.metrics),
                    actual_dimensions=list(response.query_plan.dimensions),
                    actual_filter_fields=self._extract_filter_fields(response),
                    actual_warnings=actual_warnings,
                    plan_valid=response.plan_validation.valid,
                    sql_valid=response.sql_validation.valid,
                    executed=bool(response.execution and response.execution.executed),
                    passed=not failures,
                    failures=failures,
                    warnings=actual_warnings,
                )
            )

        run = EvaluationRunRecord(
            run_id=f"eval_{uuid.uuid4().hex[:12]}",
            case_count=len(items),
            passed_count=sum(1 for item in items if item.passed),
            failed_count=sum(1 for item in items if not item.passed),
            items=items,
        )
        if self.evaluation_run_repository is not None:
            self.evaluation_run_repository.append(run)
        return run

    def _run_question(
        self,
        *,
        question: str,
        session_questions: list[str],
        user_context: UserContext | None,
    ):
        session_state = None
        for seed_question in session_questions:
            seed_response = self.orchestrator.chat(
                PlanRequest(
                    question=seed_question,
                    session_state=session_state,
                    user_context=user_context,
                )
            )
            session_state = seed_response.next_session_state
        return self.orchestrator.chat(
            PlanRequest(
                question=question,
                session_state=session_state,
                user_context=user_context,
            )
        )

    def _run_case(
        self,
        case: EvaluationCase,
        request: EvaluationRunRequest,
        user_context: UserContext | None = None,
    ):
        return self._run_question(
            question=case.question,
            session_questions=case.session_questions,
            user_context=user_context if user_context is not None else request.user_context,
        )

    def _evaluate_case(self, case: EvaluationCase, response) -> list[str]:
        failures: list[str] = []
        answer_status = response.answer.status if response.answer else None
        terminal_non_sql_statuses = {"clarification_needed", "invalid"}
        actual_metrics = list(response.query_plan.metrics)
        actual_dimensions = list(response.query_plan.dimensions)
        actual_filter_fields = self._extract_filter_fields(response)
        actual_reason_code = response.classification.reason_code
        actual_warnings = self._collect_response_warnings(response)
        if case.expected_domain and response.classification.subject_domain != case.expected_domain:
            failures.append(
                f"expected_domain={case.expected_domain}, actual={response.classification.subject_domain}"
            )
        if case.expected_question_type and response.classification.question_type != case.expected_question_type:
            failures.append(
                f"expected_question_type={case.expected_question_type}, actual={response.classification.question_type}"
            )
        if case.expected_status and answer_status != case.expected_status:
            failures.append(
                f"expected_status={case.expected_status}, actual={answer_status}"
            )
        if case.expected_metrics:
            missing_metrics = [item for item in case.expected_metrics if item not in actual_metrics]
            if missing_metrics:
                failures.append("missing_metrics=" + ",".join(missing_metrics))
        if case.unexpected_metrics:
            unexpected_metrics = [item for item in case.unexpected_metrics if item in actual_metrics]
            if unexpected_metrics:
                failures.append("unexpected_metrics=" + ",".join(unexpected_metrics))
        if case.expected_dimensions:
            missing_dimensions = [item for item in case.expected_dimensions if item not in actual_dimensions]
            if missing_dimensions:
                failures.append("missing_dimensions=" + ",".join(missing_dimensions))
        if case.expected_filter_fields:
            missing_filter_fields = [
                item for item in case.expected_filter_fields if item not in actual_filter_fields
            ]
            if missing_filter_fields:
                failures.append("missing_filter_fields=" + ",".join(missing_filter_fields))
        if case.expected_reason_code and actual_reason_code != case.expected_reason_code:
            failures.append(
                f"expected_reason_code={case.expected_reason_code}, actual={actual_reason_code}"
            )
        if case.expected_warnings_contains:
            for expected_warning in case.expected_warnings_contains:
                if not any(expected_warning in warning for warning in actual_warnings):
                    failures.append(f"missing_warning_substring={expected_warning}")
        should_require_sql = answer_status not in terminal_non_sql_statuses
        if not response.plan_validation.valid and should_require_sql:
            failures.append("plan_validation_failed")
        if not response.sql_validation.valid and should_require_sql:
            failures.append("sql_validation_failed")
        return failures

    def _extract_filter_fields(self, response) -> list[str]:
        fields: list[str] = []
        for item in response.query_plan.filters:
            if item.field not in fields:
                fields.append(item.field)
        return fields

    def _collect_response_warnings(self, response) -> list[str]:
        warnings: list[str] = []
        for warning in list(response.plan_validation.warnings) + list(response.sql_validation.warnings):
            if warning not in warnings:
                warnings.append(warning)
        if response.execution is not None:
            for warning in response.execution.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        return warnings

    def _generate_case_id(self, trace_id: str, subject_domain: str | None) -> str:
        prefix = (subject_domain or "unknown").replace("-", "_")
        prefix = prefix.replace(" ", "_")
        return f"runtime_{prefix}_{trace_id[-8:]}"

    def _generate_example_id(
        self,
        trace_id: str,
        subject_domain: str | None,
        question_type: str | None,
    ) -> str:
        domain_prefix = (subject_domain or "unknown").replace("-", "_").replace(" ", "_")
        question_prefix = (question_type or "new").replace("-", "_").replace(" ", "_")
        return f"runtime_{domain_prefix}_{question_prefix}_{trace_id[-8:]}"

    def _generate_example_intent(
        self,
        subject_domain: str | None,
        question_type: str | None,
        scenario: str | None,
    ) -> str:
        parts = [subject_domain or "unknown", question_type or "new"]
        if scenario:
            parts.append(scenario)
        return "_".join(part.replace("-", "_").replace(" ", "_") for part in parts if part)

    def _default_example_scenario(self, question_type: str | None) -> str:
        if question_type == "follow_up":
            return "runtime_follow_up"
        if question_type == "clarification_needed":
            return "runtime_clarification"
        if question_type == "new_related":
            return "runtime_new_related"
        if question_type == "new_unrelated":
            return "runtime_new_unrelated"
        if question_type == "invalid":
            return "runtime_invalid"
        return "runtime_captured"

    def _get_case(self, case_id: str) -> EvaluationCase:
        for item in self._load_cases():
            if item.id == case_id:
                return item
        raise KeyError(case_id)

    def _resolve_replay_user(
        self,
        requested_user_id: str | None,
        default_user_id: str | None = None,
    ) -> UserContext | None:
        target_user_id = requested_user_id or default_user_id
        if not target_user_id:
            return None
        if self.auth_service is None:
            return None
        user = self.auth_service.get_user(target_user_id)
        if user is not None:
            return user
        return self.auth_service.create_stub_user(target_user_id)

    def _load_prior_session_questions(self, session_id: str, trace_id: str) -> list[str]:
        if self.session_repository is None:
            return []
        messages = self.session_repository.list_messages(session_id)
        return self._extract_prior_user_questions(messages, trace_id)

    def _extract_prior_user_questions(self, messages: list[ChatMessage], trace_id: str) -> list[str]:
        session_questions: list[str] = []
        for message in messages:
            if message.role == "user" and message.trace_id == trace_id:
                break
            if message.role == "user" and message.content.strip():
                session_questions.append(message.content.strip())
        return session_questions

    def _build_original_response_snapshot(self, trace_id: str):
        if self.response_restore_service is not None:
            return self.response_restore_service.build_from_trace_id(trace_id)
        if self.runtime_log_repository is None or not hasattr(self.orchestrator, "audit_service"):
            return None
        trace = self.orchestrator.audit_service.get_trace(trace_id)
        sql_audit = self.runtime_log_repository.get_sql_audit(trace_id)
        query_log = self.runtime_log_repository.get_query_log(trace_id)
        if trace is None or query_log is None:
            return None

        classification_metadata = self._step_metadata(trace, "plan")
        compile_metadata = self._step_metadata(trace, "compile_plan")
        validate_plan_metadata = self._step_metadata(trace, "validate_plan")

        from backend.app.models.api import ChatResponse, ExecutionResponse, ValidationResponse
        from backend.app.models.answer import AnswerPayload
        from backend.app.models.classification import QuestionClassification, QueryIntent
        from backend.app.models.query_plan import QueryPlan
        from backend.app.models.retrieval import RetrievalContext
        from backend.app.models.session_state import SessionState

        classification_payload = classification_metadata.get("classification") or {}
        query_intent_payload = classification_metadata.get("query_intent") or classification_metadata.get("semantic_parse") or {}
        compiled_plan_payload = compile_metadata.get("compiled_plan") or {}

        classification = QuestionClassification(**{
            "question_type": classification_payload.get("question_type", query_log.question_type or "new"),
            "subject_domain": classification_payload.get("subject_domain", query_log.subject_domain or "unknown"),
            "inherit_context": classification_payload.get("inherit_context", False),
            "need_clarification": classification_payload.get("need_clarification", False),
            "reason": classification_payload.get("reason"),
            "reason_code": classification_payload.get("reason_code"),
            "clarification_question": classification_payload.get("clarification_question"),
            "context_delta": classification_payload.get("context_delta", {}),
            "confidence": classification_payload.get("confidence", 0.0),
        })
        query_intent = QueryIntent(**{
            "normalized_question": query_intent_payload.get("normalized_question", query_log.question or ""),
            "matched_metrics": query_intent_payload.get("matched_metrics", []),
            "matched_entities": query_intent_payload.get("matched_entities", []),
            "requested_dimensions": query_intent_payload.get("requested_dimensions", []),
            "filters": query_intent_payload.get("filters", []),
            "time_context": query_intent_payload.get("time_context", {}),
            "version_context": query_intent_payload.get("version_context"),
            "subject_domain": query_intent_payload.get("subject_domain", query_log.subject_domain or "unknown"),
            "has_follow_up_cue": query_intent_payload.get("has_follow_up_cue", False),
            "has_explicit_slots": query_intent_payload.get("has_explicit_slots", False),
        })
        query_plan = QueryPlan(**{
            "question_type": compiled_plan_payload.get("question_type", classification.question_type),
            "subject_domain": compiled_plan_payload.get("subject_domain", classification.subject_domain),
            "tables": compiled_plan_payload.get("tables", []),
            "entities": compiled_plan_payload.get("entities", []),
            "metrics": compiled_plan_payload.get("metrics", []),
            "dimensions": compiled_plan_payload.get("dimensions", []),
            "filters": compiled_plan_payload.get("filters", []),
            "join_path": compiled_plan_payload.get("join_path", []),
            "time_context": compiled_plan_payload.get("time_context", {}),
            "version_context": compiled_plan_payload.get("version_context"),
            "inherit_context": compiled_plan_payload.get("inherit_context", classification.inherit_context),
            "context_delta": compiled_plan_payload.get("context_delta", {}),
            "need_clarification": compiled_plan_payload.get("need_clarification", classification.need_clarification),
            "clarification_question": compiled_plan_payload.get("clarification_question", classification.clarification_question),
            "reason_code": compiled_plan_payload.get("reason_code", classification.reason_code),
            "sort": compiled_plan_payload.get("sort", []),
            "limit": compiled_plan_payload.get("limit", 200),
            "reason": compiled_plan_payload.get("reason", classification.reason),
        })
        plan_validation = ValidationResponse(
            valid=bool(query_log.plan_valid),
            errors=validate_plan_metadata.get("errors", []),
            warnings=validate_plan_metadata.get("warnings", []),
            risk_level=query_log.plan_risk_level or "low",
            risk_flags=query_log.plan_risk_flags,
        )
        sql_validation = ValidationResponse(
            valid=bool(query_log.sql_valid),
            errors=sql_audit.errors if sql_audit is not None else [],
            warnings=sql_audit.warnings if sql_audit is not None else [],
            risk_level=sql_audit.sql_risk_level if sql_audit is not None and sql_audit.sql_risk_level else "low",
            risk_flags=sql_audit.sql_risk_flags if sql_audit is not None else [],
        )
        execution = None
        answer = AnswerPayload(status=query_log.answer_status or "stub", summary=query_log.answer_status or "")
        return ChatResponse(
            classification=classification,
            query_intent=query_intent,
            retrieval=RetrievalContext(hits=[]),
            trace=trace,
            answer=answer,
            query_plan=query_plan,
            sql=sql_audit.sql_text if sql_audit is not None else None,
            plan_validation=plan_validation,
            sql_validation=sql_validation,
            execution=execution,
            next_session_state=SessionState(session_id=query_log.session_id),
        )

    def _build_replay_diff(self, original_response, replay_response) -> EvaluationReplayDiff | None:
        if original_response is None:
            return None
        original_metrics = set(original_response.query_plan.metrics)
        replay_metrics = set(replay_response.query_plan.metrics)
        original_dimensions = set(original_response.query_plan.dimensions)
        replay_dimensions = set(replay_response.query_plan.dimensions)
        original_filters = set(self._extract_filter_fields(original_response))
        replay_filters = set(self._extract_filter_fields(replay_response))
        original_plan_flags = set(original_response.plan_validation.risk_flags)
        replay_plan_flags = set(replay_response.plan_validation.risk_flags)
        original_sql_flags = set(original_response.sql_validation.risk_flags)
        replay_sql_flags = set(replay_response.sql_validation.risk_flags)
        original_prompt_context = self._prompt_context_summary(original_response)
        replay_prompt_context = self._prompt_context_summary(replay_response)

        return EvaluationReplayDiff(
            classification_changed=(
                original_response.classification.question_type != replay_response.classification.question_type
                or original_response.classification.subject_domain != replay_response.classification.subject_domain
            ),
            question_type_changed=original_response.classification.question_type != replay_response.classification.question_type,
            subject_domain_changed=original_response.classification.subject_domain != replay_response.classification.subject_domain,
            answer_status_changed=(original_response.answer.status if original_response.answer else None) != (replay_response.answer.status if replay_response.answer else None),
            plan_valid_changed=original_response.plan_validation.valid != replay_response.plan_validation.valid,
            plan_risk_level_changed=original_response.plan_validation.risk_level != replay_response.plan_validation.risk_level,
            sql_valid_changed=original_response.sql_validation.valid != replay_response.sql_validation.valid,
            sql_risk_level_changed=original_response.sql_validation.risk_level != replay_response.sql_validation.risk_level,
            execution_status_changed=(original_response.execution.status if original_response.execution else None) != (replay_response.execution.status if replay_response.execution else None),
            sql_changed=(original_response.sql or "") != (replay_response.sql or ""),
            prompt_context_changed=original_prompt_context != replay_prompt_context,
            original_prompt_context_summary=original_prompt_context,
            replay_prompt_context_summary=replay_prompt_context,
            metrics_added=sorted(replay_metrics - original_metrics),
            metrics_removed=sorted(original_metrics - replay_metrics),
            dimensions_added=sorted(replay_dimensions - original_dimensions),
            dimensions_removed=sorted(original_dimensions - replay_dimensions),
            filter_fields_added=sorted(replay_filters - original_filters),
            filter_fields_removed=sorted(original_filters - replay_filters),
            plan_risk_flags_added=sorted(replay_plan_flags - original_plan_flags),
            plan_risk_flags_removed=sorted(original_plan_flags - replay_plan_flags),
            sql_risk_flags_added=sorted(replay_sql_flags - original_sql_flags),
            sql_risk_flags_removed=sorted(original_sql_flags - replay_sql_flags),
        )

    def _prompt_context_summary(self, response) -> dict:
        trace = getattr(response, "trace", None)
        if trace is None:
            return {}
        metadata = self._step_metadata(trace, "build_sql_prompt")
        summary = metadata.get("context_summary")
        return summary if isinstance(summary, dict) else {}

    def _step_metadata(self, trace, step_name: str) -> dict:
        for step in trace.steps:
            if step.name == step_name and step.metadata:
                return step.metadata
        return {}

    def _load_cases(self) -> list[EvaluationCase]:
        payload = json.loads(self.eval_cases_path.read_text(encoding='utf-8'))
        return [EvaluationCase(**item) for item in payload]

    def _save_cases(self, cases: list[EvaluationCase]) -> None:
        self.eval_cases_path.write_text(
            json.dumps([item.model_dump(mode='json') for item in cases], ensure_ascii=False, indent=2) + "\n",
            encoding='utf-8',
        )

    def _accumulate_dimension(
        self,
        bucket: dict[str, Counter[str]],
        key: str,
        passed: bool,
    ) -> None:
        counter = bucket.setdefault(key, Counter())
        counter["total"] += 1
        if passed:
            counter["passed"] += 1
        else:
            counter["failed"] += 1

    def _materialize_dimension(
        self,
        bucket: dict[str, Counter[str]],
    ) -> list[EvaluationDimensionSummary]:
        return [
            EvaluationDimensionSummary(
                key=key,
                total=counter.get("total", 0),
                passed=counter.get("passed", 0),
                failed=counter.get("failed", 0),
            )
            for key, counter in sorted(
                bucket.items(),
                key=lambda item: (item[1].get("total", 0), item[0]),
                reverse=True,
            )
        ]
