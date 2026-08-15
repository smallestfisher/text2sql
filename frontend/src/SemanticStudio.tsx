import { useCallback, useEffect, useMemo, useState } from "react";
import { api, isAuthFailure } from "./api";
import type {
  BusinessKnowledgeDocument,
  BusinessKnowledgeEntry,
  DataSourceCollectionResponse,
  DataSourceCreateRequest,
  DataSourceRecord,
  ExampleRecord,
  ExampleTemplateRecord,
  JoinPatternEntry,
  JoinPatternsDocument,
  SchemaSyncResponse,
  TableSchemaEntry,
  TablesDocument,
} from "./types";

type AssetTab = "tables" | "knowledge" | "joins" | "examples" | "data_sources";

const ASSET_TABS: { key: AssetTab; label: string; hint: string }[] = [
  { key: "data_sources", label: "数据源", hint: "接入 Oracle 并同步物理表结构" },
  { key: "tables", label: "表结构", hint: "物理表、字段、时间格式与关系" },
  { key: "knowledge", label: "业务知识", hint: "可复用业务规则、口径与禁忌" },
  { key: "joins", label: "Join Pattern", hint: "稳定的多表关联方式" },
  { key: "examples", label: "示例", hint: "人工确认的 NL2SQL few-shot" },
];

const SUBJECT_DOMAINS = [
  "inventory",
  "demand",
  "plan_actual",
  "sales_financial",
  "dimension",
  "unknown",
];

