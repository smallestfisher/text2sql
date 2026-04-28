from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError
except Exception:  # pragma: no cover - optional dependency
    sqlglot = None
    exp = None
    ParseError = Exception


@dataclass
class JoinInspection:
    source: str
    has_condition: bool


@dataclass
class SqlInspection:
    statement_count: int
    sources: list[str] = field(default_factory=list)
    cte_names: list[str] = field(default_factory=list)
    joins: list[JoinInspection] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    referenced_fields: list[str] = field(default_factory=list)
    select_fields: list[str] = field(default_factory=list)
    group_by_fields: list[str] = field(default_factory=list)
    order_by_fields: list[str] = field(default_factory=list)
    has_select: bool = False
    has_where: bool = False
    where_clause: str = ""
    has_limit: bool = False
    limit_value: int | None = None
    has_subquery: bool = False
    has_distinct: bool = False
    has_having: bool = False
    has_wildcard_select: bool = False
    normalized_sql: str = ""
    parser_backend: str = "regex"
    parse_errors: list[str] = field(default_factory=list)


class SqlAstValidator:
    AGGREGATE_FUNCTIONS = {"SUM", "COUNT", "AVG", "MIN", "MAX"}
    NON_FUNCTION_TOKENS = {"IN", "AND", "OR"}
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

    def health(self) -> dict:
        return {
            "backend": "sqlglot" if sqlglot is not None else "regex",
            "sqlglot_enabled": sqlglot is not None,
        }

    def inspect(self, sql: str | None) -> SqlInspection:
        if sqlglot is not None:
            inspection = self._inspect_with_sqlglot(sql)
            if inspection is not None:
                return inspection
        return self._inspect_with_regex(sql)

    def validate(self, sql: str | None) -> tuple[list[str], list[str]]:
        if sql is None:
            return ["sql is empty"], []

        errors: list[str] = []
        warnings: list[str] = []
        inspection = self.inspect(sql)

        errors.extend(inspection.parse_errors)
        normalized = (sql or "").strip()

        if not inspection.has_select:
            errors.append("sql does not contain SELECT")
        if inspection.statement_count > 1:
            errors.append("multiple SQL statements are not allowed")
        if re.search(r"\bGROUP\s+BY\b", normalized, re.IGNORECASE) and not any(
            function in self.AGGREGATE_FUNCTIONS for function in inspection.functions
        ):
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

    def _inspect_with_regex(self, sql: str | None) -> SqlInspection:
        normalized = (sql or "").strip()
        statements = [item.strip() for item in re.split(r";\s*", normalized) if item.strip()]
        lowered = normalized.lower()
        where_clause = self._extract_where_clause(normalized)
        limit_match = re.search(r"\bLIMIT\s+(\d+)", normalized, re.IGNORECASE)
        sources = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", normalized, re.IGNORECASE)
        cte_names = self._extract_cte_names(normalized)
        aliases = self._extract_source_aliases(normalized)
        functions = sorted(set(re.findall(r"\b([A-Z_]+)\s*\(", normalized.upper())))

        return SqlInspection(
            statement_count=len(statements),
            sources=sources,
            cte_names=cte_names,
            joins=self._extract_joins(normalized),
            functions=functions,
            referenced_fields=self._extract_referenced_fields(normalized, sources, aliases, functions),
            select_fields=self._extract_select_fields(normalized),
            group_by_fields=self._extract_group_by_fields(normalized),
            order_by_fields=self._extract_order_by_fields(normalized),
            has_select=re.search(r"\bSELECT\b", normalized, re.IGNORECASE) is not None,
            has_where=bool(where_clause),
            where_clause=where_clause,
            has_limit=limit_match is not None,
            limit_value=int(limit_match.group(1)) if limit_match else None,
            has_subquery=re.search(r"\(\s*SELECT\b", normalized, re.IGNORECASE) is not None,
            has_distinct=re.search(r"\bSELECT\s+DISTINCT\b", normalized, re.IGNORECASE) is not None,
            has_having=re.search(r"\bHAVING\b", normalized, re.IGNORECASE) is not None,
            has_wildcard_select=re.search(r"\bSELECT\s+.*\*", normalized, re.IGNORECASE | re.DOTALL) is not None,
            normalized_sql=lowered,
            parser_backend="regex",
        )

    def _inspect_with_sqlglot(self, sql: str | None) -> SqlInspection | None:
        normalized = (sql or "").strip()
        if not normalized:
            return SqlInspection(statement_count=0, normalized_sql="", parser_backend="sqlglot")
        try:
            statements = sqlglot.parse(normalized, read="mysql")
        except ParseError as exc:
            return SqlInspection(
                statement_count=1,
                normalized_sql=normalized.lower(),
                parser_backend="sqlglot",
                parse_errors=[f"sql parse error: {exc}"],
            )
        if not statements:
            return SqlInspection(statement_count=0, normalized_sql="", parser_backend="sqlglot")

        root = statements[0]
        sources = self._unique_strings(
            [
                table.name
                for table in root.find_all(exp.Table)
                if getattr(table, "name", None)
            ]
        )
        cte_names = self._unique_strings(
            [
                cte.alias_or_name
                for cte in root.find_all(exp.CTE)
                if getattr(cte, "alias_or_name", None)
            ]
        )
        functions = self._unique_strings(
            [
                self._function_name(node)
                for node in root.find_all(exp.Func)
                if self._function_name(node)
            ]
        )
        referenced_fields = self._unique_strings(
            [
                column.name
                for column in root.find_all(exp.Column)
                if getattr(column, "name", None)
            ]
        )
        where_clause = ""
        where_node = root.find(exp.Where)
        if where_node is not None:
            where_clause = where_node.this.sql(dialect="mysql")
        limit_node = root.find(exp.Limit)

        return SqlInspection(
            statement_count=len(statements),
            sources=sources,
            cte_names=cte_names,
            joins=self._extract_sqlglot_joins(root),
            functions=functions,
            referenced_fields=referenced_fields,
            select_fields=self._extract_sqlglot_select_fields(root),
            group_by_fields=self._extract_sqlglot_group_by_fields(root),
            order_by_fields=self._extract_sqlglot_order_by_fields(root),
            has_select=root.find(exp.Select) is not None or isinstance(root, exp.Select),
            has_where=bool(where_clause),
            where_clause=where_clause,
            has_limit=limit_node is not None,
            limit_value=self._extract_limit_value(limit_node),
            has_subquery=any(True for _ in root.find_all(exp.Subquery)),
            has_distinct=bool(getattr(root, "args", {}).get("distinct")),
            has_having=root.args.get("having") is not None if hasattr(root, "args") else False,
            has_wildcard_select=any(True for _ in root.find_all(exp.Star)),
            normalized_sql=root.sql(dialect="mysql").lower(),
            parser_backend="sqlglot",
        )

    def _extract_select_fields(self, sql: str) -> list[str]:
        outer_sql = self._outer_query_sql(sql)
        match = re.search(
            r"\bSELECT\b(.*?)(?:\bFROM\b|$)",
            outer_sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return []
        return self._extract_fields_from_clause(match.group(1))

    def _extract_where_clause(self, sql: str) -> str:
        outer_sql = self._outer_query_sql(sql)
        match = re.search(
            r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
            outer_sql,
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

    def _extract_group_by_fields(self, sql: str) -> list[str]:
        outer_sql = self._outer_query_sql(sql)
        match = re.search(
            r"\bGROUP\s+BY\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)",
            outer_sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return []
        return self._extract_fields_from_clause(match.group(1))

    def _extract_order_by_fields(self, sql: str) -> list[str]:
        outer_sql = self._outer_query_sql(sql)
        match = re.search(
            r"\bORDER\s+BY\b(.*?)(?:\bLIMIT\b|$)",
            outer_sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return []
        return self._extract_fields_from_clause(match.group(1))

    def _extract_fields_from_clause(self, clause: str) -> list[str]:
        fields = re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\b", clause)
        ignored = set(self.SQL_KEYWORDS)
        result: list[str] = []
        for field in fields:
            if field.upper() in ignored:
                continue
            if field not in result:
                result.append(field)
        return result

    def _outer_query_sql(self, sql: str) -> str:
        stripped = sql.strip()
        if not re.match(r"^WITH\b", stripped, re.IGNORECASE):
            return stripped
        depth = 0
        for index, char in enumerate(stripped):
            if char == '(':
                depth += 1
            elif char == ')':
                depth = max(0, depth - 1)
            elif depth == 0 and stripped[index:index+6].upper() == 'SELECT':
                return stripped[index:]
        return stripped

    def _extract_referenced_fields(
        self,
        sql: str,
        sources: list[str],
        aliases: list[str],
        functions: list[str],
    ) -> list[str]:
        stripped = re.sub(r"'(?:''|[^'])*'", " ", sql)
        stripped = re.sub(r'"(?:[^"])*"', " ", stripped)
        field_candidates = re.findall(
            r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\b",
            stripped,
        )
        ignored = {item.upper() for item in sources}
        ignored.update(item.upper() for item in aliases)
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

    def _extract_source_aliases(self, sql: str) -> list[str]:
        aliases = re.findall(
            r"\b(?:FROM|JOIN)\s+[A-Za-z_][A-Za-z0-9_]*\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
            sql,
            re.IGNORECASE,
        )
        return self._unique_strings(aliases)

    def _extract_cte_names(self, sql: str) -> list[str]:
        cte_names: list[str] = []
        match = re.match(r"\s*WITH\s+(.*)\bSELECT\b", sql, re.IGNORECASE | re.DOTALL)
        if not match:
            return cte_names
        prefix = match.group(1)
        for cte_match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", prefix, re.IGNORECASE):
            name = cte_match.group(1)
            if name not in cte_names:
                cte_names.append(name)
        return cte_names

    def _extract_sqlglot_joins(self, root: Any) -> list[JoinInspection]:
        joins: list[JoinInspection] = []
        for join in root.find_all(exp.Join):
            source = ""
            if isinstance(join.this, exp.Table):
                source = join.this.name
            elif hasattr(join.this, "alias_or_name"):
                source = join.this.alias_or_name
            elif join.this is not None:
                source = join.this.sql(dialect="mysql")
            joins.append(
                JoinInspection(
                    source=source,
                    has_condition=join.args.get("on") is not None or join.args.get("using") is not None,
                )
            )
        return joins

    def _extract_sqlglot_group_by_fields(self, root: Any) -> list[str]:
        select = self._outer_select_node(root)
        group = select.args.get("group") if select is not None and hasattr(select, "args") else None
        if group is None:
            return []
        fields: list[str] = []
        for expression in getattr(group, "expressions", []) or []:
            fields.extend(self._expression_column_names(expression))
        return self._unique_strings(fields)

    def _extract_sqlglot_order_by_fields(self, root: Any) -> list[str]:
        select = self._outer_select_node(root)
        order = select.args.get("order") if select is not None and hasattr(select, "args") else None
        if order is None:
            return []
        fields: list[str] = []
        for expression in getattr(order, "expressions", []) or []:
            fields.extend(self._expression_column_names(expression))
        return self._unique_strings(fields)

    def _extract_sqlglot_select_fields(self, root: Any) -> list[str]:
        select = self._outer_select_node(root)
        if select is None:
            return []
        fields: list[str] = []
        for expression in getattr(select, "expressions", []) or []:
            fields.extend(self._expression_column_names(expression))
        return self._unique_strings(fields)

    def _outer_select_node(self, root: Any) -> Any:
        if exp is None or root is None:
            return None
        if isinstance(root, exp.Select):
            return root
        this_node = root.args.get("this") if hasattr(root, "args") else None
        if isinstance(this_node, exp.Select):
            return this_node
        return root.find(exp.Select)

    def _expression_column_names(self, expression: Any) -> list[str]:
        if exp is None or expression is None:
            return []
        return self._unique_strings(
            [
                item.name
                for item in expression.find_all(exp.Column)
                if getattr(item, "name", None)
            ]
        )

    def _extract_limit_value(self, limit_node: Any) -> int | None:
        if limit_node is None:
            return None
        expression = getattr(limit_node, "expression", None)
        if expression is not None:
            value = getattr(expression, "this", None)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        match = re.search(r"\bLIMIT\s+(\d+)", limit_node.sql(dialect="mysql"), re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _function_name(self, node: Any) -> str:
        if hasattr(node, "sql_name") and callable(node.sql_name):
            name = node.sql_name()
            if name:
                return str(name).upper()
        if hasattr(node, "key") and node.key:
            return str(node.key).upper()
        return ""

    def _unique_strings(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
