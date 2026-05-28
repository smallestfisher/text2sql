from __future__ import annotations

import re
from typing import Any

from backend.app.models.example_library import ExampleRecord, ExampleTemplateRecord
from backend.app.services.query_intent_parser import QueryIntentParser
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator


class ExampleFactory:
    def __init__(self, domain_config: dict, semantic_runtime: SemanticRuntime) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime
        self.intent_parser = QueryIntentParser(domain_config, semantic_runtime)
        self.sql_inspector = SqlAstValidator()

    def normalize(self, payload: dict | ExampleTemplateRecord) -> ExampleRecord:
        template = self.validate_template(payload)
        question = template.question.strip()
        sql = template.sql.strip()
        if not question:
            raise ValueError("example question is required")
        if not sql:
            raise ValueError("example sql is required")

        intent = self.intent_parser.parse(question)
        tables = self._tables_from_sql(sql)
        subject_domain = self._subject_domain(template.subject_domain, tables, intent.subject_domain)
        if not tables:
            tables = self.semantic_runtime.resolve_tables_for_plan(
                subject_domain,
                intent.matched_metrics,
            )
        example_id = template.id.strip() if template.id else self._make_id(question, subject_domain)
        dimensions = self._string_list(template.dimensions) or list(intent.requested_dimensions)

        return ExampleRecord(
            id=example_id,
            question=question,
            normalized_question=(intent.normalized_question or question).strip(),
            intent=self._default_intent(question, subject_domain),
            scenario="user_example",
            coverage_tags=self._unique(["real", subject_domain, *template.tags]),
            subject_domain=subject_domain,
            question_type="new",
            tables=tables,
            entities=list(intent.matched_entities),
            metrics=self._string_list(template.metrics) or list(intent.matched_metrics),
            dimensions=dimensions,
            filters=list(intent.filters),
            join_path=self.semantic_runtime.resolve_join_path(tables),
            sql=sql,
            result_shape=(template.result_shape or self._result_shape(dimensions)).strip() or None,
            notes=(template.notes or "").strip() or None,
        )

    def validate_template(self, payload: dict | ExampleTemplateRecord) -> ExampleTemplateRecord:
        return payload if isinstance(payload, ExampleTemplateRecord) else ExampleTemplateRecord(**payload)

    def dump_template(self, payload: dict | ExampleTemplateRecord) -> dict[str, Any]:
        template = self.validate_template(payload)
        return template.model_dump(exclude_none=True)

    def _tables_from_sql(self, sql: str) -> list[str]:
        inspection = self.sql_inspector.inspect(sql)
        cte_names = set(inspection.cte_names)
        tables = [source for source in inspection.sources if source not in cte_names]
        return [table for table in self._unique(tables) if self.semantic_runtime.is_known_table(table)]

    def _result_shape(self, dimensions: list[str]) -> str:
        if dimensions:
            return ",".join(dimensions)
        return "metric_only"

    def _default_intent(self, question: str, subject_domain: str) -> str:
        return f"{subject_domain} example for: {question}"

    def _make_id(self, question: str, subject_domain: str) -> str:
        ascii_tokens = re.findall(r"[A-Za-z0-9]+", question.lower())
        compact = "_".join(ascii_tokens[:8]) if ascii_tokens else "example"
        return f"{subject_domain}_{compact}"[:80].strip("_")

    def _subject_domain(self, value: object, tables: list[str], inferred: str) -> str:
        explicit = str(value or "").strip()
        if explicit:
            if not self.semantic_runtime.is_known_domain(explicit):
                raise ValueError(f"unsupported example subject_domain: {explicit}")
            return explicit
        if inferred != "unknown":
            return inferred
        domain_by_table: dict[str, str] = {}
        for domain in self.domain_config.get("domains", []):
            domain_name = str(domain.get("name") or "").strip()
            for table_name in domain.get("tables", []):
                domain_by_table.setdefault(str(table_name), domain_name)
        matches = [domain_by_table[table] for table in tables if table in domain_by_table]
        return matches[0] if matches else "unknown"

    def _string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _unique(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result
