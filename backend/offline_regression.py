from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import EVAL_CASES_PATH
from backend.app.models.api import ValidationResponse
from backend.app.models.evaluation import EvaluationCase
from backend.app.models.session_state import SessionState
from backend.app.services.answer_builder import AnswerBuilder
from backend.app.services.permission_service import PermissionService
from backend.app.services.policy_engine import PolicyEngine
from backend.app.services.query_plan_compiler import QueryPlanCompiler
from backend.app.services.query_plan_validator import QueryPlanValidator
from backend.app.services.query_planner import QueryPlanner
from backend.app.services.semantic_loader import SemanticLayerLoader
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.session_state_service import SessionStateService


OFFLINE_SQL_SKIP_REASON = (
    "sql generation skipped in offline regression; LLM-first SQL is validated in live/replay paths"
)


def load_cases(path: Path) -> list[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationCase(**item) for item in payload]


def build_components() -> dict[str, Any]:
    semantic_layer = SemanticLayerLoader().load()
    semantic_runtime = SemanticRuntime(semantic_layer)
    return {
        "semantic_layer": semantic_layer,
        "semantic_runtime": semantic_runtime,
        "query_planner": QueryPlanner(
            semantic_layer=semantic_layer,
            semantic_runtime=semantic_runtime,
            classification_llm_enabled=False,
        ),
        "query_plan_compiler": QueryPlanCompiler(semantic_runtime=semantic_runtime),
        "query_plan_validator": QueryPlanValidator(semantic_runtime=semantic_runtime),
        "permission_service": PermissionService(policy_engine=PolicyEngine(semantic_runtime=semantic_runtime)),
        "session_state_service": SessionStateService(),
        "answer_builder": AnswerBuilder(),
    }


def run_question(
    question: str,
    session_state: SessionState | None,
    user_context,
    components: dict[str, Any],
) -> dict[str, Any]:
    semantic_layer = components["semantic_layer"]
    query_planner = components["query_planner"]
    query_plan_compiler = components["query_plan_compiler"]
    query_plan_validator = components["query_plan_validator"]
    permission_service = components["permission_service"]
    session_state_service = components["session_state_service"]
    answer_builder = components["answer_builder"]

    semantic_parse, classification, query_plan, planner_warnings = query_planner.create_plan(
        question=question,
        session_state=session_state,
    )
    query_plan, permission_warnings = permission_service.apply_to_query_plan(
        query_plan=query_plan,
        user_context=user_context,
    )
    query_plan = query_plan_compiler.compile(query_plan=query_plan, retrieval=None)

    plan_result = query_plan_validator.validate_detailed(
        query_plan=query_plan,
        semantic_layer=semantic_layer,
    )
    plan_errors = plan_result.errors
    plan_warnings = plan_result.warnings
    plan_validation = ValidationResponse(
        valid=not plan_errors,
        errors=plan_errors,
        warnings=planner_warnings + permission_warnings + plan_warnings,
        risk_level=plan_result.risk_level,
        risk_flags=plan_result.risk_flags,
    )

    sql = None
    sql_validation = ValidationResponse(
        valid=True,
        errors=[],
        warnings=[OFFLINE_SQL_SKIP_REASON],
        risk_level="low",
        risk_flags=[],
    )
    answer = answer_builder.build(
        classification=classification,
        query_plan=query_plan,
        execution=None,
        plan_validation=plan_validation,
        sql_validation=sql_validation,
    )
    next_session_state = session_state_service.build_next_state(
        query_plan=query_plan,
        previous_state=session_state,
        sql=sql,
    )
    return {
        "semantic_parse": semantic_parse,
        "classification": classification,
        "query_plan": query_plan,
        "sql": sql,
        "plan_validation": plan_validation,
        "sql_validation": sql_validation,
        "sql_generation_mode": "skipped_llm_first_offline",
        "sql_skip_reason": OFFLINE_SQL_SKIP_REASON,
        "answer": answer,
        "next_session_state": next_session_state,
    }


