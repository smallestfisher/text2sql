from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.models.session_state import SessionState

if TYPE_CHECKING:
    from backend.app.services.prompt_builder import PromptBuilder


class QuestionContextPromptBuilder:
    def __init__(self, prompt_builder: PromptBuilder) -> None:
        self._prompt_builder = prompt_builder

    def build(
        self,
        *,
        question: str,
        session_state: SessionState | None,
        parser_signals: dict[str, Any] | None = None,
    ) -> dict:
        builder = self._prompt_builder
        parser_signals = parser_signals or {}
        subject_domain = str(parser_signals.get("subject_domain") or (session_state.subject_domain if session_state else "unknown"))
        focus_tables = builder._question_context_focus_tables(subject_domain, parser_signals, session_state)
        conversation_summary = builder._conversation_summary(session_state) if session_state is not None else ""
        recent_turns = [
            builder._turn_text(item)
            for item in (session_state.recent_turns[-4:] if session_state is not None else [])
        ]
        business_knowledge_excerpt = builder._question_context_business_knowledge(
            subject_domain=subject_domain,
            question=question,
            conversation_summary=conversation_summary,
            recent_turns=recent_turns,
        )[:1200]
        table_fields = builder._context_table_fields(focus_tables)
        context_hints = builder._compact_mapping(
            {
                "parser_observations": builder._compact_mapping(parser_signals),
                "pending_clarification": builder._pending_clarification_payload(session_state),
                "business_knowledge_excerpt": business_knowledge_excerpt,
                "focus_tables": focus_tables,
                "table_fields": table_fields,
            }
        )
        prompt_diagnostics = {
            "conversation_summary_chars": len(conversation_summary),
            "recent_turn_count": len(recent_turns),
            "business_knowledge_excerpt_chars": len(business_knowledge_excerpt),
            "focus_table_count": len(focus_tables),
            "table_field_table_count": len(table_fields),
            "table_field_count": sum(len(fields) for fields in table_fields.values()),
            "has_pending_clarification": bool(context_hints.get("pending_clarification")),
        }
        return {
            "task": "question_context_generation",
            "question": question,
            "conversation_summary": conversation_summary,
            "last_turn": builder._last_turn_payload(session_state),
            "recent_turns": recent_turns,
            "context_hints": context_hints,
            "prompt_diagnostics": prompt_diagnostics,
            "instructions": {
                "return_format": "json",
                "fields": [
                    "decision",
                    "context_relation",
                    "subject_domain",
                    "effective_question",
                    "semantic_brief",
                    "clarification_question",
                    "reason",
                ],
                "decision_values": ["answerable", "clarification_needed", "invalid"],
                "context_relation_values": ["new", "follow_up", "ambiguous"],
                "constraints": [
                    "只做问题上下文整理，不生成 SQL。",
                    "如果当前问题是追问，effective_question 必须改写成不依赖上下文也能理解的完整自然语言问题。",
                    "首问只要本身是一个完整的自然语言业务查询句，就必须返回 decision=answerable、context_relation=new，并把原问题作为 effective_question；不要在 question_context 阶段追问字段、表、SQL 实现、可选维度、可选过滤条件、额外时间范围或业务口径细节。",
                    "当 context_relation=new 时，effective_question 必须忠实保留当前用户原话的查询对象、指标、时间、版本、数量和条件；不得用历史上下文替换、覆盖或改写当前问题的明确信息。",
                    "完整业务查询句的判断只看用户是否表达了要查什么；即使后续 SQL 生成可能还需要选择字段、表、指标公式、默认口径或是否追加过滤条件，也应先进入下一步，不能在本阶段 clarification_needed。",
                    "如果用户是在纠正或澄清自己上一句话，并且纠正后的句子已经能独立表达查询目标，也必须返回 answerable；不要继续追问可选条件。",
                    "如果用户问“哪一个”“最多的是谁”“Top/排名”等，返回对象就是查询输出，不要把这个输出对象误当成必须由用户补充的过滤条件。",
                    "只有用户这句话缺少核心意图、是无法解析的省略追问且上下文也无法补全，或明显不是业务查询时，才返回 clarification_needed 或 invalid。",
                    "如果 context_hints.pending_clarification 存在，当前用户问题应优先视为对上一轮澄清问题的回答；必须结合 pending_clarification、conversation_summary 和用户回答生成完整 effective_question。",
                    "当用户对 pending_clarification 给出确认、否认或补充信息时，不要把“是的”“不是”“对”等确认词当成独立业务问题。",
                    "只能继承 conversation_summary 和 recent_turns 中明确出现的信息；不确定指代时返回 clarification_needed。",
                    "如果用户表达替换、删除或新增条件，必须在 effective_question 中自然语言表达出来。",
                    "短追问优先基于最近一轮用户问题补全；除非用户明确要求回到更早主题，不要跳回更早轮次的查询意图。",
                    "不要因为“分布”“情况”“统计”就自行补充用户没有明确提出的维度。",
                    "如果用户提到“最新”但没有给出具体时间，应在 semantic_brief 中保留最新口径，不要编造具体日期。",
                    "只判断用户这句话和可用会话上下文是否足以形成完整自然语言问题；不要判断业务知识、字段、表、计算方法或 SQL 是否足够。",
                    "semantic_brief 用自然语言说明用户真正要查什么，供后续检索和 SQL 生成使用。",
                    "只输出指定 JSON 字段，不要输出结构化业务规划字段。",
                    "不要输出 markdown。",
                ],
            },
        }

    def conversation_summary(self, session_state: SessionState | None) -> str:
        if session_state is None:
            return ""
        return self._prompt_builder._conversation_summary(session_state)
