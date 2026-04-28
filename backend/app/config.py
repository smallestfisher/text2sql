from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
# Manifest entry for semantic/domain_config/* fragments.
DOMAIN_CONFIG_PATH = REPO_ROOT / "semantic" / "domain_config.json"
QUERY_PLAN_SCHEMA_PATH = REPO_ROOT / "schemas" / "query_plan.schema.json"
SESSION_STATE_SCHEMA_PATH = REPO_ROOT / "schemas" / "session_state.schema.json"
EXAMPLES_TEMPLATE_PATH = REPO_ROOT / "examples" / "nl2sql_examples.template.json"
TABLES_METADATA_PATH = REPO_ROOT / "tables.json"
BUSINESS_KNOWLEDGE_PATH = REPO_ROOT / "business_knowledge.json"
JOIN_PATTERNS_PATH = REPO_ROOT / "semantic" / "join_patterns.json"
RUNTIME_STORE_SCHEMA_PATH = REPO_ROOT / "sql" / "runtime_store.sql"
EVAL_CASES_PATH = REPO_ROOT / "eval" / "evaluation_cases.json"