const TIME_GRAINS = ["day", "week", "month", "version", "unknown"];

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
          <span className="studio-status-idle">已同步</span>
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
      const record = await api.adminGetMetadataDocument(props.token, "tables_metadata");
      const content = (record.content || {}) as TablesDocument;
      setDoc(content);
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
    if (!doc) {
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
    if (!doc) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const record = await api.adminUpdateMetadataDocument(props.token, "tables_metadata", doc);
      const content = (record.content || {}) as TablesDocument;
      setDoc(content);
      setBaseline(JSON.stringify(content));
      setMessage("表结构已保存并触发检索重载");
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
            placeholder={"id\nMONTH (需求起始月份)"}
          />
          <div className="studio-grid-3">
            <TextField
              label="MAIN_KEY"
              value={String(current.MAIN_KEY || "")}
              onChange={(value) => patchTable({ MAIN_KEY: value })}
              placeholder="COL_A,COL_B"
            />
            <TextField
              label="month_col"
              value={String(current.month_col || "")}
              onChange={(value) => patchTable({ month_col: value })}
            />
            <TextField
              label="version_col"
              value={String(current.version_col || "")}
              onChange={(value) => patchTable({ version_col: value })}
            />
          </div>
          <TextField
            label="date_col"
            value={String(current.date_col || "")}
            onChange={(value) => patchTable({ date_col: value })}
            placeholder="按需填写，日粒度表的日期字段"
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
                  let name = "FGCODE";
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
      const record = await api.adminGetMetadataDocument(props.token, "business_knowledge");
      const content = (record.content || { entries: [] }) as BusinessKnowledgeDocument;
      if (!Array.isArray(content.entries)) {
        content.entries = [];
      }
      setDoc(content);
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
    if (!doc) {
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
    if (!doc) {
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
      const record = await api.adminUpdateMetadataDocument(props.token, "business_knowledge", doc);
      const content = (record.content || { entries: [] }) as BusinessKnowledgeDocument;
      setDoc(content);
      setBaseline(JSON.stringify(content));
      setMessage("业务知识已保存并触发检索重载");
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
            hint={`可选：${SUBJECT_DOMAINS.join(" / ")}`}
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
      const record = await api.adminGetMetadataDocument(props.token, "join_patterns");
      const content = (record.content || { patterns: [] }) as JoinPatternsDocument;
      if (!Array.isArray(content.patterns)) {
        content.patterns = [];
      }
      setDoc(content);
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
    if (!doc) {
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
    if (!doc) {
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
      const record = await api.adminUpdateMetadataDocument(props.token, "join_patterns", doc);
      const content = (record.content || { patterns: [] }) as JoinPatternsDocument;
      setDoc(content);
      setBaseline(JSON.stringify(content));
      setMessage("Join pattern 已保存并触发检索重载");
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
            hint="每行一个连接条件，如 a.FGCODE = b.FGCODE"
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

function recordToDraft(record: ExampleRecord): ExampleDraft {
  // coverage_tags is derived as ["real", subject_domain, ...tags]; strip the two
  // derived leading values back to the author-supplied tags.
  const derived = new Set<string>(["real", record.subject_domain]);
  const tags = (record.coverage_tags || []).filter((tag) => !derived.has(tag));
  return {
    id: record.id,
    isNew: false,
    question: record.question,
    sql: record.sql,
    subject_domain: record.subject_domain || "unknown",
    metrics: record.metrics || [],
    dimensions: record.dimensions || [],
    tags,
    notes: record.notes || "",
    result_shape: record.result_shape || "",
  };
}

function draftToTemplate(draft: ExampleDraft): ExampleTemplateRecord {
  const template: ExampleTemplateRecord = {
    question: draft.question.trim(),
    sql: draft.sql.trim(),
  };
  if (!draft.isNew) {
    template.id = draft.id;
  } else if (draft.id.trim()) {
    template.id = draft.id.trim();
  }
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
  const [records, setRecords] = useState<ExampleRecord[]>([]);
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
      const response = await api.adminListExamples(props.token);
      const sorted = response.examples.slice().sort((a, b) => a.id.localeCompare(b.id));
      setRecords(sorted);
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
    const nextDraft = recordToDraft(record);
    setDraft(nextDraft);
    setBaseline(JSON.stringify(nextDraft));
    setSelected(id);
    setMessage("");
    setError("");
  }

  function createExample() {
    const nextDraft: ExampleDraft = {
      id: "",
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
    if (!draft) {
      return;
    }
    if (!draft.question.trim() || !draft.sql.trim()) {
      setError("question 和 sql 必填");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const template = draftToTemplate(draft);
      const response = draft.isNew
        ? await api.adminCreateExample(props.token, template)
        : await api.adminUpdateExample(props.token, draft.id, template);
      await load();
      const savedDraft = recordToDraft(response.example);
      setDraft(savedDraft);
      setBaseline(JSON.stringify(savedDraft));
      setSelected(response.example.id);
      setMessage(draft.isNew ? "示例已新增并触发检索重载" : "示例已更新并触发检索重载");
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
    id: record.id,
    title: record.question.slice(0, 42) || record.id,
    subtitle: record.subject_domain,
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
              placeholder={draft.isNew ? "留空则由 question 自动生成" : undefined}
              hint={draft.isNew ? "新增示例可留空自动生成" : "编辑已有示例时不建议修改 id"}
            />
            <div className="studio-domain-picker">
              <span className="studio-field-label">业务域</span>
              <select
                className="studio-input"
                value={draft.subject_domain}
                onChange={(event) => patchDraft({ subject_domain: event.target.value })}
              >
                {SUBJECT_DOMAINS.map((domain) => (
                  <option key={domain} value={domain}>
                    {domain}
                  </option>
                ))}
              </select>
            </div>
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
            hint="覆盖标签；保存后会自动附加 real 与业务域"
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
          />
        </div>
      ) : (
        <div className="studio-placeholder">从左侧选择示例，或新增示例。</div>
      )}
    </StudioLayout>
  );
}

/* ----------------------------- Data Sources ----------------------------- */

function DataSourcesEditor(props: { token: string }) {
  const [sources, setSources] = useState<DataSourceRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  // Warnings surfaced by the most recent sync, keyed by data source id.
  const [warningsBySource, setWarningsBySource] = useState<Record<string, string[]>>({});

  // New-source form state.
  const [name, setName] = useState("");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [schemas, setSchemas] = useState("");
  const [workspaceId, setWorkspaceId] = useState("default");
  const [domainId, setDomainId] = useState("default");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const record = await api.adminListDataSources(props.token);
      setSources(record.data_sources || []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [props.token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    if (!name.trim() || !databaseUrl.trim()) {
      setError("请填写名称和 Oracle 数据库 URL");
      return;
    }
    setError("");
    setMessage("");
    try {
      const payload: DataSourceCreateRequest = {
        workspace_id: workspaceId.trim() || "default",
        domain_id: domainId.trim() || "default",
        name: name.trim(),
        database_url: databaseUrl.trim(),
        schemas: schemas
          .split(/[\s,，]+/)
          .map((s) => s.trim())
          .filter(Boolean),
      };
      await api.adminCreateDataSource(props.token, payload);
      setName("");
      setDatabaseUrl("");
      setSchemas("");
      setMessage("已创建数据源（状态：草稿）。点“同步 schema”开始接入。");
      await load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function sync(record: DataSourceRecord) {
    setBusyId(record.id);
    setError("");
    setMessage("");
    try {
      const syncSchemas = record.schemas || [];
      const response: SchemaSyncResponse = await api.adminSyncDataSource(
        props.token,
        record.id,
        syncSchemas,
      );
      const warnings = response.warnings || [];
      setWarningsBySource((prev) => ({ ...prev, [record.id]: warnings }));
      const userActioned = warnings.some((w) => w.includes("Semantic Studio"));
      const summary =
        `同步完成：${response.table_count} 张表、${response.column_count} 列、${response.relationship_count} 物理外键。` +
        (userActioned
          ? " 业务字段（说明/时间字段/join）未被覆盖，请在「表结构」里补充。"
          : " 无新增待补充内容。");
      setMessage(summary);
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="studio-form">
      <div className="studio-form-head">
        <div className="studio-badge">数据源接入</div>
        <h3>注册 Oracle 数据源并同步物理表结构</h3>
      </div>
      <p className="studio-subsection-hint">
        同步只读取物理事实（表/列/主键/外键），作为占位写入「表结构」。
        业务内容（表说明、中文列说明、时间字段、join、口径）请到「表结构」里人工补充，
        再次同步不会覆盖这些人工内容。
      </p>

      <fieldset className="studio-fieldset">
        <legend>新建数据源</legend>
        <TextField
          label="名称"
          value={name}
          onChange={setName}
          placeholder="业务库 / 一厂产线 Oracle"
        />
        <TextField
          label="Oracle URL"
          value={databaseUrl}
          onChange={setDatabaseUrl}
          placeholder="oracle+oracledb://admin:***@host:1521/?service_name=FREEPDB1"
        />
        <TextField
          label="schema（逗号分隔，留空取默认）"
          value={schemas}
          onChange={setSchemas}
          placeholder="ADMIN, PROD"
        />
        <div className="studio-grid-2">
          <TextField label="workspace_id" value={workspaceId} onChange={setWorkspaceId} />
          <TextField label="domain_id" value={domainId} onChange={setDomainId} />
        </div>
        <button className="primary-button" type="button" onClick={() => void create()}>
          创建数据源
        </button>
      </fieldset>

      {(error || message) && (
        <div className={error ? "studio-error" : "studio-message"}>
          {error || message}
        </div>
      )}

      <div className="studio-subsection">
        <div className="studio-subsection-head">
          <span>已接入数据源</span>
          <button
            type="button"
            className="studio-mini-add"
            onClick={() => void load()}
            disabled={loading}
          >
            刷新
          </button>
        </div>
        {loading && sources.length === 0 ? (
          <div className="studio-loading">加载中…</div>
        ) : sources.length === 0 ? (
          <div className="studio-empty">暂无数据源。</div>
        ) : (
          <ul className="studio-list-rows">
            {sources.map((record) => (
              <li key={record.id} className="studio-list-row">
                <div className="studio-list-row-main">
                  <div className="studio-list-row-title">{record.name}</div>
                  <div className="studio-list-row-meta">
                    {record.dialect} · 状态 {record.status}
                    {record.schemas?.length ? ` · schema ${record.schemas.join(", ")}` : ""}
                    {record.last_sync_at ? ` · 最近同步 ${record.last_sync_at}` : ""}
                  </div>
                  {record.last_error ? (
                    <div className="studio-list-row-error">{record.last_error}</div>
                  ) : null}
                  {warningsBySource[record.id]?.length ? (
                    <div className="studio-list-row-warnings">
                      {warningsBySource[record.id].map((w, i) => (
                        <div key={i}>· {w}</div>
                      ))}
                    </div>
                  ) : null}
                </div>
                <button
                  className="primary-button"
                  type="button"
                  disabled={busyId === record.id}
                  onClick={() => void sync(record)}
                >
                  {busyId === record.id ? "同步中…" : "同步 schema"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ------------------------------- Container ------------------------------- */

export function SemanticStudio(props: { token: string; onAuthFailure?: () => void }) {
  const [tab, setTab] = useState<AssetTab>("tables");

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
    <div className="studio-root" id="admin-semantic">
      <div className="studio-head">
        <div>
          <div className="studio-badge">语义资产</div>
          <h2>表结构 / 业务知识 / Join Pattern / 示例</h2>
          <p>直接维护 SQL 生成依赖的语义事实。保存后自动触发检索重载，无需重启服务。</p>
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
        {tab === "data_sources" ? <DataSourcesEditor token={props.token} /> : null}
        {tab === "tables" ? <TablesEditor token={props.token} /> : null}
        {tab === "knowledge" ? <KnowledgeEditor token={props.token} /> : null}
        {tab === "joins" ? <JoinPatternsEditor token={props.token} /> : null}
        {tab === "examples" ? <ExamplesEditor token={props.token} /> : null}
      </div>
    </div>
  );
}
