from __future__ import annotations

import json
from pathlib import Path
import uuid
from collections import Counter

from backend.app.config import EVAL_CASES_PATH
from backend.app.models.api import PlanRequest
from backend.app.models.conversation import ChatMessage
from backend.app.models.auth import UserContext
from backend.app.models.evaluation import (
    EvaluationCase,
    EvaluationCaseCollection,
    EvaluationDimensionSummary,
    EvaluationResultItem,
    EvaluationReplayRequest,
    EvaluationReplayResult,
    EvaluationRunRecord,
    EvaluationRunRequest,
    EvaluationSummary,
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
    ) -> None:
        self.orchestrator = orchestrator
        self.eval_cases_path = eval_cases_path
        self.evaluation_run_repository = evaluation_run_repository
        self.session_repository = session_repository
        self.runtime_log_repository = runtime_log_repository
        self.auth_service = auth_service
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
        replay_user = self._resolve_replay_user(request.user_id, fallback_user_id=original_user_id)
        response = self._run_question(
            question=record.question,
            session_questions=session_questions,
            user_context=replay_user,
        )
        return EvaluationReplayResult(
            source_type="runtime_query_log",
            source_id=trace_id,
            question=record.question,
            session_questions=session_questions,
            replay_user=replay_user,
            original_trace_id=trace_id,
            original_session_id=record.session_id,
            original_user_id=record.user_id,
            response=response,
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
                    actual_semantic_views=list(response.query_plan.semantic_views),
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
        actual_semantic_views = list(response.query_plan.semantic_views)
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
        if case.expected_semantic_views:
            missing_semantic_views = [
                item for item in case.expected_semantic_views if item not in actual_semantic_views
            ]
            if missing_semantic_views:
                failures.append("missing_semantic_views=" + ",".join(missing_semantic_views))
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

    def _get_case(self, case_id: str) -> EvaluationCase:
        for item in self._load_cases():
            if item.id == case_id:
                return item
        raise KeyError(case_id)

    def _resolve_replay_user(
        self,
        requested_user_id: str | None,
        fallback_user_id: str | None = None,
    ) -> UserContext | None:
        target_user_id = requested_user_id or fallback_user_id
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
