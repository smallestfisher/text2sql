from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class JoinInspection:
    source: str
    has_condition: bool


@dataclass
class SqlInspection:
    statement_count: int
    sources: list[str] = field(default_factory=list)
    joins: list[JoinInspection] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    referenced_fields: list[str] = field(default_factory=list)
    has_select: bool = False
    has_where: bool = False
    where_clause: str = ""
    has_limit: bool = False
    limit_value: int | None = None
    has_subquery: bool = False
    normalized_sql: str = ""


class SqlAstValidator:
    AGGREGATE_FUNCTIONS = {"SUM", "COUNT", "AVG", "MIN", "MAX"}
    NON_FUNCTION_TOKENS = {"IN"}
    SQL_KEYWORDS = {
        "SELECT",
        "FROM",
        "WHERE",
        "GROUP",
        "BY",
        "ORDER",
        "LIMIT",
        "JOIN",
        "LEFT",
        "RIGHT",
        "INNER",
        "OUTER",
        "ON",
        "USING",
        "AND",
        "OR",
        "AS",
        "ASC",
        "DESC",
        "BETWEEN",
        "LIKE",
        "IS",
        "NULL",
        "NOT",
        "DISTINCT",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "UNION",
        "ALL",
        "HAVING",
    }

    def inspect(self, sql: str | None) -> SqlInspection:
        normalized = (sql or "").strip()
        statements = [item.strip() for item in re.split(r";\s*", normalized) if item.strip()]
        lowered = normalized.lower()
        where_clause = self._extract_where_clause(normalized)
        limit_match = re.search(r"\bLIMIT\s+(\d+)", normalized, re.IGNORECASE)
        sources = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", normalized, re.IGNORECASE)
        functions = sorted(set(re.findall(r"\b([A-Z_]+)\s*\(", normalized.upper())))

        return SqlInspection(
            statement_count=len(statements),
            sources=sources,
            joins=self._extract_joins(normalized),
            functions=functions,
            referenced_fields=self._extract_referenced_fields(normalized, sources, functions),
            has_select=re.search(r"\bSELECT\b", normalized, re.IGNORECASE) is not None,
            has_where=bool(where_clause),
            where_clause=where_clause,
            has_limit=limit_match is not None,
            limit_value=int(limit_match.group(1)) if limit_match else None,
            has_subquery=re.search(r"\(\s*SELECT\b", normalized, re.IGNORECASE) is not None,
            normalized_sql=lowered,
        )

    def validate(self, sql: str | None) -> tuple[list[str], list[str]]:
        if sql is None:
            return ["sql is empty"], []

        errors: list[str] = []
        warnings: list[str] = []
        inspection = self.inspect(sql)

        if not inspection.has_select:
            errors.append("sql does not contain SELECT")
        if inspection.statement_count > 1:
            errors.append("multiple SQL statements are not allowed")
        if re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE) and re.search(
            r"\bSUM\b|\bCOUNT\b|\bAVG\b|\bMIN\b|\bMAX\b", sql, re.IGNORECASE
        ) is None:
            warnings.append("GROUP BY exists without recognized aggregate function")
        for join in inspection.joins:
            if not join.has_condition:
                warnings.append(f"JOIN on {join.source} does not include ON/USING condition")
        if inspection.has_subquery:
            warnings.append("sql contains subquery; review semantic explainability carefully")

        unsupported = sorted(
            function
            for function in inspection.functions
            if function not in self.AGGREGATE_FUNCTIONS
            and function not in self.NON_FUNCTION_TOKENS
        )
        if unsupported:
            warnings.append(f"sql contains unclassified functions: {', '.join(unsupported)}")

        return errors, warnings

    def _extract_where_clause(self, sql: str) -> str:
        match = re.search(
            r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        return match.group(1)

    def _extract_joins(self, sql: str) -> list[JoinInspection]:
        joins: list[JoinInspection] = []
        matches = list(re.finditer(r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)\b", sql, re.IGNORECASE))
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(sql)
            segment = sql[start:end]
            joins.append(
                JoinInspection(
                    source=match.group(1),
                    has_condition=(
                        re.search(r"\bON\b", segment, re.IGNORECASE) is not None
                        or re.search(r"\bUSING\b", segment, re.IGNORECASE) is not None
                    ),
                )
            )
        return joins

    def _extract_referenced_fields(
        self,
        sql: str,
        sources: list[str],
        functions: list[str],
    ) -> list[str]:
        stripped = re.sub(r"'(?:''|[^'])*'", " ", sql)
        stripped = re.sub(r'"(?:[^"])*"', " ", stripped)
        field_candidates = re.findall(
            r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\b",
            stripped,
        )
        ignored = {item.upper() for item in sources}
        ignored.update(functions)
        ignored.update(self.SQL_KEYWORDS)

        referenced: list[str] = []
        seen: set[str] = set()
        for candidate in field_candidates:
            upper = candidate.upper()
            if upper in ignored:
                continue
            if re.fullmatch(r"\d+", candidate):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            referenced.append(candidate)
        return referenced
