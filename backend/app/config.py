from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_LAYER_PATH = REPO_ROOT / "semantic" / "semantic_layer.json"
QUERY_PLAN_SCHEMA_PATH = REPO_ROOT / "schemas" / "query_plan.schema.json"
SESSION_STATE_SCHEMA_PATH = REPO_ROOT / "schemas" / "session_state.schema.json"
EXAMPLES_TEMPLATE_PATH = REPO_ROOT / "examples" / "nl2sql_examples.template.json"
TABLES_METADATA_PATH = REPO_ROOT / "tables.json"
README_TEXT_PATH = REPO_ROOT / "readme.txt"
BUSINESS_KNOWLEDGE_PATH = REPO_ROOT / "business_knowledge.json"
RUNTIME_STORE_SCHEMA_PATH = REPO_ROOT / "sql" / "runtime_store.sql"
RUNTIME_DATA_DIR = REPO_ROOT / "runtime_data"
SESSIONS_DATA_PATH = RUNTIME_DATA_DIR / "sessions.json"
AUDIT_DATA_PATH = RUNTIME_DATA_DIR / "audit_traces.json"
FEEDBACK_DATA_PATH = RUNTIME_DATA_DIR / "feedback_records.json"
AUTH_USERS_DATA_PATH = RUNTIME_DATA_DIR / "auth_users.json"
EVAL_CASES_PATH = REPO_ROOT / "eval" / "evaluation_cases.json"
EVAL_RUNS_PATH = RUNTIME_DATA_DIR / "evaluation_runs.json"
