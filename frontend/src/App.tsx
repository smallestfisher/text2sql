import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type {
  ChatMessage,
  ChatResponse,
  ChatSession,
  EvaluationSummary,
  FeedbackSummary,
  MetadataOverview,
  RoleRecord,
  RuntimeQueryLogRecord,
  RuntimeRetrievalLogRecord,
  RuntimeSqlAuditRecord,
  RuntimeStatus,
  SessionSnapshotRecord,
  TraceRecord,
  UserContext,
  UserUpsertPayload,
  ViewMode,
} from "./types";

const TOKEN_KEY = "text2sql.token";

type AuthMode = "login" | "bootstrap";
type InspectorTab = "result" | "sql" | "trace" | "state";
type LoadSessionsOptions = {
  preferredSessionId?: string | null;
  preserveCurrentResponse?: boolean;
};

const emptyUserForm: UserUpsertPayload = {
  username: "",
  password: "",
  roles: ["viewer"],
  can_view_sql: true,
  can_execute_sql: true,
  is_active: true,
};

function App() {
  const [token, setToken] = useState<string | null>(() => window.localStorage.getItem(TOKEN_KEY));
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [authError, setAuthError] = useState("");
  const [authPending, setAuthPending] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserContext | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("workspace");

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [workspaceError, setWorkspaceError] = useState("");
  const [chatPending, setChatPending] = useState(false);
  const [sessionPending, setSessionPending] = useState(false);

  const [currentResponse, setCurrentResponse] = useState<ChatResponse | null>(null);
  const [sessionSnapshots, setSessionSnapshots] = useState<SessionSnapshotRecord[]>([]);
  const [queryLogs, setQueryLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<TraceRecord | null>(null);
  const [selectedTraceRetrieval, setSelectedTraceRetrieval] = useState<RuntimeRetrievalLogRecord[]>([]);
  const [selectedTraceSqlAudit, setSelectedTraceSqlAudit] = useState<RuntimeSqlAuditRecord | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("result");

  const [feedbackComment, setFeedbackComment] = useState("");
  const [myFeedbackSummary, setMyFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current: "", next: "" });
  const [passwordMessage, setPasswordMessage] = useState("");

  const [adminPending, setAdminPending] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [metadataOverview, setMetadataOverview] = useState<MetadataOverview | null>(null);
  const [adminUsers, setAdminUsers] = useState<UserContext[]>([]);
  const [adminRoles, setAdminRoles] = useState<RoleRecord[]>([]);
  const [adminLogs, setAdminLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [adminFeedbackSummary, setAdminFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [adminEvalSummary, setAdminEvalSummary] = useState<EvaluationSummary | null>(null);
  const [adminError, setAdminError] = useState("");
  const [userForm, setUserForm] = useState<UserUpsertPayload>(emptyUserForm);
  const [userIdInput, setUserIdInput] = useState("");

  useEffect(() => {
    void initialize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (token && viewMode === "admin" && currentUser?.roles.includes("admin")) {
      void loadAdminData(token);
    }
  }, [token, viewMode, currentUser]);

  const selectedSession = useMemo(
    () => sessions.find((item) => item.id === selectedSessionId) || null,
    [sessions, selectedSessionId],
  );

  const canViewSql = currentUser?.can_view_sql ?? false;
  const canExecuteSql = currentUser?.can_execute_sql ?? false;

  useEffect(() => {
    if (!canViewSql && inspectorTab === "sql") {
      setInspectorTab("result");
    }
  }, [canViewSql, inspectorTab]);

  async function initialize() {
    if (token) {
      try {
        const me = await api.me(token);
        setCurrentUser(me);
        await Promise.all([loadSessions(token), loadMyFeedbackSummary(token)]);
        return;
      } catch (error) {
        clearAuthState();
        setAuthError(errorMessage(error));
      }
    }
    try {
      const status = await api.bootstrapStatus();
      setAuthMode(status.has_users ? "login" : "bootstrap");
    } catch (error) {
      setAuthError(errorMessage(error));
    }
  }

  async function handleLogin(username: string, password: string) {
    setAuthPending(true);
    setAuthError("");
    try {
      const response =
        authMode === "bootstrap"
          ? await api.bootstrapAdmin(username, password).then(() => api.login(username, password))
          : await api.login(username, password);
      commitAuth(response.access_token, response.user);
      await Promise.all([loadSessions(response.access_token), loadMyFeedbackSummary(response.access_token)]);
    } catch (error) {
      setAuthError(errorMessage(error));
    } finally {
      setAuthPending(false);
    }
  }

  function commitAuth(nextToken: string, user: UserContext) {
    window.localStorage.setItem(TOKEN_KEY, nextToken);
    setToken(nextToken);
    setCurrentUser(user);
    setViewMode("workspace");
    setAuthError("");
  }

  function clearAuthState() {
    window.localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setCurrentUser(null);
    setSessions([]);
    setSelectedSessionId(null);
    setMessages([]);
    setCurrentResponse(null);
    setSelectedTrace(null);
    setSelectedTraceRetrieval([]);
    setSelectedTraceSqlAudit(null);
    setQueryLogs([]);
    setSessionSnapshots([]);
    setMyFeedbackSummary(null);
    setWorkspaceError("");
    setAdminError("");
    setQuestion("");
  }

  function resetWorkspaceDetails(preserveCurrentResponse = false) {
    setMessages([]);
    setSessionSnapshots([]);
    setQueryLogs([]);
    if (!preserveCurrentResponse) {
      setCurrentResponse(null);
    }
    setSelectedTrace(null);
    setSelectedTraceRetrieval([]);
    setSelectedTraceSqlAudit(null);
  }

  async function loadSessions(authToken: string, options: LoadSessionsOptions = {}) {
    setSessionPending(true);
    setWorkspaceError("");
    try {
      const response = await api.listSessions(authToken);
      setSessions(response.sessions);
      const nextSessionId =
        options.preferredSessionId && response.sessions.some((item) => item.id === options.preferredSessionId)
          ? options.preferredSessionId
          : selectedSessionId && response.sessions.some((item) => item.id === selectedSessionId)
            ? selectedSessionId
            : response.sessions[0]?.id || null;
      setSelectedSessionId(nextSessionId);
      if (nextSessionId) {
        await loadSessionBundle(authToken, nextSessionId, options.preserveCurrentResponse);
      } else {
        resetWorkspaceDetails(options.preserveCurrentResponse);
      }
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    } finally {
      setSessionPending(false);
    }
  }

  async function loadSessionBundle(authToken: string, sessionId: string, preserveCurrentResponse = false) {
    if (!preserveCurrentResponse) {
      resetWorkspaceDetails(false);
    } else {
      setMessages([]);
      setSessionSnapshots([]);
      setQueryLogs([]);
    }

    try {
      const [history, snapshots, logs] = await Promise.all([
        api.getSessionHistory(authToken, sessionId),
        api.getSessionSnapshots(authToken, sessionId),
        api.listMyQueryLogs(authToken, { sessionId, limit: 20 }),
      ]);
      setMessages(history.messages);
      setSessionSnapshots(snapshots);
      setQueryLogs(logs.query_logs);

      const latestTraceId = logs.query_logs[0]?.trace_id || null;
      if (latestTraceId) {
        await loadTraceBundle(latestTraceId, authToken);
      } else {
        setSelectedTrace(null);
        setSelectedTraceRetrieval([]);
        setSelectedTraceSqlAudit(null);
      }
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function handleCreateSession() {
    if (!token) {
      return;
    }
    setSessionPending(true);
    setWorkspaceError("");
    try {
      const response = await api.createSession(token);
      setSelectedSessionId(response.session.id);
      resetWorkspaceDetails();
      setInspectorTab("result");
      await loadSessions(token, { preferredSessionId: response.session.id });
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    } finally {
      setSessionPending(false);
    }
  }

  async function handleSelectSession(sessionId: string) {
    if (!token) {
      return;
    }
    setSelectedSessionId(sessionId);
    setWorkspaceError("");
    setInspectorTab("result");
    await loadSessionBundle(token, sessionId);
  }

  async function handleChatSubmit() {
    if (!token || !question.trim()) {
      return;
    }
    if (!canExecuteSql) {
      setWorkspaceError("当前账号没有执行 SQL 的权限。");
      return;
    }

    setChatPending(true);
    setWorkspaceError("");
    try {
      let sessionId = selectedSessionId;
      if (!sessionId) {
        const created = await api.createSession(token);
        sessionId = created.session.id;
        setSelectedSessionId(sessionId);
      }

      const response = await api.chatQuery(token, question.trim(), sessionId || undefined);
      setCurrentResponse(response);
      setInspectorTab("result");
      setQuestion("");
      await loadSessions(token, {
        preferredSessionId: sessionId,
        preserveCurrentResponse: true,
      });
      if (response.trace?.trace_id) {
        await loadTraceBundle(response.trace.trace_id, token);
      }
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    } finally {
      setChatPending(false);
    }
  }

  async function loadTraceBundle(traceId: string, authToken = token) {
    if (!authToken) {
      return;
    }
    try {
      const [trace, retrieval, sqlAudit] = await Promise.all([
        api.getTrace(authToken, traceId),
        api.getTraceRetrieval(authToken, traceId),
        api.getTraceSqlAudit(authToken, traceId).catch(() => null),
      ]);
      setSelectedTrace(trace);
      setSelectedTraceRetrieval(retrieval);
      setSelectedTraceSqlAudit(sqlAudit);
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function handleFeedback(type: "correct" | "incorrect" | "clarification" | "other") {
    if (!token || !currentResponse?.trace?.trace_id) {
      return;
    }
    try {
      await api.submitFeedback(token, {
        sessionId: selectedSessionId || undefined,
        traceId: currentResponse.trace.trace_id,
        feedbackType: type,
        comment: feedbackComment.trim() || undefined,
      });
      setFeedbackComment("");
      await loadMyFeedbackSummary(token);
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function loadMyFeedbackSummary(authToken: string) {
    try {
      const summary = await api.summarizeMyFeedbacks(authToken);
      setMyFeedbackSummary(summary);
    } catch {
      setMyFeedbackSummary(null);
    }
  }

  async function handleArchiveToggle(session: ChatSession) {
    if (!token) {
      return;
    }
    const nextStatus = session.status === "active" ? "archived" : "active";
    try {
      await api.updateSessionStatus(token, session.id, nextStatus);
      await loadSessions(token, { preferredSessionId: session.id });
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function handleChangePassword() {
    if (!token || !passwordForm.current || !passwordForm.next) {
      return;
    }
    setPasswordMessage("");
    try {
      await api.changePassword(token, passwordForm.current, passwordForm.next);
      setPasswordForm({ current: "", next: "" });
      setPasswordMessage("密码已更新。");
    } catch (error) {
      setPasswordMessage(errorMessage(error));
    }
  }

  async function loadAdminData(authToken: string) {
    setAdminPending(true);
    setAdminError("");
    try {
      const [status, overview, users, roles, logs, feedbacks, evalSummary] = await Promise.all([
        api.adminRuntimeStatus(authToken),
        api.adminMetadataOverview(authToken),
        api.adminUsers(authToken),
        api.adminRoles(authToken),
        api.adminQueryLogs(authToken),
        api.adminFeedbackSummary(authToken),
        api.adminEvaluationSummary(authToken),
      ]);
      setRuntimeStatus(status);
      setMetadataOverview(overview);
      setAdminUsers(users);
      setAdminRoles(roles);
      setAdminLogs(logs.query_logs);
      setAdminFeedbackSummary(feedbacks);
      setAdminEvalSummary(evalSummary);
    } catch (error) {
      setAdminError(errorMessage(error));
    } finally {
      setAdminPending(false);
    }
  }

  async function handleAdminUserSave() {
    if (!token || !userIdInput.trim() || !userForm.username.trim()) {
      return;
    }
    try {
      await api.adminUpsertUser(token, userIdInput.trim(), {
        ...userForm,
        roles: userForm.roles.map((item) => item.trim()).filter(Boolean),
      });
      setUserForm(emptyUserForm);
      setUserIdInput("");
      await loadAdminData(token);
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  if (!currentUser || !token) {
    return <LoginView mode={authMode} pending={authPending} error={authError} onSubmit={handleLogin} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">Text2SQL</div>
          <div className="brand-meta">{currentUser.username || currentUser.user_id}</div>
        </div>

        <nav className="nav-group">
          <button
            className={viewMode === "workspace" ? "nav-link is-active" : "nav-link"}
            onClick={() => setViewMode("workspace")}
          >
            工作台
          </button>
          {currentUser.roles.includes("admin") ? (
            <button
              className={viewMode === "admin" ? "nav-link is-active" : "nav-link"}
              onClick={() => setViewMode("admin")}
            >
              管理台
            </button>
          ) : null}
        </nav>

        <div className="sidebar-section">
          <div className="section-row">
            <span>会话</span>
            <button className="button ghost" onClick={() => void handleCreateSession()} disabled={sessionPending}>
              新建
            </button>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                key={session.id}
                className={selectedSessionId === session.id ? "session-item is-active" : "session-item"}
                onClick={() => void handleSelectSession(session.id)}
              >
                <span className="session-title">{session.title || "未命名会话"}</span>
                <span className="session-meta">
                  {session.status === "archived" ? "已归档" : "活跃"} · {formatDateTime(session.updated_at)}
                </span>
              </button>
            ))}
            {!sessions.length ? <div className="empty-line">还没有会话。</div> : null}
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="permission-note">
            <span>查看 SQL</span>
            <strong>{canViewSql ? "允许" : "禁止"}</strong>
          </div>
          <div className="permission-note">
            <span>执行 SQL</span>
            <strong>{canExecuteSql ? "允许" : "禁止"}</strong>
          </div>
          <button className="button secondary" onClick={() => setChangePasswordOpen(true)}>
            修改密码
          </button>
          <button className="button ghost" onClick={clearAuthState}>
            退出登录
          </button>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div>
            <h1>{viewMode === "workspace" ? "Text2SQL 工作台" : "系统管理"}</h1>
            <div className="topbar-meta">
              <span>{currentUser.roles.join(", ") || "user"}</span>
              {myFeedbackSummary ? <span>我的反馈 {myFeedbackSummary.total}</span> : null}
              <span>{selectedSession ? `当前会话 ${selectedSession.id.slice(0, 8)}` : "未选择会话"}</span>
            </div>
          </div>
          <div className="topbar-actions">
            {viewMode === "admin" ? (
              <button className="button secondary" onClick={() => token && void loadAdminData(token)} disabled={adminPending}>
                刷新
              </button>
            ) : null}
          </div>
        </header>

        {viewMode === "workspace" ? (
          <WorkspaceView
            session={selectedSession}
            messages={messages}
            question={question}
            onQuestionChange={setQuestion}
            onSend={() => void handleChatSubmit()}
            onArchiveToggle={(session) => void handleArchiveToggle(session)}
            canExecuteSql={canExecuteSql}
            pending={chatPending || sessionPending}
            error={workspaceError}
          />
        ) : (
          <AdminView
            pending={adminPending}
            error={adminError}
            runtimeStatus={runtimeStatus}
            metadataOverview={metadataOverview}
            adminUsers={adminUsers}
            adminRoles={adminRoles}
            adminLogs={adminLogs}
            feedbackSummary={adminFeedbackSummary}
            evaluationSummary={adminEvalSummary}
            userIdInput={userIdInput}
            onUserIdInputChange={setUserIdInput}
            userForm={userForm}
            onUserFormChange={setUserForm}
            onSaveUser={() => void handleAdminUserSave()}
          />
        )}
      </main>

      {changePasswordOpen ? (
        <div className="modal-backdrop" onClick={() => setChangePasswordOpen(false)}>
          <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
            <h2>修改密码</h2>
            <div className="field">
              <label htmlFor="current-password">当前密码</label>
              <input
                id="current-password"
                type="password"
                value={passwordForm.current}
                onChange={(event) => setPasswordForm((state) => ({ ...state, current: event.target.value }))}
              />
            </div>
            <div className="field">
              <label htmlFor="next-password">新密码</label>
              <input
                id="next-password"
                type="password"
                value={passwordForm.next}
                onChange={(event) => setPasswordForm((state) => ({ ...state, next: event.target.value }))}
              />
            </div>
            {passwordMessage ? <div className="inline-note">{passwordMessage}</div> : null}
            <div className="modal-actions">
              <button className="button ghost" onClick={() => setChangePasswordOpen(false)}>
                关闭
              </button>
              <button className="button primary" onClick={() => void handleChangePassword()}>
                更新
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LoginView(props: {
  mode: AuthMode;
  pending: boolean;
  error: string;
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="auth-shell">
      <div className="auth-panel">
        <div className="auth-note">{props.mode === "bootstrap" ? "初始化管理员账户" : "登录系统"}</div>
        <h1>{props.mode === "bootstrap" ? "创建首个管理员" : "进入工作台"}</h1>
        <div className="field">
          <label htmlFor="username">用户名</label>
          <input id="username" value={username} onChange={(event) => setUsername(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {props.error ? <div className="error-box">{props.error}</div> : null}
        <button
          className="button primary block"
          onClick={() => void props.onSubmit(username, password)}
          disabled={props.pending || !username.trim() || !password.trim()}
        >
          {props.pending ? "处理中..." : props.mode === "bootstrap" ? "创建账户" : "登录"}
        </button>
      </div>
    </div>
  );
}

function WorkspaceView(props: {
  session: ChatSession | null;
  messages: ChatMessage[];
  question: string;
  onQuestionChange: (value: string) => void;
  onSend: () => void;
  onArchiveToggle: (session: ChatSession) => void;
  canExecuteSql: boolean;
  pending: boolean;
  error: string;
}) {
  const activeSession = props.session;

  return (
    <div className="workspace-shell">
      <section className="panel conversation-panel">
        <div className="panel-header">
          <div>
            <h2>{props.session?.title || "当前会话"}</h2>
            <div className="panel-meta">
              <span>{props.session?.status === "archived" ? "已归档" : "活跃"}</span>
              <span>{props.session ? formatDateTime(props.session.updated_at) : "暂无会话"}</span>
            </div>
          </div>
          {activeSession ? (
            <button className="button ghost" onClick={() => props.onArchiveToggle(activeSession)}>
              {activeSession.status === "active" ? "归档" : "恢复"}
            </button>
          ) : null}
        </div>

        <div className="message-list">
          {props.messages.map((message) => (
            <article key={message.id} className={message.role === "user" ? "message-card message-user" : "message-card"}>
              <div className="message-row">
                <strong>{message.role === "user" ? "你" : "系统"}</strong>
                <span>{formatDateTime(message.created_at)}</span>
              </div>
              <div className="message-content">{message.content}</div>
            </article>
          ))}
          {props.pending ? (
            <article className="message-card message-status">
              <div className="message-row">
                <strong>系统</strong>
                <span>处理中</span>
              </div>
              <div className="message-content">正在分析问题并生成查询结果，请稍等。</div>
            </article>
          ) : null}
          {!props.messages.length ? <div className="empty-line">发送第一个问题后，这里会保留完整对话历史。</div> : null}
        </div>

        <div className="composer">
          <label htmlFor="prompt">问题</label>
          <textarea
            id="prompt"
            rows={4}
            value={props.question}
            onChange={(event) => props.onQuestionChange(event.target.value)}
            placeholder="例如：查询 2026 年 4 月各工厂库存，或者继续追问上一条结果。"
          />
          <div className="composer-footer">
            <div className="composer-note">
              {props.pending
                ? "请求已提交，正在处理。"
                : props.canExecuteSql
                  ? "支持多轮追问、补充问题和上下文延续。"
                  : "当前账号只能查看历史，不能执行 SQL。"}
            </div>
            <button className="button primary" onClick={props.onSend} disabled={props.pending || !props.question.trim() || !props.canExecuteSql}>
              {props.pending ? "发送中..." : "发送"}
            </button>
          </div>
          {props.error ? <div className="error-inline">{props.error}</div> : null}
        </div>
      </section>
    </div>
  );
}

function ResultTab(props: {
  response: ChatResponse | null;
  feedbackComment: string;
  onFeedbackCommentChange: (value: string) => void;
  onFeedback: (type: "correct" | "incorrect" | "clarification" | "other") => void;
}) {
  const execution = props.response?.execution;
  const answer = props.response?.answer;
  const classification = props.response?.classification;
  const retrieval = props.response?.retrieval;

  return (
    <div className="inspector-body">
      <section className="subpanel">
        <h3>回答</h3>
        <div className="answer-summary">{answer?.summary || "尚未生成结果。"}</div>
        {answer?.detail ? <div className="answer-detail">{answer.detail}</div> : null}
        {answer?.follow_up_hint ? <div className="inline-note">后续追问建议：{answer.follow_up_hint}</div> : null}
      </section>

      {classification ? (
        <section className="subpanel">
          <h3>理解结果</h3>
          <div className="data-grid compact">
            <div className="key-row">
              <span>类型</span>
              <span>{classification.question_type}</span>
            </div>
            <div className="key-row">
              <span>主题域</span>
              <span>{classification.subject_domain}</span>
            </div>
            <div className="key-row">
              <span>置信度</span>
              <span>{classification.confidence.toFixed(2)}</span>
            </div>
            <div className="key-row">
              <span>继承上下文</span>
              <span>{classification.inherit_context ? "是" : "否"}</span>
            </div>
          </div>
          {classification.reason_code ? <div className="inline-note">原因代码：{classification.reason_code}</div> : null}
          {classification.clarification_question ? (
            <div className="inline-note">澄清问题：{classification.clarification_question}</div>
          ) : null}
        </section>
      ) : null}

      {retrieval ? (
        <section className="subpanel">
          <h3>召回概览</h3>
          <div className="data-grid compact">
            <div className="key-row">
              <span>命中总数</span>
              <span>{retrieval.hits.length}</span>
            </div>
            <div className="key-row">
              <span>通道</span>
              <span>{retrieval.retrieval_channels.join(", ") || "-"}</span>
            </div>
            <div className="key-row">
              <span>视图</span>
              <span>{retrieval.semantic_views.join(", ") || "-"}</span>
            </div>
          </div>
        </section>
      ) : null}

      {execution ? (
        <section className="subpanel">
          <h3>执行结果</h3>
          <div className="data-grid compact">
            <div className="key-row">
              <span>状态</span>
              <span>{execution.status}</span>
            </div>
            <div className="key-row">
              <span>行数</span>
              <span>{execution.row_count}</span>
            </div>
            <div className="key-row">
              <span>耗时</span>
              <span>{execution.elapsed_ms ?? "-"} ms</span>
            </div>
          </div>
          {execution.columns.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {execution.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {execution.rows.map((row, index) => (
                    <tr key={`${index}-${execution.columns.join("-")}`}>
                      {execution.columns.map((column) => (
                        <td key={column}>{stringifyCell(row[column])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {execution.warnings.length ? <ListBlock title="Warnings" items={execution.warnings} /> : null}
          {execution.errors.length ? <ListBlock title="Errors" items={execution.errors} tone="error" /> : null}
        </section>
      ) : null}

      {props.response?.trace?.trace_id ? (
        <section className="subpanel">
          <h3>反馈</h3>
          <div className="feedback-actions">
            <button className="button secondary" onClick={() => props.onFeedback("correct")}>
              正确
            </button>
            <button className="button secondary" onClick={() => props.onFeedback("incorrect")}>
              不正确
            </button>
            <button className="button secondary" onClick={() => props.onFeedback("clarification")}>
              需澄清
            </button>
          </div>
          <textarea
            rows={3}
            value={props.feedbackComment}
            onChange={(event) => props.onFeedbackCommentChange(event.target.value)}
            placeholder="补充备注可用于后续分析。"
          />
        </section>
      ) : null}
    </div>
  );
}

function SqlTab(props: { response: ChatResponse | null }) {
  const response = props.response;
  return (
    <div className="inspector-body">
      <section className="subpanel">
        <h3>SQL</h3>
        <pre className="code-block">{response?.sql || "当前结果没有可展示的 SQL。"}</pre>
      </section>
      <section className="subpanel">
        <h3>查询计划</h3>
        <pre className="code-block">{response ? JSON.stringify(response.query_plan, null, 2) : "暂无查询计划。"}</pre>
      </section>
      <section className="subpanel">
        <h3>校验</h3>
        <div className="data-grid compact">
          <div className="key-row">
            <span>计划</span>
            <span>{response?.plan_validation.valid ? "通过" : "未通过"}</span>
          </div>
          <div className="key-row">
            <span>SQL</span>
            <span>{response?.sql_validation.valid ? "通过" : "未通过"}</span>
          </div>
        </div>
        {response?.plan_validation.warnings.length ? (
          <ListBlock title="计划告警" items={response.plan_validation.warnings} />
        ) : null}
        {response?.sql_validation.warnings.length ? (
          <ListBlock title="SQL 告警" items={response.sql_validation.warnings} />
        ) : null}
      </section>
    </div>
  );
}

function TraceTab(props: {
  queryLogs: RuntimeQueryLogRecord[];
  trace: TraceRecord | null;
  retrieval: RuntimeRetrievalLogRecord[];
  sqlAudit: RuntimeSqlAuditRecord | null;
  onSelectTrace: (traceId: string) => void;
}) {
  return (
    <div className="inspector-body">
      <section className="subpanel">
        <h3>最近查询</h3>
        <div className="query-log-list">
          {props.queryLogs.map((item) => (
            <button key={item.trace_id} className="query-log-item" onClick={() => props.onSelectTrace(item.trace_id)}>
              <span>{item.question || item.trace_id}</span>
              <span>{formatDateTime(item.created_at)}</span>
            </button>
          ))}
          {!props.queryLogs.length ? <div className="empty-line">当前会话还没有查询记录。</div> : null}
        </div>
      </section>

      <section className="subpanel">
        <h3>执行链路</h3>
        {props.trace ? (
          <>
            <div className="inline-note">Trace ID: {props.trace.trace_id}</div>
            {props.trace.warnings.length ? <ListBlock title="告警" items={props.trace.warnings} /> : null}
            <div className="trace-list">
              {props.trace.steps.map((step, index) => (
                <div key={`${step.name}-${index}`} className="trace-item">
                  <div className="trace-item-row">
                    <strong>{step.name}</strong>
                    <span>{step.status}</span>
                  </div>
                  {step.detail ? <div className="trace-item-detail">{step.detail}</div> : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="empty-line">选择一条查询后可以查看完整 Trace。</div>
        )}
      </section>

      <section className="subpanel">
        <h3>召回日志</h3>
        {props.retrieval.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>排名</th>
                  <th>来源</th>
                  <th>得分</th>
                  <th>特征</th>
                </tr>
              </thead>
              <tbody>
                {props.retrieval.map((item) => (
                  <tr key={item.retrieval_log_id}>
                    <td>{item.rank_position}</td>
                    <td>{`${item.source_type}:${item.source_id}`}</td>
                    <td>{item.score.toFixed(3)}</td>
                    <td>{item.matched_features.join(", ") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-line">当前没有召回日志。</div>
        )}
      </section>

      <section className="subpanel">
        <h3>SQL 审计</h3>
        <pre className="code-block">{props.sqlAudit?.sql_text || "当前没有 SQL 审计记录。"}</pre>
        {props.sqlAudit?.warnings.length ? <ListBlock title="告警" items={props.sqlAudit.warnings} /> : null}
        {props.sqlAudit?.errors.length ? <ListBlock title="错误" items={props.sqlAudit.errors} tone="error" /> : null}
      </section>
    </div>
  );
}

function StateTab(props: { snapshots: SessionSnapshotRecord[]; response: ChatResponse | null }) {
  return (
    <div className="inspector-body">
      <section className="subpanel">
        <h3>当前状态</h3>
        <pre className="code-block">
          {props.response?.next_session_state
            ? JSON.stringify(props.response.next_session_state, null, 2)
            : "当前页面只展示本次请求返回的最新 session state。"}
        </pre>
      </section>
      <section className="subpanel">
        <h3>快照</h3>
        <div className="snapshot-list">
          {props.snapshots.map((snapshot) => (
            <details key={snapshot.snapshot_id} className="snapshot-item">
              <summary>{formatDateTime(snapshot.created_at)}</summary>
              <pre className="code-block">{JSON.stringify(snapshot.state, null, 2)}</pre>
            </details>
          ))}
          {!props.snapshots.length ? <div className="empty-line">当前会话还没有快照。</div> : null}
        </div>
      </section>
    </div>
  );
}

function AdminView(props: {
  pending: boolean;
  error: string;
  runtimeStatus: RuntimeStatus | null;
  metadataOverview: MetadataOverview | null;
  adminUsers: UserContext[];
  adminRoles: RoleRecord[];
  adminLogs: RuntimeQueryLogRecord[];
  feedbackSummary: FeedbackSummary | null;
  evaluationSummary: EvaluationSummary | null;
  userIdInput: string;
  onUserIdInputChange: (value: string) => void;
  userForm: UserUpsertPayload;
  onUserFormChange: (next: UserUpsertPayload) => void;
  onSaveUser: () => void;
}) {
  return (
    <div className="admin-shell">
      {props.error ? <div className="error-box">{props.error}</div> : null}

      <section className="panel">
        <div className="panel-header">
          <h2>运行时状态</h2>
          {props.pending ? <span className="panel-meta">刷新中...</span> : null}
        </div>
        <div className="admin-grid">
          <KeyTable
            title="服务"
            rows={[
              ["Business DB", stringifyObject(props.runtimeStatus?.business_database)],
              ["Runtime DB", stringifyObject(props.runtimeStatus?.runtime_database)],
              ["LLM", stringifyObject(props.runtimeStatus?.llm)],
              ["Vector", stringifyObject(props.runtimeStatus?.vector_retrieval)],
              ["SQL AST", stringifyObject(props.runtimeStatus?.sql_ast)],
            ]}
          />
          <KeyTable
            title="语义层"
            rows={[
              ["Semantic version", props.metadataOverview?.semantic_version || "-"],
              ["Domains", String(props.metadataOverview?.semantic_domains.length || 0)],
              ["Views", String(props.metadataOverview?.semantic_views.length || 0)],
              ["Examples", String(props.metadataOverview?.example_count || 0)],
            ]}
          />
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>用户管理</h2>
        </div>
        <div className="admin-grid">
          <div className="subpanel">
            <div className="field">
              <label htmlFor="user-id">User ID</label>
              <input id="user-id" value={props.userIdInput} onChange={(event) => props.onUserIdInputChange(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="new-username">用户名</label>
              <input
                id="new-username"
                value={props.userForm.username}
                onChange={(event) => props.onUserFormChange({ ...props.userForm, username: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="new-password">密码</label>
              <input
                id="new-password"
                type="password"
                value={props.userForm.password || ""}
                onChange={(event) => props.onUserFormChange({ ...props.userForm, password: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="roles">角色</label>
              <input
                id="roles"
                value={props.userForm.roles.join(", ")}
                onChange={(event) =>
                  props.onUserFormChange({
                    ...props.userForm,
                    roles: event.target.value
                      .split(",")
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })
                }
              />
            </div>
            <div className="toggle-row">
              <label>
                <input
                  type="checkbox"
                  checked={props.userForm.can_view_sql}
                  onChange={(event) => props.onUserFormChange({ ...props.userForm, can_view_sql: event.target.checked })}
                />
                查看 SQL
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={props.userForm.can_execute_sql}
                  onChange={(event) => props.onUserFormChange({ ...props.userForm, can_execute_sql: event.target.checked })}
                />
                执行 SQL
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={props.userForm.is_active}
                  onChange={(event) => props.onUserFormChange({ ...props.userForm, is_active: event.target.checked })}
                />
                激活
              </label>
            </div>
            <button className="button primary" onClick={props.onSaveUser}>
              保存用户
            </button>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>用户</th>
                  <th>角色</th>
                  <th>查看 SQL</th>
                  <th>执行 SQL</th>
                </tr>
              </thead>
              <tbody>
                {props.adminUsers.map((user) => (
                  <tr key={user.user_id}>
                    <td>{user.username || user.user_id}</td>
                    <td>{user.roles.join(", ") || "-"}</td>
                    <td>{user.can_view_sql ? "yes" : "no"}</td>
                    <td>{user.can_execute_sql ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>角色与汇总</h2>
        </div>
        <div className="admin-grid">
          <KeyTable title="角色" rows={props.adminRoles.map((role) => [role.role_name, role.description || "-"])} />
          <KeyTable
            title="反馈"
            rows={(props.feedbackSummary?.by_type || []).map((item) => [item.feedback_type, String(item.count)])}
          />
          <KeyTable
            title="评测"
            rows={[
              ["Runs", String(props.evaluationSummary?.run_count || 0)],
              ["Cases", String(props.evaluationSummary?.case_count || 0)],
              ["Passed", String(props.evaluationSummary?.passed_count || 0)],
              ["Failed", String(props.evaluationSummary?.failed_count || 0)],
            ]}
          />
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>最近查询日志</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>问题</th>
                <th>主题域</th>
                <th>类型</th>
                <th>状态</th>
                <th>行数</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {props.adminLogs.map((item) => (
                <tr key={item.trace_id}>
                  <td>{item.question || item.trace_id}</td>
                  <td>{item.subject_domain || "-"}</td>
                  <td>{item.question_type || "-"}</td>
                  <td>{item.answer_status || "-"}</td>
                  <td>{item.row_count ?? "-"}</td>
                  <td>{formatDateTime(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function KeyTable(props: { title: string; rows: Array<[string, string]> }) {
  return (
    <div className="subpanel">
      <h3>{props.title}</h3>
      <div className="data-grid">
        {props.rows.length ? (
          props.rows.map(([key, value]) => (
            <div key={key} className="key-row">
              <span>{key}</span>
              <span>{value}</span>
            </div>
          ))
        ) : (
          <div className="empty-line">没有数据。</div>
        )}
      </div>
    </div>
  );
}

function ListBlock(props: { title: string; items: string[]; tone?: "error" }) {
  return (
    <div className={props.tone === "error" ? "list-block list-error" : "list-block"}>
      <div className="list-title">{props.title}</div>
      <ul>
        {props.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function formatDateTime(value: string) {
  try {
    return new Date(value).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

function stringifyCell(value: unknown) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function stringifyObject(value: unknown) {
  if (!value || typeof value !== "object") {
    return "-";
  }
  return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${key}: ${String(item)}`)
    .join(" | ");
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "发生了未预期的错误。";
}

export default App;
