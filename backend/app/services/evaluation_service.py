from __future__ import annotations

import json
from pathlib import Path
import uuid

from backend.app.config import EVAL_CASES_PATH, EVAL_RUNS_PATH
from backend.app.models.api import PlanRequest
from backend.app.models.evaluation import (
    EvaluationCase,
    EvaluationCaseCollection,
    EvaluationResultItem,
    EvaluationRunRecord,
    EvaluationRunRequest,
)


class EvaluationService:
    def __init__(
        self,
        orchestrator,
        eval_cases_path: Path = EVAL_CASES_PATH,
        eval_runs_path: Path = EVAL_RUNS_PATH,
    ) -> None:
        self.orchestrator = orchestrator
        self.eval_cases_path = eval_cases_path
        self.eval_runs_path = eval_runs_path
        self.eval_cases_path.parent.mkdir(parents=True, exist_ok=True)
        self.eval_runs_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.eval_cases_path.exists():
            self.eval_cases_path.write_text('[]\n', encoding='utf-8')
        if not self.eval_runs_path.exists():
            self.eval_runs_path.write_text('[]\n', encoding='utf-8')

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
        return self._load_runs()

    def run(self, request: EvaluationRunRequest) -> EvaluationRunRecord:
        cases = self._load_cases()
        if request.case_ids:
            case_lookup = {item.id: item for item in cases}
            selected = [case_lookup[item] for item in request.case_ids if item in case_lookup]
        else:
            selected = cases

        items: list[EvaluationResultItem] = []
        for case in selected:
            response = self._run_case(case, request)
            failures = self._evaluate_case(case, response)
            items.append(
                EvaluationResultItem(
                    case_id=case.id,
                    question=case.question,
                    classification_question_type=response.classification.question_type,
                    classification_domain=response.classification.subject_domain,
                    answer_status=response.answer.status if response.answer else None,
                    plan_valid=response.plan_validation.valid,
                    sql_valid=response.sql_validation.valid,
                    executed=bool(response.execution and response.execution.executed),
                    passed=not failures,
                    failures=failures,
                    warnings=list(response.plan_validation.warnings) + list(response.sql_validation.warnings),
                )
            )

        run = EvaluationRunRecord(
            run_id=f"eval_{uuid.uuid4().hex[:12]}",
            case_count=len(items),
            passed_count=sum(1 for item in items if item.passed),
            failed_count=sum(1 for item in items if not item.passed),
            items=items,
        )
        runs = self._load_runs()
        runs.append(run)
        self._save_runs(runs)
        return run

    def _run_case(self, case: EvaluationCase, request: EvaluationRunRequest):
        session_state = None
        for question in case.session_questions:
            seed_response = self.orchestrator.chat(
                PlanRequest(
                    question=question,
                    session_state=session_state,
                    user_context=request.user_context,
                )
            )
            session_state = seed_response.next_session_state
        return self.orchestrator.chat(
            PlanRequest(
                question=case.question,
                session_state=session_state,
                user_context=request.user_context,
            )
        )

    def _evaluate_case(self, case: EvaluationCase, response) -> list[str]:
        failures: list[str] = []
        answer_status = response.answer.status if response.answer else None
        terminal_non_sql_statuses = {"clarification_needed", "invalid"}
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
            actual_metrics = set(response.query_plan.metrics)
            missing_metrics = [item for item in case.expected_metrics if item not in actual_metrics]
            if missing_metrics:
                failures.append("missing_metrics=" + ",".join(missing_metrics))
        should_require_sql = answer_status not in terminal_non_sql_statuses
        if not response.plan_validation.valid and should_require_sql:
            failures.append("plan_validation_failed")
        if not response.sql_validation.valid and should_require_sql:
            failures.append("sql_validation_failed")
        return failures

    def _load_cases(self) -> list[EvaluationCase]:
        payload = json.loads(self.eval_cases_path.read_text(encoding='utf-8'))
        return [EvaluationCase(**item) for item in payload]

    def _save_cases(self, cases: list[EvaluationCase]) -> None:
        self.eval_cases_path.write_text(
            json.dumps([item.model_dump(mode='json') for item in cases], ensure_ascii=False, indent=2) + "\n",
            encoding='utf-8',
        )

    def _load_runs(self) -> list[EvaluationRunRecord]:
        payload = json.loads(self.eval_runs_path.read_text(encoding='utf-8'))
        return [EvaluationRunRecord(**item) for item in payload]

    def _save_runs(self, runs: list[EvaluationRunRecord]) -> None:
        self.eval_runs_path.write_text(
            json.dumps([item.model_dump(mode='json') for item in runs], ensure_ascii=False, indent=2) + "\n",
            encoding='utf-8',
        )
