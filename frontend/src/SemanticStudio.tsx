import { useCallback, useEffect, useMemo, useState } from "react";
import { api, isAuthFailure } from "./api";
import type {
  BusinessKnowledgeDocument,
  BusinessKnowledgeEntry,
  DatabaseStatusRecord,
  ExampleTemplateRecord,
  JoinPatternEntry,
  JoinPatternsDocument,
  SemanticAssetDraftRecord,
  SemanticReleaseDetailRecord,
  SemanticReleaseRecord,
  TableSchemaEntry,
  TablesDocument,
} from "./types";

type AssetTab = "database" | "tables" | "knowledge" | "joins" | "examples";

const ASSET_TABS: { key: AssetTab; label: string; hint: string }[] = [
  { key: "database", label: "数据库结构", hint: "当前连接、Schema 同步与发布" },
  { key: "tables", label: "表结构", hint: "物理表、字段、时间格式与关系" },
  { key: "knowledge", label: "业务知识", hint: "可复用业务规则、口径与禁忌" },
  { key: "joins", label: "Join Pattern", hint: "稳定的多表关联方式" },
  { key: "examples", label: "示例", hint: "人工确认的 NL2SQL few-shot" },
];

const TIME_GRAINS = ["day", "week", "month", "version", "unknown"];

const ASSET_LABELS: Record<string, string> = {
  tables_metadata: "表结构",
  business_knowledge: "业务知识",
  join_patterns: "Join Pattern",
  examples_template: "问题示例",
};

const SYNC_STATUS_LABELS: Record<string, string> = {
  not_synced: "未同步",
  syncing: "同步中",
  ready: "已同步",
  error: "同步失败",
};

const RELEASE_STATUS_LABELS: Record<string, string> = {
  building: "构建中",
  active: "当前生效",
  inactive: "历史版本",
  failed: "发布失败",
};

function errorText(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function parseTokens(value: string): string[] {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseLines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trimEnd())
    .filter((item) => item.trim().length > 0);
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatWarnings(warnings: string[]): string {
  return warnings.length ? `；${warnings.join("；")}` : "";
}

function TextField(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="studio-field">
      <span className="studio-field-label">{props.label}</span>
      <input
        className="studio-input"
        value={props.value}
        placeholder={props.placeholder}
        onChange={(event) => props.onChange(event.target.value)}
      />
      {props.hint ? <span className="studio-field-hint">{props.hint}</span> : null}
    </label>
  );
}

function AreaField(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  rows?: number;
  mono?: boolean;
}) {
  return (
    <label className="studio-field">
      <span className="studio-field-label">{props.label}</span>
      <textarea
        className={`studio-textarea${props.mono ? " is-mono" : ""}`}
        value={props.value}
        rows={props.rows || 3}
        placeholder={props.placeholder}
        onChange={(event) => props.onChange(event.target.value)}
      />
      {props.hint ? <span className="studio-field-hint">{props.hint}</span> : null}
    </label>
  );
}

function TokenListField(props: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="studio-field">
      <span className="studio-field-label">{props.label}</span>
      <input
        className="studio-input"
        value={props.values.join(", ")}
        placeholder={props.placeholder || "用逗号分隔"}
        onChange={(event) => props.onChange(parseTokens(event.target.value))}
      />
      <span className="studio-field-hint">{props.hint || "用逗号分隔多个值"}</span>
    </label>
  );
}

function LineListField(props: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  hint?: string;
  rows?: number;
  mono?: boolean;
}) {
  return (
    <label className="studio-field">
      <span className="studio-field-label">{props.label}</span>
      <textarea
        className={`studio-textarea${props.mono ? " is-mono" : ""}`}
        value={props.values.join("\n")}
        rows={props.rows || 4}
        placeholder={props.placeholder || "每行一条"}
        onChange={(event) => props.onChange(parseLines(event.target.value))}
      />
      <span className="studio-field-hint">{props.hint || "每行一条"}</span>
    </label>
  );
}

type StudioListItem = { id: string; title: string; subtitle?: string };

function StudioLayout(props: {
  items: StudioListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  createLabel: string;
  searchPlaceholder: string;
  emptyLabel: string;
  children: React.ReactNode;
}) {
  const [query, setQuery] = useState("");
  const filtered = props.items.filter((item) => {
    if (!query.trim()) {
      return true;
    }
    const needle = query.trim().toLowerCase();
    return (
      item.title.toLowerCase().includes(needle) ||
      (item.subtitle || "").toLowerCase().includes(needle) ||
      item.id.toLowerCase().includes(needle)
    );
  });
  return (
    <div className="studio-layout">
      <aside className="studio-list">
        <div className="studio-list-head">
          <input
            className="studio-search"
            value={query}
            placeholder={props.searchPlaceholder}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="primary-button studio-create" type="button" onClick={props.onCreate}>
            {props.createLabel}
          </button>
        </div>
        <div className="studio-list-body">
          {filtered.length ? (
            filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`studio-list-item${item.id === props.selectedId ? " is-active" : ""}`}
                onClick={() => props.onSelect(item.id)}
              >
                <span className="studio-list-title">{item.title}</span>
                {item.subtitle ? <span className="studio-list-sub">{item.subtitle}</span> : null}
              </button>
            ))
          ) : (
            <div className="studio-empty">{props.emptyLabel}</div>
          )}
        </div>
      </aside>
      <section className="studio-detail">{props.children}</section>
    </div>
  );
}