def evaluate_case(case: EvaluationCase, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    classification = result["classification"]
    query_plan = result["query_plan"]
    answer = result["answer"]
    plan_validation = result["plan_validation"]
    sql_validation = result["sql_validation"]
    actual_metrics = list(query_plan.metrics)
    actual_dimensions = list(query_plan.dimensions)
    actual_sort_fields = unique([item.field for item in query_plan.sort])
    actual_filter_fields = unique([item.field for item in query_plan.filters])
    actual_semantic_views = list(query_plan.semantic_views)
    actual_warnings = unique(plan_validation.warnings + sql_validation.warnings)

    if case.expected_domain and classification.subject_domain != case.expected_domain:
        failures.append(f"expected_domain={case.expected_domain}, actual={classification.subject_domain}")
    if case.expected_question_type and classification.question_type != case.expected_question_type:
        failures.append(
            f"expected_question_type={case.expected_question_type}, actual={classification.question_type}"
        )
    if case.expected_status and answer.status != case.expected_status:
        failures.append(f"expected_status={case.expected_status}, actual={answer.status}")
    if case.expected_reason_code and classification.reason_code != case.expected_reason_code:
        failures.append(
            f"expected_reason_code={case.expected_reason_code}, actual={classification.reason_code}"
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
    if case.unexpected_dimensions:
        unexpected_dimensions = [item for item in case.unexpected_dimensions if item in actual_dimensions]
        if unexpected_dimensions:
            failures.append("unexpected_dimensions=" + ",".join(unexpected_dimensions))
    if case.expected_sort_fields:
        missing_sort_fields = [item for item in case.expected_sort_fields if item not in actual_sort_fields]
        if missing_sort_fields:
            failures.append("missing_sort_fields=" + ",".join(missing_sort_fields))
    if case.unexpected_sort_fields:
        unexpected_sort_fields = [item for item in case.unexpected_sort_fields if item in actual_sort_fields]
        if unexpected_sort_fields:
            failures.append("unexpected_sort_fields=" + ",".join(unexpected_sort_fields))
    if case.expected_filter_fields:
        missing_filter_fields = [item for item in case.expected_filter_fields if item not in actual_filter_fields]
        if missing_filter_fields:
            failures.append("missing_filter_fields=" + ",".join(missing_filter_fields))
    if case.expected_semantic_views:
        missing_semantic_views = [item for item in case.expected_semantic_views if item not in actual_semantic_views]
        if missing_semantic_views:
            failures.append("missing_semantic_views=" + ",".join(missing_semantic_views))
    if case.expected_warnings_contains:
        for expected_warning in case.expected_warnings_contains:
            if not any(expected_warning in warning for warning in actual_warnings):
                failures.append(f"missing_warning_substring={expected_warning}")

    terminal_non_sql_statuses = {"clarification_needed", "invalid"}
    should_require_sql = answer.status not in terminal_non_sql_statuses
    if not plan_validation.valid and should_require_sql:
        failures.append("plan_validation_failed")
    if not sql_validation.valid and should_require_sql:
        failures.append("sql_validation_failed")
    return failures


def build_session_state(case: EvaluationCase, components: dict[str, Any]) -> SessionState | None:
    state = None
    for seed_question in case.session_questions:
        result = run_question(
            question=seed_question,
            session_state=state,
            user_context=case.user_context,
            components=components,
        )
        state = result["next_session_state"]
    return state


def unique(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        if item not in values:
            values.append(item)
    return values


def summarize_dimension(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    buckets: dict[str, dict[str, int]] = {}
    for item in results:
        raw_value = item.get(key)
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        values = values or ["unspecified"]
        for value in values:
            name = str(value or "unspecified")
            bucket = buckets.setdefault(name, {"total": 0, "failed": 0})
            bucket["total"] += 1
            if not item["passed"]:
                bucket["failed"] += 1
    return dict(sorted(buckets.items()))


def summarize_failures(results: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in results:
        for failure in item.get("failures", []):
            failure_type = failure.split("=", 1)[0] if "=" in failure else failure
            counter[failure_type] += 1
    return dict(sorted(counter.items()))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    return {
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": failed,
        "sql_generation_mode": "skipped_llm_first_offline",
        "sql_skip_reason": OFFLINE_SQL_SKIP_REASON,
        "by_question_type": summarize_dimension(results, "classification_question_type"),
        "by_domain": summarize_dimension(results, "classification_domain"),
        "by_scenario": summarize_dimension(results, "scenario"),
        "by_coverage_tag": summarize_dimension(results, "coverage_tags"),
        "failure_types": summarize_failures(results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline Text2SQL regression without database access.")
    parser.add_argument(
        "--cases-path",
        default=str(EVAL_CASES_PATH),
        help="Path to evaluation cases JSON file.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Specific case id to run. Repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of cases to run after filtering.",
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Print only failed cases.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the full JSON regression report.",
    )
    parser.add_argument(
        "--report-dir",
        default="",
        help="Optional directory to write summary.json and failures.json reports.",
    )
    args = parser.parse_args()

    components = build_components()
    cases = load_cases(Path(args.cases_path))
    if args.case_id:
        selected = {item for item in args.case_id}
        cases = [item for item in cases if item.id in selected]
    if args.limit > 0:
        cases = cases[: args.limit]

    results: list[dict[str, Any]] = []
    for case in cases:
        session_state = build_session_state(case, components)
        result = run_question(
            question=case.question,
            session_state=session_state,
            user_context=case.user_context,
            components=components,
        )
        failures = evaluate_case(case, result)
        results.append(
            {
                "case_id": case.id,
                "scenario": case.scenario,
                "coverage_tags": case.coverage_tags,
                "classification_question_type": result["classification"].question_type,
                "classification_domain": result["classification"].subject_domain,
                "answer_status": result["answer"].status,
                "actual_reason_code": result["classification"].reason_code,
                "actual_metrics": list(result["query_plan"].metrics),
                "actual_dimensions": list(result["query_plan"].dimensions),
                "actual_filter_fields": unique([item.field for item in result["query_plan"].filters]),
                "actual_semantic_views": list(result["query_plan"].semantic_views),
                "plan_valid": result["plan_validation"].valid,
                "sql_valid": result["sql_validation"].valid,
                "sql_generation_mode": result["sql_generation_mode"],
                "sql_skip_reason": result["sql_skip_reason"],
                "passed": not failures,
                "failures": failures,
                "warnings": unique(result["plan_validation"].warnings + result["sql_validation"].warnings),
            }
        )

    summary = summarize(results)
    report = {"summary": summary, "items": results}

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        failures_only = [item for item in results if not item["passed"]]
        (report_dir / "failures.json").write_text(
            json.dumps({"items": failures_only}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(
        f"offline regression: {summary['passed_count']}/{summary['case_count']} passed, "
        f"{summary['failed_count']} failed"
    )
    print(f"sql generation: {summary['sql_generation_mode']} ({summary['sql_skip_reason']})")
    for question_type, bucket in sorted(summary["by_question_type"].items()):
        print(
            f"- type {question_type}: total={bucket['total']} failed={bucket['failed']}"
        )
    for scenario, bucket in sorted(summary["by_scenario"].items()):
        print(
            f"- scenario {scenario}: total={bucket['total']} failed={bucket['failed']}"
        )
    if summary["failure_types"]:
        print(
            "- failure_types:",
            ", ".join(f"{name}={count}" for name, count in summary["failure_types"].items()),
        )
    for item in results:
        if args.failures_only and item["passed"]:
            continue
        status = "PASS" if item["passed"] else "FAIL"
        print(
            f"{status} {item['case_id']} "
            f"type={item['classification_question_type']} "
            f"domain={item['classification_domain']} "
            f"status={item['answer_status']}"
        )
        if item["failures"]:
            print("  failures:", "; ".join(item["failures"]))
        if item["warnings"]:
            print("  warnings:", "; ".join(item["warnings"]))


if __name__ == "__main__":
    main()
