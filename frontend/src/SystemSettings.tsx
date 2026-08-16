import { useEffect, useMemo, useState } from "react";

import { api, isAuthFailure } from "./api";
import type { ConfigFieldRecord } from "./types";

const GROUP_LABELS: Record<string, string> = {
  app: "应用 / 本地持久化",
  business_db: "业务数据库",
  llm: "主模型 LLM",
  vector: "向量检索",
  sql: "SQL / 执行治理",
};

const GROUP_ORDER = ["business_db", "llm", "vector", "sql", "app"];

const SOURCE_LABELS: Record<string, string> = {
  env: "环境变量",
  default: "默认值",
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
      items: map.get(group) as ConfigFieldRecord[],
    }));
  }, [fields]);

  return (
    <div className="studio-root">
      <div className="studio-head">
        <div>
          <div className="studio-list-title">系统设置</div>
          <div className="studio-list-sub">
            配置由环境变量或 Secret 注入。修改部署配置后重启后端生效；运行库只保存产品状态。
          </div>
        </div>
        <div className="studio-status-actions">
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>
            重新加载
          </button>
        </div>
      </div>

      {error ? <div className="studio-status-error">{error}</div> : null}
      {loading ? <div className="studio-loading">加载中…</div> : null}

      {!loading
        ? grouped.map((section) => (
            <div key={section.group} className="studio-subsection">
              <div className="studio-subsection-head">{section.label}</div>
              <div className="studio-grid-2">
                {section.items.map((field) => (
                  <ConfigField key={field.name} field={field} />
                ))}
              </div>
            </div>
          ))
        : null}
    </div>
  );
}

function ConfigField(props: { field: ConfigFieldRecord }) {
  const { field } = props;
  const sourceLabel = SOURCE_LABELS[field.source] ?? field.source;

  return (
    <label className="studio-field">
      <span className="studio-field-label">
        {field.name}
        <span className="studio-badge">{sourceLabel}</span>
        {field.secret ? <span className="studio-badge">密钥</span> : null}
      </span>
      <input
        className="studio-input"
        type="text"
        value={field.value ?? ""}
        disabled
        placeholder="未配置"
        readOnly
      />
    </label>
  );
}
