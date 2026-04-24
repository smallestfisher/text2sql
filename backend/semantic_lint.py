from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_LAYER_PATH = REPO_ROOT / "semantic" / "semantic_layer.json"


class SemanticLintError(ValueError):
    pass


def load_semantic_layer(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unique_names(items: list[dict], key: str) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    ordered: list[str] = []
    duplicates: list[str] = []
    for item in items:
        name = str(item.get(key, "")).strip()
        if not name:
            continue
        if name in seen and name not in duplicates:
            duplicates.append(name)
            continue
        seen.add(name)
        ordered.append(name)
    return ordered, duplicates


def collect_allowed_fields(data: dict) -> set[str]:
    fields: set[str] = set()
    for metric in data.get("metrics", []):
        metric_name = metric.get("name")
        semantic_column = metric.get("semantic_column")
        if metric_name:
            fields.add(str(metric_name))
        if semantic_column:
            fields.add(str(semantic_column))
    for entity in data.get("entities", []):
        entity_name = entity.get("name")
        if entity_name:
            fields.add(str(entity_name))
        for alias in entity.get("aliases", []):
            if alias:
                fields.add(str(alias))
    for semantic_view in data.get("semantic_views", []):
        for field in semantic_view.get("output_fields", []):
            if field:
                fields.add(str(field))
        for alias_field in semantic_view.get("field_aliases", {}).keys():
            if alias_field:
                fields.add(str(alias_field))
    for domain in data.get("domains", []):
        for table in domain.get("tables", []):
            if table:
                fields.add(str(table))
    return fields


def lint_semantic_layer(data: dict) -> list[str]:
    issues: list[str] = []

    domain_names, duplicate_domains = unique_names(data.get("domains", []), "name")
    if duplicate_domains:
        issues.append("duplicate domain names: " + ", ".join(duplicate_domains))

    entity_names, duplicate_entities = unique_names(data.get("entities", []), "name")
    if duplicate_entities:
        issues.append("duplicate entity names: " + ", ".join(duplicate_entities))

    metric_names, duplicate_metrics = unique_names(data.get("metrics", []), "name")
    if duplicate_metrics:
        issues.append("duplicate metric names: " + ", ".join(duplicate_metrics))

    view_names, duplicate_views = unique_names(data.get("semantic_views", []), "name")
    if duplicate_views:
        issues.append("duplicate semantic view names: " + ", ".join(duplicate_views))

    known_domains = set(domain_names)
    known_metrics = set(metric_names)
    known_entities = set(entity_names)
    known_views = set(view_names)
    known_graph_nodes = set(data.get("semantic_graph", {}).get("nodes", []))
    allowed_fields = collect_allowed_fields(data)

    query_profiles = data.get("query_profiles", {})
    profile_names = set(query_profiles.keys())
    missing_profiles = sorted(known_domains - profile_names - {"dimension"})
    if missing_profiles:
        issues.append("missing query_profiles for domains: " + ", ".join(missing_profiles))

    unknown_profiles = sorted(profile_names - known_domains)
    if unknown_profiles:
        issues.append("query_profiles reference unknown domains: " + ", ".join(unknown_profiles))

    metric_to_domain = data.get("domain_inference", {}).get("metric_to_domain", {})
    unknown_metric_mappings = sorted(metric for metric in metric_to_domain if metric not in known_metrics)
    if unknown_metric_mappings:
        issues.append("domain_inference.metric_to_domain references unknown metrics: " + ", ".join(unknown_metric_mappings))
    unknown_metric_domains = sorted({domain for domain in metric_to_domain.values() if domain not in known_domains})
    if unknown_metric_domains:
        issues.append("domain_inference.metric_to_domain points to unknown domains: " + ", ".join(unknown_metric_domains))

    for semantic_view in data.get("semantic_views", []):
        view_name = semantic_view.get("name", "<unknown>")
        serves_domains = semantic_view.get("serves_domains", [])
        unknown_serves_domains = sorted(domain for domain in serves_domains if domain not in known_domains)
        if unknown_serves_domains:
            issues.append(f"semantic_view {view_name} serves unknown domains: {', '.join(unknown_serves_domains)}")
        source_tables = semantic_view.get("source_tables", [])
        unknown_sources = sorted(
            table for table in source_tables if table not in known_graph_nodes and table not in known_views
        )
        if unknown_sources:
            issues.append(f"semantic_view {view_name} references unknown source tables/views: {', '.join(unknown_sources)}")
        output_fields = semantic_view.get("output_fields", [])
        if not output_fields:
            issues.append(f"semantic_view {view_name} has empty output_fields")
        field_aliases = semantic_view.get("field_aliases", {})
        unknown_alias_fields = sorted(field for field in field_aliases if field not in output_fields)
        if unknown_alias_fields:
            issues.append(f"semantic_view {view_name} field_aliases reference fields outside output_fields: {', '.join(unknown_alias_fields)}")

    for domain_name, profile in query_profiles.items():
        default_views = profile.get("default_semantic_views", [])
        unknown_default_views = sorted(view for view in default_views if view not in known_views)
        if unknown_default_views:
            issues.append(f"query_profile {domain_name} references unknown default_semantic_views: {', '.join(unknown_default_views)}")

        permission_fields = profile.get("permission_scope_fields", {}).values()
        unknown_permission_fields = sorted({field for field in permission_fields if field not in allowed_fields})
        if unknown_permission_fields:
            issues.append(f"query_profile {domain_name} permission_scope_fields reference unknown fields: {', '.join(unknown_permission_fields)}")

        default_sort_fields = [item.get("field") for item in profile.get("default_sort", []) if isinstance(item, dict)]
        unknown_sort_fields = sorted({field for field in default_sort_fields if field and field not in allowed_fields})
        if unknown_sort_fields:
            issues.append(f"query_profile {domain_name} default_sort references unknown fields: {', '.join(unknown_sort_fields)}")

        time_filter_fields = profile.get("time_filter_fields", [])
        unknown_time_fields = sorted(field for field in time_filter_fields if field not in allowed_fields)
        if unknown_time_fields:
            issues.append(f"query_profile {domain_name} time_filter_fields reference unknown fields: {', '.join(unknown_time_fields)}")

        version_field = profile.get("version_field")
        if version_field and version_field not in allowed_fields:
            issues.append(f"query_profile {domain_name} version_field references unknown field: {version_field}")

    extractors = data.get("extractors", {})
    for extractor_name in ["filters", "dimensions", "time", "version", "sort"]:
        for rule in extractors.get(extractor_name, []):
            field = rule.get("field")
            if field and field != "__matched_metric__" and field not in allowed_fields:
                issues.append(f"extractor {extractor_name} references unknown field: {field}")

    return issues


def main() -> int:
    data = load_semantic_layer(SEMANTIC_LAYER_PATH)
    issues = lint_semantic_layer(data)
    if not issues:
        print("semantic lint: ok")
        return 0
    print(f"semantic lint: {len(issues)} issue(s)")
    for issue in issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
