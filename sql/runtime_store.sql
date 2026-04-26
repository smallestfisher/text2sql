CREATE TABLE IF NOT EXISTS roles (
  role_name VARCHAR(64) PRIMARY KEY,
  description TEXT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  user_id VARCHAR(64) PRIMARY KEY,
  username VARCHAR(191) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  can_view_sql BOOLEAN NOT NULL,
  can_execute_sql BOOLEAN NOT NULL,
  can_download_results BOOLEAN NOT NULL,
  is_active BOOLEAN NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id VARCHAR(64) NOT NULL,
  role_name VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (user_id, role_name)
);

CREATE TABLE IF NOT EXISTS data_permissions (
  permission_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  scope_type VARCHAR(32) NOT NULL,
  scope_value VARCHAR(191) NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE (user_id, scope_type, scope_value)
);

CREATE TABLE IF NOT EXISTS field_visibility_policies (
  policy_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  field_name VARCHAR(191) NOT NULL,
  visibility_mode VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE (user_id, field_name)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NULL,
  title TEXT NULL,
  status VARCHAR(32) NOT NULL,
  current_state_json LONGTEXT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  role VARCHAR(32) NOT NULL,
  content LONGTEXT NOT NULL,
  trace_id VARCHAR(64) NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS session_state_snapshots (
  snapshot_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  trace_id VARCHAR(64) NULL,
  state_json LONGTEXT NOT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS query_logs (
  trace_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NULL,
  user_id VARCHAR(64) NULL,
  question LONGTEXT NULL,
  question_type VARCHAR(64) NULL,
  subject_domain VARCHAR(64) NULL,
  answer_status VARCHAR(64) NULL,
  plan_valid BOOLEAN NULL,
  plan_risk_level VARCHAR(16) NULL,
  plan_risk_flags_json LONGTEXT NULL,
  sql_valid BOOLEAN NULL,
  sql_risk_level VARCHAR(16) NULL,
  sql_risk_flags_json LONGTEXT NULL,
  executed BOOLEAN NULL,
  row_count INT NULL,
  warnings_json LONGTEXT NULL,
  trace_json LONGTEXT NOT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_logs (
  retrieval_log_id VARCHAR(64) PRIMARY KEY,
  trace_id VARCHAR(64) NOT NULL,
  rank_position INT NOT NULL,
  source_type VARCHAR(64) NOT NULL,
  source_id VARCHAR(191) NOT NULL,
  score DOUBLE NOT NULL,
  matched_features_json LONGTEXT NULL,
  metadata_json LONGTEXT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS sql_audit_logs (
  sql_audit_id VARCHAR(64) PRIMARY KEY,
  trace_id VARCHAR(64) NOT NULL,
  sql_text LONGTEXT NULL,
  plan_valid BOOLEAN NOT NULL,
  plan_risk_level VARCHAR(16) NULL,
  plan_risk_flags_json LONGTEXT NULL,
  sql_valid BOOLEAN NOT NULL,
  sql_risk_level VARCHAR(16) NULL,
  sql_risk_flags_json LONGTEXT NULL,
  executed BOOLEAN NOT NULL,
  row_count INT NULL,
  warnings_json LONGTEXT NULL,
  errors_json LONGTEXT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_logs (
  feedback_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NULL,
  trace_id VARCHAR(64) NULL,
  user_id VARCHAR(64) NULL,
  feedback_type VARCHAR(32) NOT NULL,
  comment LONGTEXT NULL,
  created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  case_count INT NOT NULL,
  passed_count INT NOT NULL,
  failed_count INT NOT NULL,
  run_json LONGTEXT NOT NULL,
  created_at DATETIME NOT NULL
);