function StudioStatusBar(props: {
  dirty: boolean;
  saving: boolean;
  message: string;
  error: string;
  onSave: () => void;
  onReset: () => void;
  saveLabel?: string;
  idleLabel?: string;
}) {
  return (
    <div className="studio-statusbar">
      <div className="studio-status-msg">
        {props.error ? (
          <span className="studio-status-error">{props.error}</span>
        ) : props.message ? (
          <span className="studio-status-ok">{props.message}</span>
        ) : props.dirty ? (
          <span className="studio-status-dirty">有未保存的修改</span>
        ) : (
          <span className="studio-status-idle">{props.idleLabel || "已同步"}</span>
        )}
      </div>
      <div className="studio-status-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={props.onReset}
          disabled={props.saving || !props.dirty}
        >
          还原
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={props.onSave}
          disabled={props.saving || !props.dirty}
        >
          {props.saving ? "保存中" : props.saveLabel || "保存"}
        </button>
      </div>
    </div>
  );
}

/* ----------------------------- Tables editor ----------------------------- */

function emptyTable(): TableSchemaEntry {
  return { description: "", columns: [] };
}

function TablesEditor(props: { token: string }) {
  const [doc, setDoc] = useState<TablesDocument | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [baseline, setBaseline] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const record = await api.adminGetSemanticDraft(props.token, "tables_metadata");
      const content = (record.content || {}) as TablesDocument;
      setDoc(content);
      setVersion(record.version);
      setBaseline(JSON.stringify(content));
      const names = Object.keys(content);
      setSelected((current) => (current && names.includes(current) ? current : names[0] || null));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => (doc ? JSON.stringify(doc) !== baseline : false), [doc, baseline]);
  const current = doc && selected ? doc[selected] : null;

  function patchTable(patch: Partial<TableSchemaEntry>) {
    if (!doc || !selected) {
      return;
    }
    setDoc({ ...doc, [selected]: { ...doc[selected], ...patch } });
  }

  function renameTable(nextName: string) {
    if (!doc || !selected) {
      return;
    }
    const trimmed = nextName.trim();
    if (!trimmed || trimmed === selected) {
      return;
    }
    if (doc[trimmed]) {
      setError(`表名已存在：${trimmed}`);
      return;
    }
    const next: TablesDocument = {};
    for (const [key, value] of Object.entries(doc)) {
      next[key === selected ? trimmed : key] = value;
    }
    setDoc(next);
    setSelected(trimmed);
  }

  function createTable() {
    if (!doc || version === null) {
      return;
    }
    let name = "new_table";
    let index = 1;
    while (doc[name]) {
      name = `new_table_${index++}`;
    }
    setDoc({ ...doc, [name]: emptyTable() });
    setSelected(name);
    setMessage("");
    setError("");
  }

  function deleteTable() {
    if (!doc || !selected) {
      return;
    }
    if (!window.confirm(`确认删除表 ${selected}？该操作会在保存后写入。`)) {
      return;
    }
    const next = { ...doc };
    delete next[selected];
    const names = Object.keys(next);
    setDoc(next);
    setSelected(names[0] || null);
  }

  async function save() {
    if (!doc || version === null) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminUpdateSemanticDraft(
        props.token,
        "tables_metadata",
        doc,
        version,
      );
      const content = (response.draft.content || {}) as TablesDocument;
      setDoc(content);
      setVersion(response.draft.version);
      setBaseline(JSON.stringify(content));
      setMessage(`表结构草稿已保存（v${response.draft.version}）${formatWarnings(response.warnings)}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!baseline) {
      return;
    }
    const content = JSON.parse(baseline) as TablesDocument;
    setDoc(content);
    setError("");
    setMessage("");
  }

  const timeFields = current?.time_fields || {};
  const relationships = current?.relationships || {};

  const items: StudioListItem[] = doc
    ? Object.keys(doc).map((name) => ({
        id: name,
        title: name,
        subtitle: doc[name].description ? String(doc[name].description).slice(0, 40) : undefined,
      }))
    : [];

  if (loading && !doc) {
    return <div className="studio-loading">加载表结构…</div>;
  }

  return (
    <StudioLayout
      items={items}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        setMessage("");
        setError("");
      }}
      onCreate={createTable}
      createLabel="新增表"
      searchPlaceholder="搜索表名 / 描述"
      emptyLabel="暂无表结构。"
    >
      {current && selected ? (
        <div className="studio-form">
          <div className="studio-form-head">
            <TextField
              label="表名"
              value={selected}
              onChange={renameTable}
              hint="修改表名会在保存时同步；需与真实物理表一致"
            />
            <button className="secondary-button is-danger" type="button" onClick={deleteTable}>
              删除表
            </button>
          </div>
          <AreaField
            label="描述"
            value={String(current.description || "")}
            onChange={(value) => patchTable({ description: value })}
            rows={2}
            placeholder="表用途、粒度、横表/纵表说明"
          />
          <LineListField
            label="字段"
            values={current.columns || []}
            onChange={(values) => patchTable({ columns: values })}
            rows={6}
            mono
            hint="每行一个字段，可保留 (中文说明)"
            placeholder={"id\nperiod (业务时间字段)"}
          />
          <TextField
            label="MAIN_KEY"
            value={String(current.MAIN_KEY || "")}
            onChange={(value) => patchTable({ MAIN_KEY: value })}
            placeholder="COL_A,COL_B"
            hint="来自物理主键；复合主键使用逗号分隔"
          />

          <div className="studio-subsection">
            <div className="studio-subsection-head">
              <span>时间字段</span>
              <button
                type="button"
                className="studio-mini-add"
                onClick={() => {
                  let name = "TIME_FIELD";
                  let index = 1;
                  while (timeFields[name]) {
                    name = `TIME_FIELD_${index++}`;
                  }
                  patchTable({ time_fields: { ...timeFields, [name]: { grain: "month", format: "YYYYMM" } } });
                }}
              >
                + 添加时间字段
              </button>
            </div>
            <p className="studio-subsection-hint">
              format 表示物理存储格式（YYYYMM / YYYYMMDD / YYYY-MM / YYYY-MM-DD），不是自然语言输入格式。
            </p>
            {Object.keys(timeFields).length ? (
              Object.entries(timeFields).map(([fieldName, spec]) => (
                <div className="studio-timefield-row" key={fieldName}>
                  <input
                    className="studio-input"
                    value={fieldName}
                    onChange={(event) => {
                      const nextName = event.target.value;
                      const next = { ...timeFields };
                      const value = next[fieldName];
                      delete next[fieldName];
                      next[nextName] = value;
                      patchTable({ time_fields: next });
                    }}
                    placeholder="字段名"
                  />
                  <select
                    className="studio-input"
                    value={spec.grain || "month"}
                    onChange={(event) =>
                      patchTable({
                        time_fields: { ...timeFields, [fieldName]: { ...spec, grain: event.target.value } },
                      })
                    }
                  >
                    {TIME_GRAINS.map((grain) => (
                      <option key={grain} value={grain}>
                        {grain}
                      </option>
                    ))}
                  </select>
                  <input
                    className="studio-input"
                    value={spec.format || ""}
                    placeholder="YYYYMM"
                    onChange={(event) =>
                      patchTable({
                        time_fields: { ...timeFields, [fieldName]: { ...spec, format: event.target.value } },
                      })
                    }
                  />
                  <input
                    className="studio-input"
                    value={(spec.semantic_names || []).join(", ")}
                    placeholder="语义名，逗号分隔"
                    onChange={(event) =>
                      patchTable({
                        time_fields: {
                          ...timeFields,
                          [fieldName]: { ...spec, semantic_names: parseTokens(event.target.value) },
                        },
                      })
                    }
                  />
                  <button
                    type="button"
                    className="studio-mini-remove"
                    onClick={() => {
                      const next = { ...timeFields };
                      delete next[fieldName];
                      patchTable({ time_fields: next });
                    }}
                  >
                    移除
                  </button>
                </div>
              ))
            ) : (
              <div className="studio-empty-inline">未声明时间字段。</div>
            )}
          </div>

          <div className="studio-subsection">
            <div className="studio-subsection-head">
              <span>关系</span>
              <button
                type="button"
                className="studio-mini-add"
                onClick={() => {
                  let name = "SOURCE_FIELD";
                  let index = 1;
                  while (relationships[name]) {
                    name = `FIELD_${index++}`;
                  }
                  patchTable({ relationships: { ...relationships, [name]: "" } });
                }}
              >
                + 添加关系
              </button>
            </div>
            <p className="studio-subsection-hint">左侧为本表字段，右侧为目标 表.字段（可逗号分隔多个目标）。</p>
            {Object.keys(relationships).length ? (
              Object.entries(relationships).map(([field, target]) => (
                <div className="studio-rel-row" key={field}>
                  <input
                    className="studio-input"
                    value={field}
                    placeholder="本表字段"
                    onChange={(event) => {
                      const nextName = event.target.value;
                      const next = { ...relationships };
                      const value = next[field];
                      delete next[field];
                      next[nextName] = value;
                      patchTable({ relationships: next });
                    }}
                  />
                  <input
                    className="studio-input"
                    value={String(target)}
                    placeholder="target_table.column"
                    onChange={(event) =>
                      patchTable({ relationships: { ...relationships, [field]: event.target.value } })
                    }
                  />
                  <button
                    type="button"
                    className="studio-mini-remove"
                    onClick={() => {
                      const next = { ...relationships };
                      delete next[field];
                      patchTable({ relationships: next });
                    }}
                  >
                    移除
                  </button>
                </div>
              ))
            ) : (
              <div className="studio-empty-inline">未声明关系。</div>
            )}
          </div>

          <StudioStatusBar
            dirty={dirty}
            saving={saving}
            message={message}
            error={error}
            onSave={() => void save()}
            onReset={reset}
            saveLabel="保存表结构"
            idleLabel={version === null ? "草稿未加载" : `草稿 v${version}`}
          />
        </div>
      ) : (
        <div className="studio-placeholder">从左侧选择一张表，或新增表。</div>
      )}
    </StudioLayout>
  );
}

/* --------------------------- Knowledge editor --------------------------- */

function emptyKnowledgeEntry(id: string): BusinessKnowledgeEntry {
  return { id, domains: [], tables: [], keywords: [], notes: [] };
}

function KnowledgeEditor(props: { token: string }) {
  const [doc, setDoc] = useState<BusinessKnowledgeDocument | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [baseline, setBaseline] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const record = await api.adminGetSemanticDraft(props.token, "business_knowledge");
      const content = (record.content || { entries: [] }) as BusinessKnowledgeDocument;
      if (!Array.isArray(content.entries)) {
        content.entries = [];
      }
      setDoc(content);
      setVersion(record.version);
      setBaseline(JSON.stringify(content));
      setSelected((current) => {
        const ids = content.entries.map((entry) => entry.id);
        return current && ids.includes(current) ? current : ids[0] || null;
      });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => (doc ? JSON.stringify(doc) !== baseline : false), [doc, baseline]);
  const entries = doc?.entries || [];
  const currentIndex = entries.findIndex((entry) => entry.id === selected);
  const current = currentIndex >= 0 ? entries[currentIndex] : null;

  function patchEntry(patch: Partial<BusinessKnowledgeEntry>) {
    if (!doc || currentIndex < 0) {
      return;
    }
    const nextEntries = entries.slice();
    nextEntries[currentIndex] = { ...nextEntries[currentIndex], ...patch };
    setDoc({ ...doc, entries: nextEntries });
  }

  function createEntry() {
    if (!doc || version === null) {
      return;
    }
    let id = "new_knowledge_entry";
    let index = 1;
    while (entries.some((entry) => entry.id === id)) {
      id = `new_knowledge_entry_${index++}`;
    }
    setDoc({ ...doc, entries: [...entries, emptyKnowledgeEntry(id)] });
    setSelected(id);
    setMessage("");
    setError("");
  }

  function deleteEntry() {
    if (!doc || currentIndex < 0) {
      return;
    }
    if (!window.confirm(`确认删除业务知识条目 ${selected}？`)) {
      return;
    }
    const nextEntries = entries.filter((_, index) => index !== currentIndex);
    setDoc({ ...doc, entries: nextEntries });
    setSelected(nextEntries[0]?.id || null);
  }

  async function save() {
    if (!doc || version === null) {
      return;
    }
    const ids = entries.map((entry) => entry.id.trim());
    if (ids.some((id) => !id)) {
      setError("每个条目都需要非空 id");
      return;
    }
    if (new Set(ids).size !== ids.length) {
      setError("条目 id 必须唯一");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminUpdateSemanticDraft(
        props.token,
        "business_knowledge",
        doc,
        version,
      );
      const content = (response.draft.content || { entries: [] }) as BusinessKnowledgeDocument;
      setDoc(content);
      setVersion(response.draft.version);
      setBaseline(JSON.stringify(content));
      setMessage(`业务知识草稿已保存（v${response.draft.version}）${formatWarnings(response.warnings)}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!baseline) {
      return;
    }
    setDoc(JSON.parse(baseline) as BusinessKnowledgeDocument);
    setError("");
    setMessage("");
  }

  const items: StudioListItem[] = entries.map((entry) => ({
    id: entry.id,
    title: entry.id,
    subtitle: (entry.domains || []).join(" · ") || undefined,
  }));

  if (loading && !doc) {
    return <div className="studio-loading">加载业务知识…</div>;
  }

  return (
    <StudioLayout
      items={items}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        setMessage("");
        setError("");
      }}
      onCreate={createEntry}
      createLabel="新增条目"
      searchPlaceholder="搜索 id / 业务域"
      emptyLabel="暂无业务知识条目。"
    >
      {current ? (
        <div className="studio-form">
          <div className="studio-form-head">
            <TextField
              label="ID"
              value={current.id}
              onChange={(value) => patchEntry({ id: value })}
              hint="稳定、唯一、小写蛇形命名"
            />
            <button className="secondary-button is-danger" type="button" onClick={deleteEntry}>
              删除条目
            </button>
          </div>
          <TokenListField
            label="业务域 domains"
            values={current.domains || []}
            onChange={(values) => patchEntry({ domains: values })}
            hint="由当前业务语义定义，可配置多个域"
          />
          <TokenListField
            label="相关表 tables"
            values={current.tables || []}
            onChange={(values) => patchEntry({ tables: values })}
            hint="规则直接涉及的事实表 / 维表 / 桥接表"
          />
          <TokenListField
            label="关键词 keywords"
            values={current.keywords || []}
            onChange={(values) => patchEntry({ keywords: values })}
            hint="用户词、业务词与关键物理字段"
          />
          <LineListField
            label="规则 notes"
            values={current.notes || []}
            onChange={(values) => patchEntry({ notes: values })}
            rows={8}
            hint="每行一个可执行规则"
          />
          <StudioStatusBar
            dirty={dirty}
            saving={saving}
            message={message}
            error={error}
            onSave={() => void save()}
            onReset={reset}
            saveLabel="保存业务知识"
            idleLabel={version === null ? "草稿未加载" : `草稿 v${version}`}
          />
        </div>
      ) : (
        <div className="studio-placeholder">从左侧选择一条业务知识，或新增条目。</div>
      )}
    </StudioLayout>
  );
}

