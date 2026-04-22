from __future__ import annotations

from backend.app.models.answer import AnswerPayload
from backend.app.models.api import ExecutionResponse, ValidationResponse
from backend.app.models.classification import QuestionClassification
from backend.app.models.query_plan import QueryPlan


class AnswerBuilder:
    def build(
        self,
        classification: QuestionClassification,
        query_plan: QueryPlan,
        execution: ExecutionResponse | None,
        plan_validation: ValidationResponse,
        sql_validation: ValidationResponse,
    ) -> AnswerPayload:
        if classification.question_type == "invalid":
            return AnswerPayload(
                status="invalid",
                summary="当前输入不是有效的数据查询问题。",
                follow_up_hint="请直接描述要查询的指标、对象和时间范围。",
            )

        if classification.need_clarification:
            return AnswerPayload(
                status="clarification_needed",
                summary=classification.clarification_question or "需要补充更多查询条件。",
                detail=classification.reason,
            )

        if not plan_validation.valid or not sql_validation.valid:
            return AnswerPayload(
                status="error",
                summary="查询链路已生成，但校验未通过。",
                detail="; ".join(plan_validation.errors + sql_validation.errors),
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

        metric_text = ", ".join(query_plan.metrics) if query_plan.metrics else "未识别指标"
        return AnswerPayload(
            status="stub",
            summary=f"已完成查询规划，当前输出围绕指标: {metric_text}。",
            detail="当前结果来自骨架链路，尚未获得数据库执行结果。",
        )
