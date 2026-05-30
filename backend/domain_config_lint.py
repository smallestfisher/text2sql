from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.domain_config_loader import DomainConfigLoader

TABLES_METADATA_PATH = REPO_ROOT / "semantic" / "tables.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def lint_schema_boundary(data: dict, tables_metadata: dict) -> list[str]:
    issues: list[str] = []
    if data.get("domains"):
        issues.append("domains should stay empty; use knowledge, examples and join_patterns for business meaning")
    if data.get("metrics"):
        issues.append("metrics should stay empty; metric meaning belongs in knowledge and examples")
    if data.get("query_profiles"):
        issues.append("query_profiles should stay empty; profile rules are deprecated")
    if data.get("extractors"):
        issues.append("extractors should stay empty; parser rules are deprecated")

    graph = data.get("semantic_graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if sorted(nodes) != sorted(tables_metadata.keys()):
        issues.append("semantic_graph.nodes must match semantic/tables.json table names")

    known_tables = set(tables_metadata.keys())
    for edge in edges:
        if not isinstance(edge, dict):
            issues.append("semantic_graph.edges must contain objects")
            continue
        source = edge.get("from")
        target = edge.get("to")
        if source not in known_tables or target not in known_tables:
            issues.append(f"semantic_graph edge references unknown table: {source} -> {target}")
        if not edge.get("on"):
            issues.append(f"semantic_graph edge is missing join condition: {source} -> {target}")
    return issues


def main() -> int:
    domain_config = DomainConfigLoader().load()
    tables_metadata = load_json(TABLES_METADATA_PATH)
    issues = lint_schema_boundary(domain_config, tables_metadata)
    if not issues:
        print("schema boundary lint: ok")
        return 0
    print(f"schema boundary lint: {len(issues)} issue(s)")
    for issue in issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