/* -------------------------- Join patterns editor -------------------------- */

function emptyJoinPattern(id: string): JoinPatternEntry {
  return { id, domains: [], tables: [], keywords: [], join_path: [], notes: [] };
}

function JoinPatternsEditor(props: { token: string }) {
  const [doc, setDoc] = useState<JoinPatternsDocument | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [baseline, setBaseline] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const record = await api.adminGetSemanticDraft(props.token, "join_patterns");
      const content = (record.content || { patterns: [] }) as JoinPatternsDocument;
      if (!Array.isArray(content.patterns)) {
        content.patterns = [];
      }
      setDoc(content);
      setVersion(record.version);
      setBaseline(JSON.stringify(content));
      setSelected((current) => {
        const ids = content.patterns.map((pattern) => pattern.id);
        return current && ids.includes(current) ? current : ids[0] || null;
      });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => (doc ? JSON.stringify(doc) !== baseline : false), [doc, baseline]);
  const patterns = doc?.patterns || [];
  const currentIndex = patterns.findIndex((pattern) => pattern.id === selected);
  const current = currentIndex >= 0 ? patterns[currentIndex] : null;

  function patchPattern(patch: Partial<JoinPatternEntry>) {
    if (!doc || currentIndex < 0) {
      return;
    }
    const next = patterns.slice();
    next[currentIndex] = { ...next[currentIndex], ...patch };
    setDoc({ ...doc, patterns: next });
  }

  function createPattern() {
    if (!doc || version === null) {
      return;
    }
    let id = "new_join_pattern";
    let index = 1;
    while (patterns.some((pattern) => pattern.id === id)) {
      id = `new_join_pattern_${index++}`;
    }
    setDoc({ ...doc, patterns: [...patterns, emptyJoinPattern(id)] });
    setSelected(id);
    setMessage("");
    setError("");
  }

  function deletePattern() {
    if (!doc || currentIndex < 0) {
      return;
    }
    if (!window.confirm(`确认删除 join pattern ${selected}？`)) {
      return;
    }
    const next = patterns.filter((_, index) => index !== currentIndex);
    setDoc({ ...doc, patterns: next });
    setSelected(next[0]?.id || null);
  }

  async function save() {
    if (!doc || version === null) {
      return;
    }
    const ids = patterns.map((pattern) => pattern.id.trim());
    if (ids.some((id) => !id)) {
      setError("每个 join pattern 都需要非空 id");
      return;
    }
    if (new Set(ids).size !== ids.length) {
      setError("join pattern id 必须唯一");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminUpdateSemanticDraft(
        props.token,
        "join_patterns",
        doc,
        version,
      );
      const content = (response.draft.content || { patterns: [] }) as JoinPatternsDocument;
      setDoc(content);
      setVersion(response.draft.version);
      setBaseline(JSON.stringify(content));
      setMessage(`Join Pattern 草稿已保存（v${response.draft.version}）${formatWarnings(response.warnings)}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!baseline) {
      return;
    }
    setDoc(JSON.parse(baseline) as JoinPatternsDocument);
    setError("");
    setMessage("");
  }

  const items: StudioListItem[] = patterns.map((pattern) => ({
    id: pattern.id,
    title: pattern.id,
    subtitle: (pattern.tables || []).join(" · ") || undefined,
  }));

  if (loading && !doc) {
    return <div className="studio-loading">加载 join pattern…</div>;
  }

  return (
    <StudioLayout
      items={items}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        setMessage("");
        setError("");
      }}
      onCreate={createPattern}
      createLabel="新增 pattern"
      searchPlaceholder="搜索 id / 表"
      emptyLabel="暂无 join pattern。"
    >
      {current ? (
        <div className="studio-form">
          <div className="studio-form-head">
            <TextField
              label="ID"
              value={current.id}
              onChange={(value) => patchPattern({ id: value })}
              hint="稳定、唯一、小写蛇形命名"
            />
            <button className="secondary-button is-danger" type="button" onClick={deletePattern}>
              删除 pattern
            </button>
          </div>
          <TokenListField
            label="业务域 domains"
            values={current.domains || []}
            onChange={(values) => patchPattern({ domains: values })}
          />
          <TokenListField
            label="涉及表 tables"
            values={current.tables || []}
            onChange={(values) => patchPattern({ tables: values })}
          />
          <TokenListField
            label="关键词 keywords"
            values={current.keywords || []}
            onChange={(values) => patchPattern({ keywords: values })}
          />
          <LineListField
            label="join path"
            values={current.join_path || []}
            onChange={(values) => patchPattern({ join_path: values })}
            rows={5}
            mono
            hint="每行一个连接条件，如 source.id = target.source_id"
          />
          <LineListField
            label="说明 notes"
            values={current.notes || []}
            onChange={(values) => patchPattern({ notes: values })}
            rows={5}
            hint="每行一条使用约束"
          />
          <StudioStatusBar
            dirty={dirty}
            saving={saving}
            message={message}
            error={error}
            onSave={() => void save()}
            onReset={reset}
            saveLabel="保存 join pattern"
            idleLabel={version === null ? "草稿未加载" : `草稿 v${version}`}
          />
        </div>
      ) : (
        <div className="studio-placeholder">从左侧选择一个 join pattern，或新增。</div>
      )}
    </StudioLayout>
  );
}

/* ----------------------------- Examples editor ----------------------------- */

type ExampleDraft = {
  id: string;
  isNew: boolean;
  question: string;
  sql: string;
  subject_domain: string;
  metrics: string[];
  dimensions: string[];
  tags: string[];
  notes: string;
  result_shape: string;
};

function templateToDraft(record: ExampleTemplateRecord): ExampleDraft {
  return {
    id: record.id || "",
    isNew: false,
    question: record.question,
    sql: record.sql,
    subject_domain: record.subject_domain || "unknown",
    metrics: record.metrics || [],
    dimensions: record.dimensions || [],
    tags: record.tags || [],
    notes: record.notes || "",
    result_shape: record.result_shape || "",
  };
}

function draftToTemplate(draft: ExampleDraft): ExampleTemplateRecord {
  const template: ExampleTemplateRecord = {
    id: draft.id.trim(),
    question: draft.question.trim(),
    sql: draft.sql.trim(),
  };
  if (draft.subject_domain && draft.subject_domain !== "unknown") {
    template.subject_domain = draft.subject_domain;
  }
  if (draft.metrics.length) {
    template.metrics = draft.metrics;
  }
  if (draft.dimensions.length) {
    template.dimensions = draft.dimensions;
  }
  if (draft.tags.length) {
    template.tags = draft.tags;
  }
  if (draft.notes.trim()) {
    template.notes = draft.notes.trim();
  }
  if (draft.result_shape.trim()) {
    template.result_shape = draft.result_shape.trim();
  }
  return template;
}

function ExamplesEditor(props: { token: string }) {
  const [records, setRecords] = useState<ExampleTemplateRecord[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [draft, setDraft] = useState<ExampleDraft | null>(null);
  const [baseline, setBaseline] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const record = await api.adminGetSemanticDraft(props.token, "examples_template");
      const content = Array.isArray(record.content)
        ? (record.content as ExampleTemplateRecord[])
        : [];
      const sorted = content
        .map((item, index) => ({ ...item, id: item.id?.trim() || `example_${index + 1}` }))
        .sort((a, b) => String(a.id).localeCompare(String(b.id)));
      setRecords(sorted);
      setVersion(record.version);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => (draft ? JSON.stringify(draft) !== baseline : false), [draft, baseline]);

  function selectRecord(id: string) {
    const record = records.find((item) => item.id === id);
    if (!record) {
      return;
    }
    const nextDraft = templateToDraft(record);
    setDraft(nextDraft);
    setBaseline(JSON.stringify(nextDraft));
    setSelected(id);
    setMessage("");
    setError("");
  }

  function createExample() {
    let id = "new_example";
    let index = 1;
    while (records.some((record) => record.id === id)) {
      id = `new_example_${index++}`;
    }
    const nextDraft: ExampleDraft = {
      id,
      isNew: true,
      question: "",
      sql: "",
      subject_domain: "unknown",
      metrics: [],
      dimensions: [],
      tags: [],
      notes: "",
      result_shape: "",
    };
    setDraft(nextDraft);
    setBaseline(JSON.stringify(nextDraft));
    setSelected(null);
    setMessage("");
    setError("");
  }

  function patchDraft(patch: Partial<ExampleDraft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  }

  async function save() {
    if (!draft || version === null) {
      return;
    }
    if (!draft.id.trim() || !draft.question.trim() || !draft.sql.trim()) {
      setError("ID、question 和 sql 必填");
      return;
    }
    if (records.some((record) => record.id === draft.id.trim() && record.id !== selected)) {
      setError(`示例 ID 已存在：${draft.id.trim()}`);
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const template = draftToTemplate(draft);
      const nextRecords = draft.isNew
        ? [...records, template]
        : records.map((record) => (record.id === selected ? template : record));
      const response = await api.adminUpdateSemanticDraft(
        props.token,
        "examples_template",
        nextRecords,
        version,
      );
      const savedRecords = Array.isArray(response.draft.content)
        ? (response.draft.content as ExampleTemplateRecord[])
            .map((item, index) => ({ ...item, id: item.id?.trim() || `example_${index + 1}` }))
            .sort((a, b) => String(a.id).localeCompare(String(b.id)))
        : [];
      const saved = savedRecords.find((record) => record.id === template.id) || template;
      const savedDraft = templateToDraft(saved);
      setRecords(savedRecords);
      setVersion(response.draft.version);
      setDraft(savedDraft);
      setBaseline(JSON.stringify(savedDraft));
      setSelected(saved.id || null);
      setMessage(
        `问题示例草稿已保存（v${response.draft.version}）${formatWarnings(response.warnings)}`,
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!baseline) {
      return;
    }
    setDraft(JSON.parse(baseline) as ExampleDraft);
    setError("");
    setMessage("");
  }

  const items: StudioListItem[] = records.map((record) => ({
    id: record.id || "",
    title: record.question.slice(0, 42) || record.id || "未命名示例",
    subtitle: record.subject_domain || "unknown",
  }));

  if (loading && !records.length) {
    return <div className="studio-loading">加载示例…</div>;
  }

  return (
    <StudioLayout
      items={items}
      selectedId={selected}
      onSelect={selectRecord}
      onCreate={createExample}
      createLabel="新增示例"
      searchPlaceholder="搜索问题 / id / 业务域"
      emptyLabel="暂无示例。"
    >
      {draft ? (
        <div className="studio-form">
          <div className="studio-form-head">
            <TextField
              label="ID"
              value={draft.id}
              onChange={(value) => patchDraft({ id: value })}
              hint="草稿内稳定且唯一；修改已有 ID 会作为重命名保存"
            />
            <TextField
              label="业务域 subject_domain"
              value={draft.subject_domain}
              onChange={(value) => patchDraft({ subject_domain: value })}
              hint="填写当前语义版本定义的业务域；无法归类时可填 unknown"
            />
          </div>
          <AreaField
            label="问题 question"
            value={draft.question}
            onChange={(value) => patchDraft({ question: value })}
            rows={2}
            placeholder="人工确认可独立理解的完整问题"
          />
          <AreaField
            label="SQL"
            value={draft.sql}
            onChange={(value) => patchDraft({ sql: value })}
            rows={8}
            mono
            placeholder="单条 Oracle 只读 SELECT / WITH ... SELECT"
            hint="真实物理表字段；默认 FETCH FIRST n ROWS ONLY；时间过滤匹配物理格式"
          />
          <div className="studio-grid-2">
            <TokenListField
              label="指标 metrics"
              values={draft.metrics}
              onChange={(values) => patchDraft({ metrics: values })}
            />
            <TokenListField
              label="维度 dimensions"
              values={draft.dimensions}
              onChange={(values) => patchDraft({ dimensions: values })}
            />
          </div>
          <TokenListField
            label="标签 tags"
            values={draft.tags}
            onChange={(values) => patchDraft({ tags: values })}
            hint="用于检索和覆盖分析"
          />
          <TextField
            label="result_shape"
            value={draft.result_shape}
            onChange={(value) => patchDraft({ result_shape: value })}
            placeholder="按需填写，留空自动推断"
          />
          <AreaField
            label="备注 notes"
            value={draft.notes}
            onChange={(value) => patchDraft({ notes: value })}
            rows={3}
          />
          <StudioStatusBar
            dirty={dirty}
            saving={saving}
            message={message}
            error={error}
            onSave={() => void save()}
            onReset={reset}
            saveLabel={draft.isNew ? "新增示例" : "保存示例"}
            idleLabel={version === null ? "草稿未加载" : `草稿 v${version}`}
          />
        </div>
      ) : (
        <div className="studio-placeholder">从左侧选择示例，或新增示例。</div>
      )}
    </StudioLayout>
  );
}

/* ------------------------ Database and releases ------------------------ */

function DatabaseStructurePanel(props: { token: string }) {
  const [status, setStatus] = useState<DatabaseStatusRecord | null>(null);
  const [drafts, setDrafts] = useState<SemanticAssetDraftRecord[]>([]);
  const [releases, setReleases] = useState<SemanticReleaseRecord[]>([]);
  const [activeRelease, setActiveRelease] = useState<SemanticReleaseDetailRecord | null>(null);
  const [includeViews, setIncludeViews] = useState(true);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [database, draftCollection, releaseCollection] = await Promise.all([
        api.adminDatabaseStatus(props.token),
        api.adminListSemanticDrafts(props.token),
        api.adminListSemanticReleases(props.token),
      ]);
      setStatus(database);
      setDrafts(draftCollection.drafts || []);
      setReleases(releaseCollection.releases || []);
      if (database.active_release_id) {
        const detail = await api.adminGetSemanticRelease(props.token, database.active_release_id);
        setActiveRelease(detail.release);
      } else {
        setActiveRelease(null);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function syncSchema() {
    setSyncing(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminSyncDatabase(props.token, includeViews);
      setMessage(
        `Schema 同步完成：${response.database.table_count} 张表、` +
          `${response.database.column_count} 列、${response.database.relationship_count} 个物理关系；` +
          `表结构草稿更新到 v${response.draft_version}${formatWarnings(response.warnings)}`,
      );
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSyncing(false);
    }
  }

  async function publish() {
    if (!window.confirm("发布当前全部语义草稿，并在准备完成后切换为当前生效版本？")) {
      return;
    }
    setPublishing(true);
    setError("");
    setMessage("");
    try {
      const response = await api.adminPublishSemanticRelease(props.token);
      setMessage(
        `语义版本 v${response.release.version} 已发布并生效${formatWarnings(response.warnings)}`,
      );
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPublishing(false);
    }
  }

  const hasUnpublishedChanges = drafts.some(
    (draft) => activeRelease?.draft_versions[draft.name] !== draft.version,
  );

  if (loading && !status) {
    return <div className="studio-loading">加载数据库与语义版本…</div>;
  }

  return (
    <div className="studio-control">
      {(error || message) && (
        <div className={error ? "studio-error" : "studio-message"}>{error || message}</div>
      )}

      <section className="studio-control-section">
        <div className="studio-control-head">
          <div>
            <h3>当前数据库</h3>
            <p>连接由部署配置提供，工作台只读取状态并同步允许范围内的物理结构。</p>
          </div>
          <button
            type="button"
            className="studio-mini-add"
            onClick={() => void load()}
            disabled={loading || syncing || publishing}
          >
            {loading ? "刷新中" : "刷新"}
          </button>
        </div>

        <dl className="studio-definition-grid">
          <div>
            <dt>连接状态</dt>
            <dd className={status?.connected ? "is-positive" : "is-error"}>
              {status?.connected ? "已连接" : status?.configured ? "连接失败" : "未配置"}
            </dd>
          </div>
          <div>
            <dt>数据库方言</dt>
            <dd>{status?.dialect || "oracle"}</dd>
          </div>
          <div>
            <dt>Schema 范围</dt>
            <dd>{status?.schema_scope.length ? status.schema_scope.join(", ") : "默认 schema"}</dd>
          </div>
          <div>
            <dt>同步状态</dt>
            <dd>{SYNC_STATUS_LABELS[status?.sync_status || "not_synced"]}</dd>
          </div>
          <div>
            <dt>物理目录</dt>
            <dd>
              {status?.table_count || 0} 表 / {status?.column_count || 0} 列 /{" "}
              {status?.relationship_count || 0} 关系
            </dd>
          </div>
          <div>
            <dt>最近同步</dt>
            <dd>{formatDateTime(status?.last_sync_at)}</dd>
          </div>
          <div>
            <dt>Catalog hash</dt>
            <dd>
              <code title={status?.catalog_hash || undefined}>
                {status?.catalog_hash ? status.catalog_hash.slice(0, 12) : "-"}
              </code>
            </dd>
          </div>
          <div>
            <dt>当前语义版本</dt>
            <dd>{activeRelease ? `v${activeRelease.version}` : "尚未发布"}</dd>
          </div>
        </dl>

        {status?.last_error ? <div className="studio-inline-error">{status.last_error}</div> : null}

        <div className="studio-control-actions">
          <label className="studio-check">
            <input
              type="checkbox"
              checked={includeViews}
              onChange={(event) => setIncludeViews(event.target.checked)}
              disabled={syncing}
            />
            <span>包含视图</span>
          </label>
          <button
            className="primary-button"
            type="button"
            onClick={() => void syncSchema()}
            disabled={syncing || !status?.configured}
          >
            {syncing ? "正在同步" : "同步 Schema"}
          </button>
        </div>
      </section>

      <section className="studio-control-section">
        <div className="studio-control-head">
          <div>
            <h3>语义草稿</h3>
            <p>各编辑器保存到独立草稿版本；发布时生成完整、不可变的语义快照。</p>
          </div>
          <button
            className="primary-button"
            type="button"
            onClick={() => void publish()}
            disabled={publishing || status?.sync_status !== "ready" || !hasUnpublishedChanges}
          >
            {publishing ? "正在发布" : "发布新版本"}
          </button>
        </div>

        <div className="studio-table-wrap">
          <table className="studio-control-table">
            <thead>
              <tr>
                <th>资产</th>
                <th>草稿版本</th>
                <th>发布状态</th>
                <th>最近保存</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((draft) => {
                const publishedVersion = activeRelease?.draft_versions[draft.name];
                const state =
                  publishedVersion === undefined
                    ? "未发布"
                    : publishedVersion === draft.version
                      ? "已发布"
                      : `有新修改（已发布 v${publishedVersion}）`;
                return (
                  <tr key={draft.name}>
                    <td>{ASSET_LABELS[draft.name] || draft.name}</td>
                    <td>v{draft.version}</td>
                    <td className={publishedVersion === draft.version ? "is-muted" : "is-pending"}>
                      {state}
                    </td>
                    <td>{formatDateTime(draft.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="studio-control-section">
        <div className="studio-control-head">
          <div>
            <h3>发布历史</h3>
            <p>历史版本只读；失败的发布不会改变当前生效版本。</p>
          </div>
        </div>

        {releases.length ? (
          <div className="studio-table-wrap">
            <table className="studio-control-table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>状态</th>
                  <th>发布人</th>
                  <th>创建时间</th>
                  <th>生效时间</th>
                </tr>
              </thead>
              <tbody>
                {releases.map((release) => (
                  <tr key={release.id}>
                    <td>
                      <strong>v{release.version}</strong>
                    </td>
                    <td className={release.status === "failed" ? "is-error" : ""}>
                      {RELEASE_STATUS_LABELS[release.status] || release.status}
                      {release.error ? <div className="studio-cell-error">{release.error}</div> : null}
                    </td>
                    <td>{release.created_by || "-"}</td>
                    <td>{formatDateTime(release.created_at)}</td>
                    <td>{formatDateTime(release.activated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="studio-empty-inline">尚无发布记录。</div>
        )}
      </section>
    </div>
  );
}

/* ------------------------------- Container ------------------------------- */

export function SemanticStudio(props: { token: string; onAuthFailure?: () => void }) {
  const [tab, setTab] = useState<AssetTab>("database");

  // Surface auth failures to the parent so it can clear the session.
  useEffect(() => {
    const handler = (event: PromiseRejectionEvent) => {
      if (isAuthFailure(event.reason)) {
        props.onAuthFailure?.();
      }
    };
    window.addEventListener("unhandledrejection", handler);
    return () => window.removeEventListener("unhandledrejection", handler);
  }, [props]);

  return (
    <div className="studio-root">
      <div className="studio-head">
        <div>
          <h2>语义工作台</h2>
          <p>同步当前数据库结构，维护语义草稿，并将完整快照发布给新的查询会话。</p>
        </div>
      </div>
      <div className="studio-tabs">
        {ASSET_TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`studio-tab${tab === item.key ? " is-active" : ""}`}
            onClick={() => setTab(item.key)}
          >
            <span className="studio-tab-label">{item.label}</span>
            <span className="studio-tab-hint">{item.hint}</span>
          </button>
        ))}
      </div>
      <div className="studio-body">
        {tab === "database" ? <DatabaseStructurePanel token={props.token} /> : null}
        {tab === "tables" ? <TablesEditor token={props.token} /> : null}
        {tab === "knowledge" ? <KnowledgeEditor token={props.token} /> : null}
        {tab === "joins" ? <JoinPatternsEditor token={props.token} /> : null}
        {tab === "examples" ? <ExamplesEditor token={props.token} /> : null}
      </div>
    </div>
  );
}
