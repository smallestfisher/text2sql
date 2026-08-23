import { useEffect, useMemo, useState } from "react";

import { api, isAuthFailure } from "./api";
import type { ConfigFieldRecord } from "./types";

const GROUP_LABELS: Record<string, string> = {
  app: "应用与登录",
  business_db: "Oracle 业务数据库",
  llm: "SQL 生成模型",
  vector: "语义向量检索",
  sql: "查询执行与安全限制",
};

const GROUP_DESCRIPTIONS: Record<string, string> = {
  app: "应用标识、运行数据位置、登录有效期和接口文档开关。",
  business_db: "当前连接的 Oracle 地址，以及允许同步的 Schema 范围。",
  llm: "负责问题理解、SQL 生成和修复的模型配置。",
  vector: "Embedding 服务、召回数量和发布时索引预热配置。",
  sql: "SQL 超时、返回行数、风险阈值和执行结果缓存配置。",
};

const GROUP_ORDER = ["business_db", "llm", "vector", "sql", "app"];

const SOURCE_LABELS: Record<string, string> = {
  env: "环境变量",
  default: "默认值",
};

const FIELD_LABELS: Record<string, string> = {
  APP_NAME: "应用名称",
  APP_VERSION: "应用版本",
  APP_ENV: "运行环境",
  LOG_LEVEL: "日志级别",
  ENABLE_DOCS: "API 文档",
  RUNTIME_DATABASE_URL: "运行数据存储",
  VECTOR_CACHE_DIR: "向量缓存目录",
  AUTH_TOKEN_SECRET: "登录令牌签名密钥",
  AUTH_TOKEN_TTL_SECONDS: "登录有效期（秒）",
  BUSINESS_DATABASE_URL: "Oracle 连接地址",
  BUSINESS_DATABASE_SCHEMAS: "允许同步的 Schema",
  OPENAI_API_KEY: "主模型 API 密钥",
  OPENAI_API_BASE: "主模型接口地址",
  LLM_MODEL: "SQL 生成模型",
  LLM_ENABLE_THINKING: "启用模型思考模式",
  LLM_TIMEOUT_SECONDS: "模型请求超时（秒）",
  LLM_MAX_RETRIES: "模型失败重试次数",
  SQL_REPAIR_MAX_RETRIES: "SQL 修复重试次数",
  LLM_CACHE_TTL_SECONDS: "Prompt 缓存时长（秒）",
  LLM_CACHE_MAX_ENTRIES: "Prompt 缓存条数",
  LLM_CACHE_PROMPT: "服务端 Prompt 缓存",
  ENABLE_VECTOR_RETRIEVAL: "启用向量检索",
  PREWARM_VECTOR_RETRIEVAL: "发布时预热向量索引",
  VECTOR_RETRIEVAL_PROVIDER: "向量服务提供方",
  VECTOR_API_KEY: "向量服务 API 密钥",
  VECTOR_API_BASE: "向量服务接口地址",
  VECTOR_MODEL: "Embedding 模型",
  VECTOR_DIMENSIONS: "向量维度",
  VECTOR_TOP_K: "向量召回数量",
  VECTOR_TIMEOUT_SECONDS: "向量请求超时（秒）",
  SQL_TIMEOUT_SECONDS: "SQL 执行超时（秒）",
  DEFAULT_SQL_LIMIT: "默认查询行数",
  HIGH_RISK_SQL_LIMIT: "高风险查询行数上限",
  EXECUTION_CACHE_TTL_SECONDS: "执行结果缓存时长（秒）",
  EXECUTION_CACHE_MAX_ENTRIES: "执行结果缓存条数",
  EXECUTION_MAX_ROWS: "单次最大返回行数",
  SLOW_QUERY_THRESHOLD_MS: "慢查询阈值（毫秒）",
};

