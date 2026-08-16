from __future__ import annotations

from backend.app.models.answer import AnswerPayload
from backend.app.models.api import ExecutionResponse, ValidationResponse
from backend.app.models.classification import QuestionClassification


class AnswerBuilder:
    def build(
        self,
        classification: QuestionClassification,
        execution: ExecutionResponse | None,
        context_validation: ValidationResponse,
        sql_validation: ValidationResponse,
        metrics: list[str] | None = None,
    ) -> AnswerPayload:
        if classification.question_type == "invalid":
            summary = "当前输入不属于当前系统支持的业务数据查询范围。"
            return AnswerPayload(
                status="invalid",
                summary=summary,
                detail=classification.reason,
                follow_up_hint="请改成当前发布版本支持的业务数据查询，并尽量带上指标、对象和时间范围。",
            )

        if classification.need_clarification:
            return AnswerPayload(
                status="clarification_needed",
                summary=classification.clarification_question or "需要补充更多查询条件。",
                detail=classification.reason,
            )

        if not context_validation.valid or not sql_validation.valid:
            return AnswerPayload(
                status="error",
                summary="查询链路已生成，但校验未通过。",
                detail="; ".join(context_validation.errors + sql_validation.errors),
            )

        if execution is not None and execution.executed:
            if execution.status == "empty_result":
                return AnswerPayload(
                    status="ok",
                    summary="查询已执行，但没有返回结果。",
                    detail="可以调整时间范围、过滤条件或统计口径后再试。",
                )
            if execution.status == "truncated":
                return AnswerPayload(
                    status="ok",
                    summary=f"查询已执行，当前返回前 {execution.row_count} 行结果。",
                    detail="结果集过大，系统已自动截断返回。",
                )
            return AnswerPayload(
                status="ok",
                summary=f"查询已执行，返回 {execution.row_count} 行结果。",
            )

        if execution is not None and execution.errors:
            if execution.status == "timeout":
                return AnswerPayload(
                    status="error",
                    summary="查询执行超时。",
                    detail="请缩小时间范围、过滤条件或降低结果粒度后重试。",
                )
            return AnswerPayload(
                status="error",
                summary="查询已生成，但数据库执行失败。",
                detail="; ".join(execution.errors),
            )

        metric_text = ", ".join(metrics or []) if metrics else "未识别指标"
        return AnswerPayload(
            status="error",
            summary="查询链路未返回执行结果。",
            detail=f"当前已完成查询规划，但未形成可交付结果。指标: {metric_text}。",
        )
