import { useEffect, useMemo, useState } from "react";

import { api, isAuthFailure } from "./api";
import type { ConfigFieldRecord } from "./types";

const GROUP_LABELS: Record<string, string> = {
  app: "应用 / 引导（只读）",
  business_db: "业务数据库",
  llm: "主模型 LLM",
  vector: "向量检索",
  sql: "SQL / 执行治理",
};

const GROUP_ORDER = ["business_db", "llm", "vector", "sql", "app"];

const SOURCE_LABELS: Record<string, string> = {
  override: "已覆盖",
  env: "环境变量",
  default: "默认值",
};

type Draft = Record<string, string>;

export function SystemSettings(props: { token: string; onAuthFailure: () => void }) {
  const [fields, setFields] = useState<ConfigFieldRecord[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const applyFields = (next: ConfigFieldRecord[]) => {
    setFields(next);
    const nextDraft: Draft = {};
    for (const field of next) {
      nextDraft[field.name] = field.value ?? "";
    }
    setDraft(nextDraft);
  };

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await api.adminGetConfig(props.token);
      applyFields(response.fields);
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

  const dirty = useMemo(() => {
    return fields.some((field) => field.editable && (field.value ?? "") !== (draft[field.name] ?? ""));
  }, [fields, draft]);

  const handleSave = async () => {
    // Only send changed editable fields. Empty string -> null (revert to baseline).
    const values: Record<string, string | null> = {};
    for (const field of fields) {
      if (!field.editable) continue;
      const current = draft[field.name] ?? "";
      if (current === (field.value ?? "")) continue;
      values[field.name] = current.trim() === "" ? null : current;
    }
    if (Object.keys(values).length === 0) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminUpdateConfig(props.token, values);
      applyFields(response.fields);
      setMessage("配置已保存并热重载");
    } catch (err) {
      if (isAuthFailure(err)) {
        props.onAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "保存配置失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="studio-root">
      <div className="studio-head">
        <div>
          <div className="studio-list-title">系统设置</div>
          <div className="studio-list-sub">
            运行时配置存入运行库并热生效。留空表示恢复为环境变量 / 默认值。运行库连接串与鉴权密钥仅可在 .env 配置。
          </div>
        </div>
        <div className="studio-status-actions">
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={loading || saving}>
            重新加载
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={() => void handleSave()}
            disabled={!dirty || saving || loading}
          >
            {saving ? "保存中…" : "保存并热重载"}
          </button>
        </div>
      </div>

      {error ? <div className="studio-status-error">{error}</div> : null}
      {message ? <div className="studio-status-ok">{message}</div> : null}
      {loading ? <div className="studio-loading">加载中…</div> : null}

      {!loading
        ? grouped.map((section) => (
            <div key={section.group} className="studio-subsection">
              <div className="studio-subsection-head">{section.label}</div>
              <div className="studio-grid-2">
                {section.items.map((field) => (
                  <ConfigField
                    key={field.name}
                    field={field}
                    value={draft[field.name] ?? ""}
                    onChange={(next) => setDraft((prev) => ({ ...prev, [field.name]: next }))}
                  />
                ))}
              </div>
            </div>
          ))
        : null}
    </div>
  );
}

function ConfigField(props: {
  field: ConfigFieldRecord;
  value: string;
  onChange: (value: string) => void;
}) {
  const { field } = props;
  const sourceLabel = SOURCE_LABELS[field.source] ?? field.source;
  const isBool = field.type === "bool" || field.type === "optional_bool";

  return (
    <label className="studio-field">
      <span className="studio-field-label">
        {field.name}
        <span className="studio-badge">{sourceLabel}</span>
        {field.secret ? <span className="studio-badge">密钥</span> : null}
      </span>
      {isBool ? (
        <select
          className="studio-input"
          value={props.value}
          disabled={!field.editable}
          onChange={(event) => props.onChange(event.target.value)}
        >
          {field.type === "optional_bool" ? <option value="">（未设置）</option> : null}
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : (
        <input
          className="studio-input"
          type="text"
          value={props.value}
          disabled={!field.editable}
          placeholder={field.editable ? "留空恢复默认" : "仅 .env 可配置"}
          onChange={(event) => props.onChange(event.target.value)}
        />
      )}
    </label>
  );
}