const FIELD_HELP: Record<string, string> = {
  BUSINESS_DATABASE_URL: "连接信息属于敏感配置，页面只显示脱敏后的当前状态。",
  BUSINESS_DATABASE_SCHEMAS: "留空表示同步当前连接用户的默认 Schema；多个 Schema 用逗号分隔。",
  OPENAI_API_KEY: "用于问题理解和 SQL 生成，密钥内容不会返回到浏览器。",
  LLM_ENABLE_THINKING: "关闭后可显著降低 Qwen3 的响应时间，但模型不会输出额外思考过程。",
  VECTOR_API_KEY: "用于生成 Embedding；未配置时系统会降级到关键词检索。",
  ENABLE_VECTOR_RETRIEVAL: "关闭后仍可使用关键词 BM25 检索。",
  PREWARM_VECTOR_RETRIEVAL: "开启后，发布语义版本时会提前构建该版本的向量索引。",
  DEFAULT_SQL_LIMIT: "生成 SQL 时默认追加的结果行数限制。",
  EXECUTION_MAX_ROWS: "数据库执行后允许返回给前端的最大结果行数。",
  AUTH_TOKEN_SECRET: "生产环境必须使用独立的高强度密钥。",
};

export function SystemSettings(props: { token: string; onAuthFailure: () => void }) {
  const [fields, setFields] = useState<ConfigFieldRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await api.adminGetConfig(props.token);
      setFields(response.fields);
    } catch (err) {
      if (isAuthFailure(err)) {
        props.onAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.token]);

  const grouped = useMemo(() => {
    const map = new Map<string, ConfigFieldRecord[]>();
    for (const field of fields) {
      const list = map.get(field.group) ?? [];
      list.push(field);
      map.set(field.group, list);
    }
    return GROUP_ORDER.filter((group) => map.has(group)).map((group) => ({
      group,
      label: GROUP_LABELS[group] ?? group,
      description: GROUP_DESCRIPTIONS[group] ?? "",
      items: map.get(group) as ConfigFieldRecord[],
    }));
  }, [fields]);

  return (
    <div className="studio-root config-root">
      <div className="studio-head config-page-head">
        <div>
          <h2>部署配置（当前生效值）</h2>
          <p>查看后端当前实际使用的数据库、模型、检索和 SQL 安全配置。</p>
        </div>
        <div className="studio-status-actions">
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>
            {loading ? "读取中…" : "刷新生效值"}
          </button>
        </div>
      </div>

      <div className="config-readonly-notice">
        <strong>此页面只读</strong>
        <span>
          请修改服务器的 <code>.env</code>、Docker Compose 环境变量或部署 Secret，然后重启后端。
          业务表说明、知识规则、关联关系和 SQL 样例仍在上方“业务语义配置”中直接编辑并发布。
        </span>
      </div>

      {error ? <div className="studio-status-error">{error}</div> : null}
      {loading ? <div className="studio-loading">加载中…</div> : null}

      {!loading
        ? grouped.map((section) => (
            <section key={section.group} className="studio-subsection config-section">
              <div className="studio-subsection-head config-section-head">
                <div>
                  <strong>{section.label}</strong>
                  <div className="studio-subsection-hint">{section.description}</div>
                </div>
                <span className="config-section-count">{section.items.length} 项</span>
              </div>
              <div className="studio-grid-2">
                {section.items.map((field) => (
                  <ConfigField key={field.name} field={field} />
                ))}
              </div>
            </section>
          ))
        : null}
    </div>
  );
}

function ConfigField(props: { field: ConfigFieldRecord }) {
  const { field } = props;
  const sourceLabel = SOURCE_LABELS[field.source] ?? field.source;
  const value = displayConfigValue(field);

  return (
    <div className="config-field">
      <div className="config-field-head">
        <span className="config-field-title">{FIELD_LABELS[field.name] ?? field.name}</span>
        <span className="config-field-badges">
          <span className="studio-badge">{sourceLabel}</span>
          {field.secret ? <span className="studio-badge">已脱敏</span> : null}
        </span>
      </div>
      <code className="config-env-name">{field.name}</code>
      <div className={`config-value${value === "未配置" ? " is-empty" : ""}`}>{value}</div>
      {FIELD_HELP[field.name] ? <div className="config-field-help">{FIELD_HELP[field.name]}</div> : null}
    </div>
  );
}

function displayConfigValue(field: ConfigFieldRecord) {
  const value = field.value?.trim();
  if (!value || value.toLowerCase() === "none") {
    return "未配置";
  }
  if (field.secret) {
    return "已配置（内容已隐藏）";
  }
  if (field.type === "bool" || field.type === "optional_bool") {
    return ["1", "true", "yes", "on"].includes(value.toLowerCase()) ? "已开启" : "已关闭";
  }
  return value;
}
