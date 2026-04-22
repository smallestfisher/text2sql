import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  ChatMessage,
  ChatResponse,
  ChatSession,
  EvaluationReplayResult,
  EvaluationSummary,
  FeedbackSummary,
  MetadataOverview,
  RoleRecord,
  RuntimeQueryLogRecord,
  RuntimeSqlAuditRecord,
  RuntimeStatus,
  SemanticSummary,
  SessionState,
  TraceRecord,
  UserContext,
  UserUpsertPayload,
} from "./types";

const TOKEN_KEY = "text2sql.frontend.token";
const SESSION_KEY = "text2sql.frontend.session";
const PROMPTS = [
  "查询 2026 年 4 月 CELL 工厂计划投入量",
  "对比本月与上月各客户出货差异",
  "按产品线查看最近 8 周库存趋势",
  "继续上一个问题，细分到工厂维度",
];

type AuthMode = "login" | "bootstrap";
type InspectorTab = "result" | "sql" | "trace" | "state";
type ViewMode = "workspace" | "admin";

const emptyUserForm: UserUpsertPayload = {
  username: "",
  password: "",
  roles: ["viewer"],
  can_view_sql: true,
  can_execute_sql: true,
  can_download_results: true,
  is_active: true,
};

function App() {
  const [token, setToken] = useState<string | null>(() => window.localStorage.getItem(TOKEN_KEY));
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [authError, setAuthError] = useState("");
  const [authPending, setAuthPending] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserContext | null>(null);
  const [semanticSummary, setSemanticSummary] = useState<SemanticSummary | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("workspace");

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    () => window.localStorage.getItem(SESSION_KEY) || null,
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [latestResponse, setLatestResponse] = useState<ChatResponse | null>(null);
  const [latestTrace, setLatestTrace] = useState<TraceRecord | null>(null);
  const [latestSqlAudit, setLatestSqlAudit] = useState<RuntimeSqlAuditRecord | null>(null);
  const [latestQueryLogs, setLatestQueryLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [workspaceError, setWorkspaceError] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [question, setQuestion] = useState("");
  const [chatPending, setChatPending] = useState(false);
  const [activeTab, setActiveTab] = useState<InspectorTab>("result");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);

  const [adminPending, setAdminPending] = useState(false);
  const [adminError, setAdminError] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [metadataOverview, setMetadataOverview] = useState<MetadataOverview | null>(null);
  const [adminUsers, setAdminUsers] = useState<UserContext[]>([]);
  const [adminRoles, setAdminRoles] = useState<RoleRecord[]>([]);
  const [adminLogs, setAdminLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [adminFeedbackSummary, setAdminFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [adminEvalSummary, setAdminEvalSummary] = useState<EvaluationSummary | null>(null);
  const [adminSessions, setAdminSessions] = useState<ChatSession[]>([]);
  const [adminReplayPendingTraceId, setAdminReplayPendingTraceId] = useState<string | null>(null);
  const [adminReplayResult, setAdminReplayResult] = useState<EvaluationReplayResult | null>(null);
  const [userForm, setUserForm] = useState<UserUpsertPayload>(emptyUserForm);
  const [resetPasswordTarget, setResetPasswordTarget] = useState<UserContext | null>(null);
  const [resetPasswordValue, setResetPasswordValue] = useState("");
  const [deleteUserTarget, setDeleteUserTarget] = useState<UserContext | null>(null);

  useEffect(() => {
    void boot();
  }, []);

  useEffect(() => {
    const node = threadRef.current;
    if (!node) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [messages, pendingQuestion, chatPending]);

  useEffect(() => {
    if (token && viewMode === "admin" && (currentUser?.roles || []).includes("admin")) {
      void loadAdminData(token);
    }
  }, [token, viewMode, currentUser]);

  async function boot() {
    try {
      const summary = await api.semanticSummary();
      setSemanticSummary(summary);
    } catch {
      setSemanticSummary(null);
    }

    try {
      const status = await api.bootstrapStatus();
      setAuthMode(status.has_users ? "login" : "bootstrap");
    } catch (error) {
      setAuthError(errorMessage(error));
    }

    if (!token) {
      return;
    }

    try {
      await initializeWorkspace(token);
    } catch {
      clearAuth();
    }
  }

  async function initializeWorkspace(authToken: string) {
    const me = await api.me(authToken);
    setCurrentUser(me);
    await refreshSessions(authToken, selectedSessionId);
  }

  async function refreshSessions(authToken: string, preferredSessionId?: string | null) {
    const response = await api.listSessions(authToken);
    setSessions(response.sessions);
    const nextSessionId =
      preferredSessionId && response.sessions.some((item) => item.id === preferredSessionId)
        ? preferredSessionId
        : response.sessions[0]?.id || null;
    setSelectedSessionId(nextSessionId);
    if (nextSessionId) {
      await loadSession(authToken, nextSessionId);
    } else {
      clearSessionDetail();
    }
  }

  function clearSessionDetail() {
    setMessages([]);
    setSessionState(null);
    setLatestResponse(null);
    setLatestTrace(null);
    setLatestSqlAudit(null);
    setLatestQueryLogs([]);
    setWorkspaceError("");
    window.localStorage.removeItem(SESSION_KEY);
  }

  async function loadSession(authToken: string, sessionId: string) {
    window.localStorage.setItem(SESSION_KEY, sessionId);
    setSelectedSessionId(sessionId);
    setWorkspaceError("");

    const [history, statePayload, queryLogsPayload] = await Promise.all([
      api.getSessionHistory(authToken, sessionId),
      api.getSessionState(authToken, sessionId),
      api.listQueryLogs(authToken, sessionId),
    ]);

    setMessages(normalizeMessages(history.messages));
    setSessionState(statePayload.state || null);
    setLatestResponse(null);
    setLatestQueryLogs(queryLogsPayload.query_logs || []);

    const traceId = queryLogsPayload.query_logs?.[0]?.trace_id;
    if (!traceId) {
      setLatestTrace(null);
      setLatestSqlAudit(null);
      return;
    }

    const [trace, sqlAudit] = await Promise.all([
      api.getTrace(authToken, traceId),
      api.getTraceSqlAudit(authToken, traceId).catch(() => null),
    ]);
    setLatestTrace(trace);
    setLatestSqlAudit(sqlAudit);
  }

  async function handleAuth(username: string, password: string) {
    setAuthPending(true);
    setAuthError("");
    try {
      if (authMode === "bootstrap") {
        await api.bootstrapAdmin(username, password);
      }
      const loginResponse = await api.login(username, password);
      window.localStorage.setItem(TOKEN_KEY, loginResponse.access_token);
      setToken(loginResponse.access_token);
      setCurrentUser(loginResponse.user);
      setViewMode("workspace");
      await refreshSessions(loginResponse.access_token, selectedSessionId);
    } catch (error) {
      setAuthError(errorMessage(error));
    } finally {
      setAuthPending(false);
    }
  }

  function clearAuth() {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(SESSION_KEY);
    setToken(null);
    setCurrentUser(null);
    setViewMode("workspace");
    setSessions([]);
    setSelectedSessionId(null);
    setMessages([]);
    setSessionState(null);
    setLatestResponse(null);
    setLatestTrace(null);
    setLatestSqlAudit(null);
    setLatestQueryLogs([]);
    setWorkspaceError("");
    setPendingQuestion("");
    setQuestion("");
    setSidebarOpen(false);
    setInspectorOpen(false);
    setAdminError("");
  }

  async function createSession(title = "新对话") {
    if (!token) {
      return;
    }
    const response = await api.createSession(token, title);
    await refreshSessions(token, response.session.id);
    setSidebarOpen(false);
  }

  async function handleSend(nextQuestion?: string) {
    if (!token || chatPending) {
      return;
    }
    const trimmed = (nextQuestion ?? question).trim();
    if (!trimmed) {
      return;
    }

    const pendingSeed = Date.now();
    const pendingUserId = `pending-user-${pendingSeed}`;
    const pendingAssistantId = `pending-assistant-${pendingSeed}`;
    const pendingSessionId = selectedSessionId || "draft";

    setChatPending(true);
    setWorkspaceError("");
    setPendingQuestion(trimmed);
    setMessages((current) =>
      normalizeMessages([
        ...current,
        {
          id: pendingUserId,
          session_id: pendingSessionId,
          role: "user",
          content: trimmed,
          created_at: new Date().toISOString(),
        },
        {
          id: pendingAssistantId,
          session_id: pendingSessionId,
          role: "assistant",
          content: "正在处理查询，请稍候...",
          created_at: new Date(Date.now() + 1000).toISOString(),
        },
      ]),
    );
    if (nextQuestion === undefined) {
      setQuestion("");
    }

    try {
      let sessionId = selectedSessionId;
      if (!sessionId) {
        const sessionTitle = trimmed.length > 18 ? `${trimmed.slice(0, 18)}...` : trimmed;
        const created = await api.createSession(token, sessionTitle);
        sessionId = created.session.id;
        setSessions((current) => [created.session, ...current]);
        setSelectedSessionId(sessionId);
        window.localStorage.setItem(SESSION_KEY, sessionId);
      }

      const response = await api.chatQuery(token, trimmed, sessionId);
      setLatestResponse(response);
      setSessionState(response.next_session_state);
      await refreshSessions(token, sessionId);
      setLatestResponse(response);
      setActiveTab("result");
      setInspectorOpen(false);
    } catch (error) {
      setMessages((current) => current.filter((message) => message.id !== pendingUserId && message.id !== pendingAssistantId));
      setWorkspaceError(errorMessage(error));
    } finally {
      setPendingQuestion("");
      setChatPending(false);
    }
  }

  async function handleSelectSession(sessionId: string) {
    if (!token) {
      return;
    }
    try {
      await loadSession(token, sessionId);
      setSidebarOpen(false);
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function handleDeleteSession(sessionId: string) {
    if (!token) {
      return;
    }
    try {
      await api.deleteSession(token, sessionId);
      if (selectedSessionId === sessionId) {
        clearSessionDetail();
      }
      await refreshSessions(token, selectedSessionId === sessionId ? null : selectedSessionId);
      setSidebarOpen(false);
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function handleAdminReplayLog(log: RuntimeQueryLogRecord) {
    if (!token) {
      return;
    }
    setAdminError("");
    setAdminReplayPendingTraceId(log.trace_id);
    try {
      const replayResult = await api.adminReplayQueryLog(token, log.trace_id, {
        reuse_original_user: true,
        include_prior_context: true,
      });
      setAdminReplayResult(replayResult);
    } catch (error) {
      setAdminError(errorMessage(error));
    } finally {
      setAdminReplayPendingTraceId(null);
    }
  }

  async function loadAdminData(authToken: string) {
    setAdminPending(true);
    setAdminError("");
    try {
      const [status, overview, users, roles, logs, feedbacks, evalSummary, runtimeSessions] = await Promise.all([
        api.adminRuntimeStatus(authToken),
        api.adminMetadataOverview(authToken),
        api.adminUsers(authToken),
        api.adminRoles(authToken),
        api.adminQueryLogs(authToken),
        api.adminFeedbackSummary(authToken),
        api.adminEvaluationSummary(authToken),
        api.adminRuntimeSessions(authToken),
      ]);
      setRuntimeStatus(status);
      setMetadataOverview(overview);
      setAdminUsers(users);
      setAdminRoles(roles);
      setAdminLogs(logs.query_logs);
      setAdminFeedbackSummary(feedbacks);
      setAdminEvalSummary(evalSummary);
      setAdminSessions(runtimeSessions.sessions);
    } catch (error) {
      setAdminError(errorMessage(error));
    } finally {
      setAdminPending(false);
    }
  }

  async function handleAdminUserSave() {
    const username = userForm.username.trim();
    if (!token || !username) {
      return;
    }
    const userId = buildUserId(username);
    try {
      await api.adminUpsertUser(token, userId, {
        ...userForm,
        username,
        roles: userForm.roles.map((item) => item.trim()).filter(Boolean),
      });
      setUserForm(emptyUserForm);
      await loadAdminData(token);
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  async function handleAdminToggleUser(user: UserContext) {
    if (!token) {
      return;
    }
    try {
      await api.adminUpsertUser(token, user.user_id, {
        username: user.username || user.user_id,
        roles: user.roles,
        can_view_sql: user.can_view_sql,
        can_execute_sql: user.can_execute_sql,
        can_download_results: user.can_download_results,
        is_active: !user.is_active,
      });
      await loadAdminData(token);
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  async function handleAdminResetPassword(user: UserContext) {
    setResetPasswordTarget(user);
    setResetPasswordValue("");
  }

  async function submitAdminResetPassword() {
    if (!token || !resetPasswordTarget || !resetPasswordValue.trim()) {
      return;
    }
    try {
      await api.adminResetUserPassword(token, resetPasswordTarget.user_id, resetPasswordValue.trim());
      setResetPasswordTarget(null);
      setResetPasswordValue("");
      setAdminError("");
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  async function handleAdminDeleteUser(user: UserContext) {
    setDeleteUserTarget(user);
  }

  async function submitAdminDeleteUser() {
    if (!token || !deleteUserTarget) {
      return;
    }
    try {
      await api.adminDeleteUser(token, deleteUserTarget.user_id);
      setDeleteUserTarget(null);
      setAdminError("");
      await loadAdminData(token);
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  const selectedSession = sessions.find((item) => item.id === selectedSessionId) || null;
  const displayMessages = messages;
  const shouldShowWelcome = !displayMessages.length;

  const contextChips = buildContextChips(sessionState);
  const isAdmin = (currentUser?.roles || []).includes("admin");
  const showAdminCenter = isAdmin && viewMode === "admin";
  const showInspector = isAdmin && viewMode === "workspace";
  const semanticCards = [
    { label: "业务域", value: String(semanticSummary?.domains.length || 0) },
    { label: "语义视图", value: String(semanticSummary?.semantic_views.length || 0) },
    { label: "指标", value: String(semanticSummary?.metrics.length || 0) },
  ];

  if (!currentUser) {
    return (
      <AuthScreen
        authMode={authMode}
        authError={authError}
        authPending={authPending}
        semanticSummary={semanticSummary}
        onSubmit={handleAuth}
      />
    );
  }

  return (
    <div className="app-shell">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <header className="mobile-toolbar">
        <button className="toolbar-button" type="button" onClick={() => setSidebarOpen((current) => !current)}>
          会话
        </button>
        <div className="mobile-title">{showAdminCenter ? "管理台" : "Text2SQL"}</div>
        {showInspector ? (
          <button className="toolbar-button" type="button" onClick={() => setInspectorOpen((current) => !current)}>
            详情
          </button>
        ) : (
          <div className="toolbar-spacer" />
        )}
      </header>

      <div
        className={`workspace-shell${sidebarOpen ? " is-sidebar-open" : ""}${inspectorOpen ? " is-inspector-open" : ""}${!showInspector ? " is-no-inspector" : ""}`}
      >
        <aside className="sidebar">
          <div className="sidebar-panel brand-panel">
            <div className="brand-lockup">
              <div className="brand-mark">T</div>
              <div className="brand-copy-block">
                <div className="brand-name">Text2SQL</div>
                <div className="brand-meta">LobeHub 风格的问数工作台</div>
              </div>
            </div>

            {isAdmin ? (
              <div className="view-switch">
                <button
                  className={`view-switch-button${viewMode === "workspace" ? " is-active" : ""}`}
                  type="button"
                  onClick={() => setViewMode("workspace")}
                >
                  用户工作台
                </button>
                <button
                  className={`view-switch-button${viewMode === "admin" ? " is-active" : ""}`}
                  type="button"
                  onClick={() => setViewMode("admin")}
                >
                  管理中心
                </button>
              </div>
            ) : null}

            {!showAdminCenter ? (
              <>
                <button className="primary-button" type="button" onClick={() => void createSession()}>
                  新建会话
                </button>

                <div className="metric-grid">
                  {semanticCards.map((item) => (
                    <div className="metric-card" key={item.label}>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="metric-grid">
                <div className="metric-card">
                  <span>用户数</span>
                  <strong>{adminUsers.length}</strong>
                </div>
                <div className="metric-card">
                  <span>运行会话</span>
                  <strong>{adminSessions.length}</strong>
                </div>
                <div className="metric-card">
                  <span>查询日志</span>
                  <strong>{adminLogs.length}</strong>
                </div>
              </div>
            )}
          </div>

          {!showAdminCenter ? (
            <div className="sidebar-panel session-panel">
              <div className="panel-row">
                <div className="panel-title">最近会话</div>
                <div className="section-count">{sessions.length}</div>
              </div>

              <div className="session-list">
                {sessions.length ? (
                  sessions.map((session) => {
                    const tags = [
                      session.last_state?.subject_domain,
                      session.status === "archived" ? "archived" : null,
                    ].filter(Boolean);
                    return (
                      <div key={session.id} className={`session-item${session.id === selectedSessionId ? " is-active" : ""}`}>
                        <div className="session-item-top">
                          <button className="session-item-trigger" type="button" onClick={() => void handleSelectSession(session.id)}>
                            <div className="session-item-title">{session.title || "未命名会话"}</div>
                          </button>
                          <div className="session-item-time">{formatDate(session.updated_at)}</div>
                        </div>
                        <div className="session-item-bottom">
                          <span className="session-item-id">{session.id.slice(0, 8)}</span>
                          {tags.length ? (
                            <div className="mini-tags">
                              {tags.slice(0, 2).map((tag) => (
                                <span className="mini-tag" key={tag}>
                                  {tag}
                                </span>
                              ))}
                            </div>
                          ) : null}
                          <button
                            className="session-delete-button"
                            type="button"
                            onClick={() => void handleDeleteSession(session.id)}
                            aria-label="删除会话"
                          >
                            删除
                          </button>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="empty-card subtle-card">还没有会话，先发起一个问题。</div>
                )}
              </div>
            </div>
          ) : (
            <div className="sidebar-panel session-panel">
              <div className="panel-row">
                <div className="panel-title">最近运行会话</div>
                <div className="section-count">{adminSessions.length}</div>
              </div>

              <div className="session-list">
                {adminSessions.length ? (
                  adminSessions.map((session) => (
                    <div className="session-item is-static" key={session.id}>
                      <div className="session-item-top">
                        <div className="session-item-title">{session.title || "未命名会话"}</div>
                        <div className="session-item-time">{formatDate(session.updated_at)}</div>
                      </div>
                      <div className="session-item-bottom">
                        <span className="session-item-id">{session.id.slice(0, 8)}</span>
                        {session.user_id ? <span className="mini-tag">{session.user_id}</span> : null}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="empty-card subtle-card">当前没有运行时会话记录。</div>
                )}
              </div>
            </div>
          )}

          <div className="sidebar-panel user-panel">
            <div className="user-head">
              <div className="user-avatar">{(currentUser.username || currentUser.user_id).slice(0, 1).toUpperCase()}</div>
              <div>
                <div className="user-name">{currentUser.username || currentUser.user_id}</div>
                <div className="user-meta">{(currentUser.roles || []).join(", ") || "viewer"}</div>
              </div>
            </div>
            <button className="secondary-button full-width" type="button" onClick={clearAuth}>
              退出登录
            </button>
          </div>
        </aside>

        {showAdminCenter ? (
          <main className="main-column admin-main">
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
              replayPendingTraceId={adminReplayPendingTraceId}
              replayResult={adminReplayResult}
              userForm={userForm}
              onUserFormChange={setUserForm}
              onSaveUser={() => void handleAdminUserSave()}
              onToggleUser={(user) => void handleAdminToggleUser(user)}
              onResetPassword={(user) => void handleAdminResetPassword(user)}
              onDeleteUser={(user) => void handleAdminDeleteUser(user)}
              onReplayLog={(log) => void handleAdminReplayLog(log)}
              onRefresh={() => token && void loadAdminData(token)}
            />
          </main>
        ) : (
          <>
            <main className="main-column">
      <section className="hero-panel admin-hero-panel">
                <div className="hero-main">
                  <div className="hero-badge">会话工作台</div>
                  <div className="hero-title">{selectedSession?.title || "直接输入你的业务问题"}</div>
                  <div className="hero-subtitle">
                    {workspaceError
                      ? workspaceError
                      : selectedSession
                        ? `${sessionState?.topic || sessionState?.subject_domain || "上下文未建立"} · 更新于 ${formatDate(selectedSession.updated_at)}`
                        : "支持自然语言问数、上下文追问、SQL 审阅和 Trace 排查"}
                  </div>
                </div>

                <div className="hero-stats">
                  <StatPill label="当前域" value={sessionState?.subject_domain || "unknown"} />
                  <StatPill label="会话数" value={String(sessions.length)} />
                  <StatPill label="结果行数" value={String(latestResponse?.execution?.row_count ?? latestSqlAudit?.row_count ?? 0)} />
                </div>

                {contextChips.length ? (
                  <div className="context-strip">
                    {contextChips.map((chip) => (
                      <span className="context-chip" key={chip}>
                        {chip}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="context-strip">
                    <span className="context-chip is-muted">发送第一条问题后，这里会显示当前会话上下文</span>
                  </div>
                )}
              </section>

              <section className="conversation-panel">
                <div className="thread-scroll" ref={threadRef}>
                  {shouldShowWelcome ? (
                    <div className="welcome-shell">
                      <div className="welcome-card">
                        <div className="welcome-title">把业务问题直接说出来</div>
                        <div className="welcome-copy">
                          系统会按会话上下文自动补足语义，生成 Query Plan、SQL、执行结果和 Trace。
                        </div>
                      </div>

                      <div className="prompt-grid">
                        {PROMPTS.map((prompt) => (
                          <button key={prompt} className="prompt-card" type="button" onClick={() => void handleSend(prompt)}>
                            <span className="prompt-card-title">{prompt}</span>
                            <span className="prompt-card-copy">作为起始问题发送</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="thread-list">
                      {displayMessages.map((message) => (
                        <article key={message.id} className={`message${message.role === "user" ? " is-user" : ""}`}>
                          <div className="message-avatar">{message.role === "user" ? "U" : "AI"}</div>
                          <div className="message-body">
                            <div className="message-meta">
                              <span>{message.role === "user" ? "你" : "Text2SQL"}</span>
                              <span>{formatDate(message.created_at)}</span>
                            </div>
                            <div className="message-card">{message.content}</div>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              <section className="composer-shell">
                <form
                  className="composer-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleSend();
                  }}
                >
                  <div className="composer-dock">
                    <textarea
                      className="composer-input"
                      rows={1}
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          void handleSend();
                        }
                      }}
                      placeholder="输入业务问题，例如：查询本周北美客户计划与实际出货差异"
                    />

                    <div className="composer-footer">
                      <div className="composer-hints">
                        <span className="hint-chip">Enter 发送</span>
                        <span className="hint-chip">Shift + Enter 换行</span>
                        <span className="hint-chip">自动继承会话上下文</span>
                      </div>

                      <button className="send-button" type="submit" disabled={chatPending}>
                        {chatPending ? "处理中" : "发送"}
                      </button>
                    </div>
                  </div>
                </form>
              </section>
            </main>

            {showInspector ? (
              <aside className="inspector">
                <div className="inspector-head">
                  <div>
                    <div className="panel-title">会话详情</div>
                    <div className="panel-subtitle">
                      {sessionState?.subject_domain || latestResponse?.query_plan.subject_domain || "等待上下文"}
                    </div>
                  </div>

                  <div className="tab-rail">
                    {(["result", "sql", "trace", "state"] as InspectorTab[]).map((tab) => (
                      <button
                        key={tab}
                        className={`tab-button${activeTab === tab ? " is-active" : ""}`}
                        type="button"
                        onClick={() => setActiveTab(tab)}
                      >
                        {tab === "result" ? "结果" : tab === "sql" ? "SQL" : tab === "trace" ? "Trace" : "状态"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="inspector-body">
                  {activeTab === "result" && <ResultPanel latestResponse={latestResponse} workspaceError={workspaceError} token={token} latestTrace={latestTrace} currentUser={currentUser} />}
                  {activeTab === "sql" && (
                    <SqlPanel
                      canViewSql={currentUser.can_view_sql}
                      latestResponse={latestResponse}
                      latestSqlAudit={latestSqlAudit}
                      sessionState={sessionState}
                    />
                  )}
                  {activeTab === "trace" && <TracePanel latestTrace={latestTrace} latestQueryLogs={latestQueryLogs} />}
                  {activeTab === "state" && <StatePanel latestResponse={latestResponse} sessionState={sessionState} />}
                </div>
              </aside>
            ) : null}
          </>
        )}
      </div>

      {(sidebarOpen || (showInspector && inspectorOpen)) && (
        <button
          className="mobile-backdrop"
          type="button"
          aria-label="关闭浮层"
          onClick={() => {
            setSidebarOpen(false);
            setInspectorOpen(false);
          }}
        />
      )}

      {deleteUserTarget ? (
        <div className="modal-backdrop" onClick={() => setDeleteUserTarget(null)}>
          <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
            <div className="detail-title">删除用户</div>
            <div className="detail-copy">删除后将无法恢复。确认删除 {deleteUserTarget.username || deleteUserTarget.user_id} 吗？</div>
            <div className="admin-user-actions">
              <button className="secondary-button" type="button" onClick={() => setDeleteUserTarget(null)}>
                取消
              </button>
              <button className="secondary-button danger-button" type="button" onClick={() => void submitAdminDeleteUser()}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {resetPasswordTarget ? (
        <div className="modal-backdrop" onClick={() => setResetPasswordTarget(null)}>
          <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
            <div className="detail-title">重置密码</div>
            <div className="detail-copy">为 {resetPasswordTarget.username || resetPasswordTarget.user_id} 设置新密码。</div>
            <label className="field">
              <span>新密码</span>
              <input
                type="password"
                value={resetPasswordValue}
                onChange={(event) => setResetPasswordValue(event.target.value)}
                autoFocus
              />
            </label>
            <div className="admin-user-actions">
              <button className="secondary-button" type="button" onClick={() => setResetPasswordTarget(null)}>
                取消
              </button>
              <button className="primary-button" type="button" onClick={() => void submitAdminResetPassword()}>
                确认重置
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AuthScreen(props: {
  authMode: AuthMode;
  authError: string;
  authPending: boolean;
  semanticSummary: SemanticSummary | null;
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <div className="screen auth-screen">
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />

      <section className="auth-layout">
        <div className="auth-showcase">
          <div className="hero-badge">Text2SQL Workspace</div>
          <div className="auth-title">面向业务分析的自然语言查询入口</div>
          <div className="auth-copy">
            登录后可以直接提问，系统会自动规划语义、生成 SQL、执行查询，并把 Trace 与上下文状态保留在同一工作台里。
          </div>

          <div className="auth-metrics">
            <StatPill label="业务域" value={String(props.semanticSummary?.domains.length || 0)} />
            <StatPill label="语义视图" value={String(props.semanticSummary?.semantic_views.length || 0)} />
            <StatPill label="指标" value={String(props.semanticSummary?.metrics.length || 0)} />
          </div>
        </div>

        <section className="auth-card">
          <div className="auth-brand">
            <div className="brand-mark">T</div>
            <div>
              <div className="brand-name">Text2SQL</div>
              <div className="brand-meta">
                {props.authMode === "bootstrap" ? "初始化管理员账号" : "登录进入用户工作台"}
              </div>
            </div>
          </div>

          <form
            className="auth-form"
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              void props.onSubmit(username, password);
            }}
          >
            <label className="field">
              <span>用户名</span>
              <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
            </label>

            <label className="field">
              <span>密码</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={props.authMode === "bootstrap" ? "new-password" : "current-password"}
              />
            </label>

            <button className="send-button auth-submit" type="submit" disabled={props.authPending}>
              {props.authPending ? "处理中" : props.authMode === "bootstrap" ? "创建并登录" : "登录"}
            </button>
          </form>

          {props.authError ? <div className="form-error">{props.authError}</div> : null}
        </section>
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
  replayPendingTraceId: string | null;
  replayResult: EvaluationReplayResult | null;
  userForm: UserUpsertPayload;
  onUserFormChange: (value: UserUpsertPayload) => void;
  onSaveUser: () => void;
  onToggleUser: (user: UserContext) => void;
  onResetPassword: (user: UserContext) => void;
  onDeleteUser: (user: UserContext) => void;
  onReplayLog: (log: RuntimeQueryLogRecord) => void;
  onRefresh: () => void;
}) {
  const runtimeEntries = props.runtimeStatus
    ? [
        ["业务库", describeHealth(props.runtimeStatus.business_database)],
        ["运行时库", describeHealth(props.runtimeStatus.runtime_database)],
        ["LLM", describeHealth(props.runtimeStatus.llm)],
        ["向量检索", describeHealth(props.runtimeStatus.vector_retrieval)],
        ["语料索引", describeHealth(props.runtimeStatus.retrieval_corpus)],
        ["SQL AST", describeHealth(props.runtimeStatus.sql_ast)],
      ]
    : [];

  const replayExecution = props.replayResult?.response.execution;
  const replayAnswer = props.replayResult?.response.answer;

  return (
    <>
      <section className="hero-panel admin-hero-panel">
        <div className="admin-toolbar-top">
          <div className="hero-main">
            <div className="hero-badge">Admin Center</div>
            <div className="hero-title">系统监控与用户管理</div>
            <div className="hero-subtitle">
              这里接入后端现有的运行时状态、用户与角色、查询日志、反馈汇总和评测摘要。
            </div>
          </div>

          <div className="admin-actions">
            <button className="primary-button" type="button" onClick={props.onRefresh} disabled={props.pending}>
              {props.pending ? "刷新中" : "刷新数据"}
            </button>
          </div>
        </div>

        <div className="admin-toolbar-strip">
          <div className="toolbar-stats">
            <span className="toolbar-stat"><strong>{props.adminUsers.length}</strong><span>用户</span></span>
            <span className="toolbar-stat"><strong>{props.adminRoles.length}</strong><span>角色</span></span>
            <span className="toolbar-stat"><strong>{props.adminLogs.length}</strong><span>日志</span></span>
            <span className="toolbar-stat"><strong>{props.feedbackSummary?.total || 0}</strong><span>反馈</span></span>
          </div>

          <nav className="admin-anchor-nav" aria-label="管理页导航">
            <a href="#admin-monitor">监控</a>
            <a href="#admin-users">用户</a>
            <a href="#admin-logs">日志</a>
            <a href="#admin-quality">评测</a>
          </nav>
        </div>
      </section>

      {props.error ? <div className="detail-card accent-card">{props.error}</div> : null}

      <section className="admin-sections">
        <div className="admin-section-row" id="admin-monitor">
          <article className="detail-card admin-card">
            <div className="detail-title">运行状态</div>
            <div className="meta-stack">
              {runtimeEntries.length ? (
                runtimeEntries.map(([label, value]) => <MetaRow key={label} label={label} value={value} />)
              ) : (
                <div className="empty-card subtle-card">暂无运行状态数据。</div>
              )}
            </div>
          </article>

          <article className="detail-card admin-card">
            <div className="detail-title">元数据概览</div>
            <div className="meta-stack">
              <MetaRow label="语义版本" value={props.metadataOverview?.semantic_version || "-"} />
              <MetaRow label="业务域数" value={String(props.metadataOverview?.semantic_domains.length || 0)} />
              <MetaRow label="语义视图数" value={String(props.metadataOverview?.semantic_views.length || 0)} />
              <MetaRow label="示例数" value={String(props.metadataOverview?.example_count || 0)} />
              <MetaRow label="Trace 数" value={String(props.metadataOverview?.trace_count || 0)} />
            </div>
          </article>
        </div>

        <article className="detail-card admin-card admin-card-full" id="admin-users">
          <div className="detail-title">用户管理</div>
          <div className="detail-copy">系统会根据用户名自动生成内部 `user_id`，不需要手动输入。</div>

          <div className="admin-form-grid">
            <label className="field">
              <span>用户名</span>
              <input
                value={props.userForm.username}
                onChange={(event) => props.onUserFormChange({ ...props.userForm, username: event.target.value })}
              />
            </label>
            <label className="field">
              <span>密码</span>
              <input
                type="password"
                value={props.userForm.password || ""}
                onChange={(event) => props.onUserFormChange({ ...props.userForm, password: event.target.value })}
              />
            </label>
            <label className="field">
              <span>角色</span>
              <input
                value={props.userForm.roles.join(", ")}
                onChange={(event) =>
                  props.onUserFormChange({
                    ...props.userForm,
                    roles: event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                  })
                }
              />
            </label>
          </div>

          <div className="admin-toggle-row">
            <button className="primary-button" type="button" onClick={props.onSaveUser}>
              保存用户
            </button>
          </div>

          <div className="admin-list">
            {props.adminUsers.length ? (
              props.adminUsers.map((user) => (
                <div className="admin-list-item" key={user.user_id}>
                  <div>
                    <div className="admin-item-title">{user.username || user.user_id}</div>
                    <div className="admin-item-meta">
                      {user.user_id} · {user.is_active ? "已启用" : "已禁用"}
                    </div>
                  </div>
                  <div className="mini-tags">
                    {(user.roles || []).map((role) => (
                      <span className="mini-tag" key={role}>
                        {role}
                      </span>
                    ))}
                  </div>
                  <div className="admin-user-actions">
                    <button className="secondary-button" type="button" onClick={() => props.onToggleUser(user)}>
                      {user.is_active ? "禁用" : "启用"}
                    </button>
                    <button className="secondary-button" type="button" onClick={() => props.onResetPassword(user)}>
                      重置密码
                    </button>
                    <button className="secondary-button danger-button" type="button" onClick={() => props.onDeleteUser(user)}>
                      删除
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-card subtle-card">暂无用户。</div>
            )}
          </div>
        </article>

        <div className="admin-section-row admin-section-row-secondary" id="admin-quality">
          <article className="detail-card admin-card">
            <div className="detail-title">角色</div>
            <div className="admin-list">
              {props.adminRoles.length ? (
                props.adminRoles.map((role) => (
                  <div className="admin-list-item" key={role.role_name}>
                    <div>
                      <div className="admin-item-title">{role.role_name}</div>
                      <div className="admin-item-meta">{role.description || "无描述"}</div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="empty-card subtle-card">暂无角色。</div>
              )}
            </div>
          </article>

          <article className="detail-card admin-card">
            <div className="detail-title">反馈汇总</div>
            <div className="meta-stack">
              <MetaRow label="反馈总数" value={String(props.feedbackSummary?.total || 0)} />
              {(props.feedbackSummary?.by_type || []).map((item) => (
                <MetaRow key={item.feedback_type} label={item.feedback_type} value={String(item.count)} />
              ))}
            </div>
          </article>

          <article className="detail-card admin-card">
            <div className="detail-title">评测汇总</div>
            <div className="meta-stack">
              <MetaRow label="Run 数" value={String(props.evaluationSummary?.run_count || 0)} />
              <MetaRow label="Case 数" value={String(props.evaluationSummary?.case_count || 0)} />
              <MetaRow label="通过" value={String(props.evaluationSummary?.passed_count || 0)} />
              <MetaRow label="失败" value={String(props.evaluationSummary?.failed_count || 0)} />
            </div>
          </article>
        </div>

        <article className="detail-card admin-card admin-card-full" id="admin-logs">
          <div className="panel-row">
            <div className="detail-title">最近查询日志</div>
            <div className="detail-copy">可直接按原问题和历史上下文复跑，便于复现失败或对比最新结果。</div>
          </div>
          <div className="admin-list">
            {props.adminLogs.length ? (
              props.adminLogs.slice(0, 10).map((log) => (
                <div className="admin-list-item" key={log.trace_id}>
                  <div>
                    <div className="admin-item-title">{log.question || "未记录问题"}</div>
                    <div className="admin-item-meta">
                      {log.subject_domain || "unknown"} · {formatDate(log.created_at)} · {log.trace_id}
                    </div>
                  </div>
                  <div className="mini-tags">
                    <span className="mini-tag">{log.answer_status || "unknown"}</span>
                    <span className="mini-tag">{String(log.row_count ?? 0)} rows</span>
                  </div>
                  <div className="admin-user-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => props.onReplayLog(log)}
                      disabled={props.replayPendingTraceId === log.trace_id}
                    >
                      {props.replayPendingTraceId === log.trace_id ? "复跑中" : "复跑"}
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-card subtle-card">暂无查询日志。</div>
            )}
          </div>

          {props.replayResult ? (
            <div className="admin-replay-panel">
              <div className="panel-row">
                <div>
                  <div className="detail-title">复跑结果</div>
                  <div className="admin-item-meta">
                    {props.replayResult.question}
                    {props.replayResult.replay_user?.username || props.replayResult.replay_user?.user_id
                      ? ` · 用户 ${props.replayResult.replay_user?.username || props.replayResult.replay_user?.user_id}`
                      : ""}
                  </div>
                </div>
                <div className="mini-tags">
                  <span className="mini-tag">{replayAnswer?.status || "unknown"}</span>
                  <span className="mini-tag">{String(replayExecution?.row_count ?? 0)} rows</span>
                </div>
              </div>

              {props.replayResult.session_questions.length ? (
                <div className="detail-copy">
                  上下文问题：{props.replayResult.session_questions.join(" / ")}
                </div>
              ) : null}

              <div className="detail-card accent-card admin-replay-answer">
                <div className="detail-title">回答摘要</div>
                <div className="detail-copy">{replayAnswer?.summary || "本次复跑没有生成回答摘要。"}</div>
                {replayAnswer?.detail ? <div className="detail-copy">{replayAnswer.detail}</div> : null}
              </div>

              <div className="stats-row">
                <div className="compact-stat">
                  <span>规划校验</span>
                  <strong>{props.replayResult.response.plan_validation.valid ? "通过" : "失败"}</strong>
                </div>
                <div className="compact-stat">
                  <span>SQL 校验</span>
                  <strong>{props.replayResult.response.sql_validation.valid ? "通过" : "失败"}</strong>
                </div>
                <div className="compact-stat">
                  <span>执行状态</span>
                  <strong>{replayExecution?.status || "unknown"}</strong>
                </div>
              </div>

              {props.replayResult.response.sql ? (
                <div className="detail-card">
                  <div className="detail-title">SQL</div>
                  <pre className="code-block">{props.replayResult.response.sql}</pre>
                </div>
              ) : null}

              {replayExecution?.rows?.length ? (
                <div className="result-table-wrap">
                  <table className="result-table">
                    <thead>
                      <tr>
                        {replayExecution.columns.map((column) => (
                          <th key={column}>{column}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {replayExecution.rows.slice(0, 20).map((row, index) => (
                        <tr key={`${index}-${replayExecution.columns.join("-")}`}>
                          {replayExecution.columns.map((column) => (
                            <td key={column}>{String(row[column] ?? "")}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty-card subtle-card">当前复跑没有结果行可展示。</div>
              )}
            </div>
          ) : null}
        </article>
      </section>
    </>
  );
}


function ResultPanel(props: { latestResponse: ChatResponse | null; workspaceError: string; token: string | null; latestTrace: TraceRecord | null; currentUser: UserContext | null }) {
  if (props.workspaceError) {
    return (
      <section className="tab-panel">
        <div className="detail-card accent-card">
          <div className="detail-title">请求失败</div>
          <div className="detail-copy">{props.workspaceError}</div>
        </div>
      </section>
    );
  }

  if (!props.latestResponse) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">发送问题后，这里会展示回答、执行状态和检索摘要。</div>
      </section>
    );
  }

  const answer = props.latestResponse.answer;
  const execution = props.latestResponse.execution;
  const retrieval = props.latestResponse.retrieval;

  return (
    <section className="tab-panel">
      <div className="detail-card accent-card">
        <div className="panel-row">
          <div className="detail-title">回答</div>
          {execution?.rows?.length && props.token && props.latestTrace && props.currentUser?.can_download_results ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                void api.downloadTraceResult(props.token!, props.latestTrace!.trace_id).then((csv) => {
                  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
                  const url = window.URL.createObjectURL(blob);
                  const anchor = document.createElement("a");
                  anchor.href = url;
                  anchor.download = `trace-${props.latestTrace!.trace_id}.csv`;
                  anchor.click();
                  window.URL.revokeObjectURL(url);
                }).catch(() => undefined);
              }}
            >
              下载结果
            </button>
          ) : null}
        </div>
        <div className="detail-copy">{answer?.summary || "本次请求没有生成回答摘要。"}</div>
        {answer?.detail ? <div className="detail-copy">{answer.detail}</div> : null}
        {answer?.follow_up_hint ? <div className="detail-copy">下一步：{answer.follow_up_hint}</div> : null}
      </div>

      <div className="stats-row">
        <div className="compact-stat">
          <span>状态</span>
          <strong>{execution?.status || "unknown"}</strong>
        </div>
        <div className="compact-stat">
          <span>返回行数</span>
          <strong>{String(execution?.row_count ?? 0)}</strong>
        </div>
        <div className="compact-stat">
          <span>耗时</span>
          <strong>{execution?.elapsed_ms ? `${execution.elapsed_ms} ms` : "-"}</strong>
        </div>
      </div>

      {execution?.rows?.length ? (
        <div className="result-table-wrap">
          <table className="result-table">
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
                    <td key={column}>{String(row[column] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-card subtle-card">当前没有结果行可展示。</div>
      )}

      <div className="detail-card">
        <div className="detail-title">检索摘要</div>
        <div className="meta-stack">
          <MetaRow label="规划校验" value={props.latestResponse.plan_validation.valid ? "通过" : "未通过"} />
          <MetaRow label="业务域" value={(retrieval?.domains || []).join(", ") || "-"} />
          <MetaRow label="语义视图" value={(retrieval?.semantic_views || []).join(", ") || "-"} />
          <MetaRow label="指标" value={(retrieval?.metrics || []).join(", ") || "-"} />
        </div>
      </div>
    </section>
  );
}

function SqlPanel(props: {
  canViewSql: boolean;
  latestResponse: ChatResponse | null;
  latestSqlAudit: RuntimeSqlAuditRecord | null;
  sessionState: SessionState | null;
}) {
  if (!props.canViewSql) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">当前账号没有 SQL 查看权限。</div>
      </section>
    );
  }

  const sql = props.latestResponse?.sql || props.latestSqlAudit?.sql_text || "";
  const queryPlan = props.latestResponse?.query_plan || props.sessionState?.last_query_plan;

  if (!sql && !queryPlan) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">这里会展示 SQL 草案、校验信息和 Query Plan。</div>
      </section>
    );
  }

  return (
    <section className="tab-panel">
      <div className="detail-card">
        <div className="detail-title">SQL</div>
        <pre className="code-block">{sql || "未生成 SQL"}</pre>
      </div>

      <div className="stats-row">
        <div className="compact-stat">
          <span>合法性</span>
          <strong>
            {props.latestResponse?.sql_validation.valid ? "通过" : props.latestSqlAudit?.sql_valid ? "通过" : "待确认"}
          </strong>
        </div>
        <div className="compact-stat">
          <span>已执行</span>
          <strong>{String(props.latestResponse?.execution?.executed ?? props.latestSqlAudit?.executed ?? false)}</strong>
        </div>
        <div className="compact-stat">
          <span>警告数</span>
          <strong>{String((props.latestResponse?.sql_validation.warnings || props.latestSqlAudit?.warnings || []).length)}</strong>
        </div>
      </div>

      <div className="detail-card">
        <div className="detail-title">Query Plan</div>
        <pre className="json-block">{JSON.stringify(queryPlan || {}, null, 2)}</pre>
      </div>
    </section>
  );
}

function TracePanel(props: {
  latestTrace: TraceRecord | null;
  latestQueryLogs: RuntimeQueryLogRecord[];
}) {
  if (!props.latestTrace && !props.latestQueryLogs.length) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">发送问题后，这里会展示 Trace 步骤和最近的查询记录。</div>
      </section>
    );
  }

  return (
    <section className="tab-panel">
      {props.latestQueryLogs.length ? (
        <div className="detail-card">
          <div className="detail-title">最近查询</div>
          <div className="meta-stack">
            {props.latestQueryLogs.slice(0, 5).map((log) => (
              <MetaRow key={log.trace_id} label={log.question || "未记录问题"} value={log.answer_status || "unknown"} />
            ))}
          </div>
        </div>
      ) : null}

      {props.latestTrace?.steps?.length ? (
        <div className="trace-list">
          {props.latestTrace.steps.map((step, index) => (
            <div className="trace-step" key={`${step.name}-${index}`}>
              <div className="trace-step-head">
                <div className="trace-step-name">{step.name}</div>
                <div className="trace-step-status">{step.status}</div>
              </div>
              {step.detail ? <div className="trace-step-copy">{step.detail}</div> : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-card subtle-card">当前没有 Trace 步骤。</div>
      )}
    </section>
  );
}

function StatePanel(props: { latestResponse: ChatResponse | null; sessionState: SessionState | null }) {
  const payload = props.latestResponse?.next_session_state || props.sessionState;
  if (!payload) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">当前没有会话状态。</div>
      </section>
    );
  }

  return (
    <section className="tab-panel">
      <div className="detail-card">
        <div className="detail-title">会话状态</div>
        <pre className="json-block">{JSON.stringify(payload, null, 2)}</pre>
      </div>
    </section>
  );
}

function StatPill(props: { label: string; value: string }) {
  return (
    <div className="stat-pill">
      <span>{props.label}</span>
      <strong>{props.value}</strong>
    </div>
  );
}

function MetaRow(props: { label: string; value: string }) {
  return (
    <div className="meta-row">
      <span>{props.label}</span>
      <strong>{props.value}</strong>
    </div>
  );
}

function normalizeMessages(items: ChatMessage[]) {
  const sorted = [...items].sort((left, right) => {
    const leftTime = parseAppDate(left.created_at)?.getTime() ?? Number.NaN;
    const rightTime = parseAppDate(right.created_at)?.getTime() ?? Number.NaN;
    if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
      return leftTime - rightTime;
    }
    if (left.trace_id && left.trace_id === right.trace_id && left.role !== right.role) {
      return left.role === "user" ? -1 : 1;
    }
    return left.id.localeCompare(right.id);
  });

  const normalized: ChatMessage[] = [];
  for (const message of sorted) {
    const previous = normalized[normalized.length - 1];
    if (
      previous &&
      previous.trace_id &&
      previous.trace_id === message.trace_id &&
      previous.role === "assistant" &&
      message.role === "user"
    ) {
      normalized[normalized.length - 1] = message;
      normalized.push(previous);
      continue;
    }
    normalized.push(message);
  }
  return normalized;
}

function buildContextChips(state: SessionState | null) {
  if (!state) {
    return [];
  }

  const chips = [
    ...(state.metrics || []).slice(0, 2).map((item) => `指标 · ${item}`),
    ...(state.dimensions || []).slice(0, 2).map((item) => `维度 · ${item}`),
    ...(state.entities || []).slice(0, 2).map((item) => `实体 · ${item}`),
  ];

  if (state.time_context?.grain && state.time_context.grain !== "unknown") {
    chips.push(`时间 · ${state.time_context.grain}`);
  }

  return Array.from(new Set(chips)).slice(0, 6);
}

function describeHealth(value: Record<string, unknown> | null | undefined) {
  if (!value) {
    return "-";
  }
  if (typeof value.ok === "boolean") {
    return value.ok ? "正常" : "异常";
  }
  if (typeof value.status === "string") {
    return value.status;
  }
  if (typeof value.connected === "boolean") {
    return value.connected ? "已连接" : "未连接";
  }
  if (typeof value.available === "boolean") {
    return value.available ? "可用" : "不可用";
  }
  if (typeof value.healthy === "boolean") {
    return value.healthy ? "健康" : "异常";
  }
  return "已返回";
}

function parseAppDate(value?: string | null) {
  if (!value) {
    return null;
  }
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized);
  const candidate = hasTimezone ? normalized : `${normalized}Z`;
  const date = new Date(candidate);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value?: string | null) {
  if (!value) {
    return "刚刚";
  }
  const date = parseAppDate(value);
  if (!date) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败";
}

function buildUserId(username: string) {
  const normalized = username
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return normalized ? `user-${normalized}` : `user-${Date.now()}`;
}

export default App;
