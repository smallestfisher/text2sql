from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.domain_config_loader import DomainConfigLoader

TABLES_METADATA_PATH = REPO_ROOT / "semantic" / "tables.json"


class DomainConfigLintError(ValueError):
    pass


def load_json(path: Path) -> dict:
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


def collect_table_fields(tables_metadata: dict) -> set[str]:
    fields: set[str] = set()
    for payload in tables_metadata.values():
        if not isinstance(payload, dict):
            continue
        for raw_column in payload.get("columns", []):
            if not raw_column:
                continue
            column_name = str(raw_column).split("(", 1)[0].strip()
            if column_name:
                fields.add(column_name)
    return fields


def collect_allowed_fields(data: dict, table_fields: set[str]) -> set[str]:
    fields = set(table_fields)
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
    for profile in data.get("query_profiles", {}).values():
        for field in profile.get("allowed_fields", []):
            if field:
                fields.add(str(field))
        for alias_field, targets in profile.get("field_aliases", {}).items():
            if alias_field:
                fields.add(str(alias_field))
            if isinstance(targets, list):
                fields.update(str(item) for item in targets if item)
            elif targets:
                fields.add(str(targets))
    return fields


def lint_domain_config(data: dict, tables_metadata: dict) -> list[str]:
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

    known_domains = set(domain_names)
    known_metrics = set(metric_names)
    known_graph_nodes = set(data.get("semantic_graph", {}).get("nodes", []))
    table_fields = collect_table_fields(tables_metadata)
    allowed_fields = collect_allowed_fields(data, table_fields)

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

    for domain_name, profile in query_profiles.items():
        allowed_profile_fields = [str(field) for field in profile.get("allowed_fields", []) if field]
        if not allowed_profile_fields:
            issues.append(f"query_profile {domain_name} has empty allowed_fields")

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

        raw_aliases = profile.get("field_aliases", {})
        if raw_aliases and not isinstance(raw_aliases, dict):
            issues.append(f"query_profile {domain_name} field_aliases must be an object")
            continue
        for alias_field, targets in raw_aliases.items():
            if alias_field not in allowed_profile_fields:
                issues.append(f"query_profile {domain_name} field_aliases key is outside allowed_fields: {alias_field}")
            values = targets if isinstance(targets, list) else [targets]
            invalid_targets = sorted(
                str(item)
                for item in values
                if item and str(item) not in allowed_fields
            )
            if invalid_targets:
                issues.append(
                    f"query_profile {domain_name} field_aliases reference unknown targets for {alias_field}: "
                    + ", ".join(invalid_targets)
                )

    for domain in data.get("domains", []):
        unknown_tables = [table for table in domain.get("tables", []) if table not in known_graph_nodes]
        if unknown_tables:
            issues.append(
                f"domain {domain.get('name', '<unknown>')} references unknown tables: {', '.join(sorted(unknown_tables))}"
            )

    extractors = data.get("extractors", {})
    for extractor_name in ["filters", "dimensions", "time", "version", "sort"]:
        for rule in extractors.get(extractor_name, []):
            field = rule.get("field")
            if field and field != "__matched_metric__" and field not in allowed_fields:
                issues.append(f"extractor {extractor_name} references unknown field: {field}")

    return issues


def main() -> int:
    domain_config = DomainConfigLoader().load()
    tables_metadata = load_json(TABLES_METADATA_PATH)
    issues = lint_domain_config(domain_config, tables_metadata)
    if not issues:
        print("domain config lint: ok")
        return 0
    print(f"domain config lint: {len(issues)} issue(s)")
    for issue in issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
