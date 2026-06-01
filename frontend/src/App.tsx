import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  ChatMessage,
  ChatResponse,
  ChatSession,
  EvaluationReplayResult,
  EvaluationSummary,
  FeedbackSummary,
  AdminMetadataReloadResponse,
  AdminVectorPrewarmResponse,
  ContextSummary,
  MetadataOverview,
  RoleRecord,
  RuntimeQueryLogRecord,
  RuntimeSqlAuditRecord,
  RuntimeStatus,
  SessionState,
  SessionTraceWorkspaceRecord,
  TraceRecord,
  ProgressEvent,
  UserContext,
  UserUpsertPayload,
} from "./types";

const TOKEN_KEY = "text2sql.frontend.token";
const SESSION_KEY = "text2sql.frontend.session";
const PROMPTS = [
  "2026年2月Array工厂审批版投入物量与实际物量Gap和达成率",
  "oms库存，近6个月库存变化趋势",
  "最新P版，2026年5月Oxide产品数量是多少",
  "继续上一个问题，细分到工厂维度",
];
const PROGRESS_BASE_STAGES = [
  "accepted",
  "load_session",
  "question_analysis",
  "retrieval",
  "sql_generation",
  "sql_validation",
  "execution",
  "answer_building",
] as const;
const PROGRESS_STAGE_META: Record<string, { label: string; note: string; icon: string }> = {
  accepted: {
    label: "请求已接收",
    note: "问题已进入执行队列，系统正在准备本次查询链路。",
    icon: "○",
  },
  load_session: {
    label: "加载会话",
    note: "恢复当前会话状态，补足上一轮上下文和筛选条件。",
    icon: "↺",
  },
  question_analysis: {
    label: "问题上下文",
    note: "识别追问关系，改写完整问题，并生成本轮上下文摘要。",
    icon: "◎",
  },
  retrieval: {
    label: "检索上下文",
    note: "按完整问题召回表结构、业务知识和真实样例。",
    icon: "⌕",
  },
  sql_generation: {
    label: "生成 SQL",
    note: "结合可用表、检索证据和安全约束生成候选 SQL。",
    icon: "Σ",
  },
  sql_validation: {
    label: "校验 SQL",
    note: "检查只读、安全、字段范围和必要过滤条件。",
    icon: "✓",
  },
  execution: {
    label: "执行查询",
    note: "执行 SQL 并拉取结果集，准备后续回答内容。",
    icon: "▶",
  },
  answer_building: {
    label: "组织回答",
    note: "整合执行结果、状态和 Trace，组织最终回答。",
    icon: "✎",
  },
  completed: {
    label: "已完成",
    note: "本次请求已结束，结果已回写到当前会话。",
    icon: "✓",
  },
  failed: {
    label: "失败",
    note: "执行链路已中断，需要根据错误信息继续排查。",
    icon: "!",
  },
};
type ProgressStepTone = "active" | "completed" | "skipped" | "failed" | "pending";

type PendingProgressStep = {
  stage: string;
  label: string;
  note: string;
  icon: string;
  tone: ProgressStepTone;
  badge: string;
};

type AuthMode = "login" | "bootstrap";
type InspectorTab = "result" | "sql" | "trace" | "state";
type ViewMode = "workspace" | "admin";
const CHITCHAT_ROLE = "chitchat";

const emptyUserForm: UserUpsertPayload = {
  username: "",
  password: "",
  roles: ["viewer"],
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
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    () => window.localStorage.getItem(SESSION_KEY) || null,
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [latestResponse, setLatestResponse] = useState<ChatResponse | null>(null);
  const [latestTrace, setLatestTrace] = useState<TraceRecord | null>(null);
  const [latestSqlAudit, setLatestSqlAudit] = useState<RuntimeSqlAuditRecord | null>(null);
  const [latestQueryLogs, setLatestQueryLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [traceArtifacts, setTraceArtifacts] = useState<SessionTraceWorkspaceRecord[]>([]);
  const [activeTraceId, setActiveTraceId] = useState<string | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [pendingProgress, setPendingProgress] = useState<ProgressEvent[]>([]);
  const [pendingTraceId, setPendingTraceId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [chatPending, setChatPending] = useState(false);
  const [activeTab, setActiveTab] = useState<InspectorTab>("result");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorAttention, setInspectorAttention] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const inspectorRef = useRef<HTMLElement | null>(null);

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
  const [adminIndexActionPending, setAdminIndexActionPending] = useState<"" | "reload" | "prewarm" | "reload_prewarm">("");
  const [adminIndexActionMessage, setAdminIndexActionMessage] = useState("");
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
  }, [messages, pendingQuestion, pendingProgress, chatPending]);

  useEffect(() => {
    if (token && viewMode === "admin" && (currentUser?.roles || []).includes("admin")) {
      void loadAdminData(token);
    }
  }, [token, viewMode, currentUser]);

  useEffect(() => {
    if (!inspectorAttention) {
      return;
    }
    const timer = window.setTimeout(() => setInspectorAttention(false), 900);
    return () => window.clearTimeout(timer);
  }, [inspectorAttention]);

  async function boot() {
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
    setTraceArtifacts([]);
    setActiveTraceId(null);
    setWorkspaceError("");
    setPendingQuestion("");
    setPendingProgress([]);
    setPendingTraceId(null);
    window.localStorage.removeItem(SESSION_KEY);
  }

  function applyWorkspacePayload(workspace: { messages: ChatMessage[]; state?: SessionState | null; latest_response?: ChatResponse | null; latest_query_logs: RuntimeQueryLogRecord[]; latest_trace?: TraceRecord | null; latest_sql_audit?: RuntimeSqlAuditRecord | null; trace_artifacts: SessionTraceWorkspaceRecord[]; }) {
    setMessages(normalizeMessages(workspace.messages || []));
    setSessionState(workspace.state || null);
    setLatestResponse(workspace.latest_response || null);
    setLatestQueryLogs(workspace.latest_query_logs || []);
    setLatestTrace(workspace.latest_trace || null);
    setLatestSqlAudit(workspace.latest_sql_audit || null);
    setTraceArtifacts(workspace.trace_artifacts || []);
    setActiveTraceId(workspace.latest_trace?.trace_id || workspace.latest_response?.trace?.trace_id || null);
  }

  function primeImmediateTraceArtifact(response: ChatResponse) {
    const traceId = response.trace?.trace_id;
    if (!traceId) {
      return;
    }
    setTraceArtifacts((current) =>
      upsertTraceArtifact(current, {
        trace_id: traceId,
        response,
        trace: response.trace,
        sql_audit: null,
        query_log: null,
      }),
    );
    setActiveTraceId(traceId);
  }

  async function loadSession(authToken: string, sessionId: string) {
    window.localStorage.setItem(SESSION_KEY, sessionId);
    setSelectedSessionId(sessionId);
    setWorkspaceError("");
    const workspace = await api.getSessionWorkspace(authToken, sessionId);
    applyWorkspacePayload(workspace);
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
    setTraceArtifacts([]);
    setActiveTraceId(null);
    setWorkspaceError("");
    setPendingQuestion("");
    setPendingProgress([]);
    setPendingTraceId(null);
    setQuestion("");
    setSidebarOpen(false);
    setInspectorOpen(false);
    setAdminError("");
  }

  async function createSession() {
    if (!token || chatPending) {
      return;
    }
    const response = await api.createSession(token);
    await refreshSessions(token, response.session.id);
    setSidebarOpen(false);
  }

  function primeSessionTitle(sessionId: string, title: string) {
    const normalized = title.trim();
    if (!normalized) {
      return;
    }
    setSessions((current) => {
      const target = current.find((session) => session.id === sessionId);
      if (!target || target.title?.trim()) {
        return current;
      }
      const updated = {
        ...target,
        title: normalized,
        updated_at: new Date().toISOString(),
      };
      return [updated, ...current.filter((session) => session.id !== sessionId)];
    });
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
    const optimisticSessionTitle = buildSessionTitle(trimmed);

    setChatPending(true);
    setWorkspaceError("");
    setPendingQuestion(trimmed);
    setPendingProgress([]);
    setPendingTraceId(null);
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
          trace_id: `pending-${pendingSeed}`,
        },
      ]),
    );
    if (selectedSessionId) {
      primeSessionTitle(selectedSessionId, optimisticSessionTitle);
    }
    if (nextQuestion === undefined) {
      setQuestion("");
    }

    let response: ChatResponse | null = null;
    let sessionId = selectedSessionId ?? undefined;
    let streamedTraceId: string | null = null;
    try {
      if (!sessionId) {
        const created = await api.createSession(token, optimisticSessionTitle);
        sessionId = created.session.id;
        setSessions((current) => [created.session, ...current]);
        setSelectedSessionId(sessionId);
        window.localStorage.setItem(SESSION_KEY, sessionId);
      }

      let streamStarted = false;
      let streamFailure: string | null = null;
      await api.chatQueryStream(token, trimmed, sessionId, (event) => {
        streamStarted = true;
        streamedTraceId = event.trace_id;
        setPendingProgress((current) => [...current, event]);
        setPendingTraceId(event.trace_id);
        if (event.type === "completed" && event.metadata?.response) {
          response = event.metadata.response as ChatResponse;
          setLatestResponse(response);
          setSessionState(response.next_session_state);
          primeImmediateTraceArtifact(response);
        }
        if (event.type === "failed") {
          streamFailure = event.detail || "请求失败";
        }
      });
      if (!response && streamFailure) {
        throw new Error(streamFailure);
      }
      if (!response && !streamStarted) {
        response = await api.chatQuery(token, trimmed, sessionId);
        setLatestResponse(response);
        setSessionState(response.next_session_state);
        primeImmediateTraceArtifact(response);
      }
      if (!response) {
        throw new Error("流式响应未返回最终结果");
      }
      await refreshSessions(token, sessionId);
      setLatestResponse(response);
      setActiveTab("result");
      setInspectorOpen(false);
    } catch (error) {
      if (response && sessionId) {
        const resolvedSessionId = sessionId;
        setMessages((current) =>
          resolvePendingMessages(current, {
            pendingUserId,
            pendingAssistantId,
            sessionId: resolvedSessionId,
            assistantContent: response?.answer?.summary || "本次请求已完成，请查看详情面板。",
            assistantTraceId: response?.trace?.trace_id || streamedTraceId || undefined,
          }),
        );
        setLatestResponse(response);
        setSessionState(response.next_session_state);
        primeImmediateTraceArtifact(response);
        setActiveTab("result");
        setWorkspaceError(`本次请求已完成，但会话刷新失败：${errorMessage(error)}`);
      } else {
        setMessages((current) => current.filter((message) => message.id !== pendingUserId && message.id !== pendingAssistantId));
        setWorkspaceError(errorMessage(error));
      }
    } finally {
      setPendingQuestion("");
      setPendingProgress([]);
      setPendingTraceId(null);
      setChatPending(false);
    }
  }

  async function handleSelectSession(sessionId: string) {
    if (!token || chatPending) {
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
    if (!token || chatPending) {
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

  function focusInspector(traceId: string) {
    setActiveTraceId(traceId);
    setActiveTab("result");
    setInspectorOpen(true);
    setInspectorAttention(false);
    window.requestAnimationFrame(() => {
      inspectorRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
      setInspectorAttention(true);
    });
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

  function formatIndexActionMessage(
    mode: "reload" | "prewarm" | "reload_prewarm",
    reloadResult?: AdminMetadataReloadResponse | null,
    prewarmResult?: AdminVectorPrewarmResponse | null,
  ) {
    if (mode === "reload") {
      return reloadResult?.reloaded
        ? `元数据已重载${reloadResult.semantic_version ? `，语义版本 ${reloadResult.semantic_version}` : ""}。`
        : "元数据重载请求已完成。";
    }
    if (mode === "prewarm") {
      return prewarmResult?.accepted
        ? `向量索引预热已触发，当前${prewarmResult.pending_rebuild ? "仍有待重建任务" : "状态已同步"}。`
        : "向量索引预热请求已完成。";
    }
    return [
      reloadResult?.reloaded
        ? `元数据已重载${reloadResult.semantic_version ? `，语义版本 ${reloadResult.semantic_version}` : ""}`
        : "元数据重载已完成",
      prewarmResult?.accepted
        ? `向量索引已预热${prewarmResult.pending_rebuild ? "，仍有待重建任务" : ""}`
        : "向量索引预热请求已完成",
    ].join("；");
  }

  async function handleAdminIndexAction(mode: "reload" | "prewarm" | "reload_prewarm") {
    if (!token) {
      return;
    }
    setAdminError("");
    setAdminIndexActionMessage("");
    setAdminIndexActionPending(mode);
    try {
      let reloadResult: AdminMetadataReloadResponse | null = null;
      let prewarmResult: AdminVectorPrewarmResponse | null = null;
      if (mode === "reload" || mode === "reload_prewarm") {
        reloadResult = await api.adminReloadMetadata(token);
      }
      if (mode === "prewarm" || mode === "reload_prewarm") {
        prewarmResult = await api.adminPrewarmVectorIndex(token);
      }
      await loadAdminData(token);
      setAdminIndexActionMessage(formatIndexActionMessage(mode, reloadResult, prewarmResult));
    } catch (error) {
      setAdminError(errorMessage(error));
    } finally {
      setAdminIndexActionPending("");
    }
  }

  async function handleAdminUserSave() {
    const username = userForm.username.trim();
    if (!token || !username) {
      return;
    }
    const userId = buildUserId(username);
    try {
      const updatedUser = await api.adminUpsertUser(token, userId, {
        ...userForm,
        username,
        roles: userForm.roles.map((item) => item.trim()).filter(Boolean),
      });
      setCurrentUser((current) => (current?.user_id === updatedUser.user_id ? updatedUser : current));
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
      const updatedUser = await api.adminUpsertUser(token, user.user_id, {
        username: user.username || user.user_id,
        roles: user.roles,
        is_active: !user.is_active,
      });
      setCurrentUser((current) => (current?.user_id === updatedUser.user_id ? updatedUser : current));
      await loadAdminData(token);
    } catch (error) {
      setAdminError(errorMessage(error));
    }
  }

  async function handleAdminToggleChitchat(user: UserContext) {
    if (!token) {
      return;
    }
    const nextRoles = toggleRole(user.roles, CHITCHAT_ROLE, !user.roles.includes(CHITCHAT_ROLE));
    try {
      const updatedUser = await api.adminUpsertUser(token, user.user_id, {
        username: user.username || user.user_id,
        roles: nextRoles,
        is_active: user.is_active,
      });
      setCurrentUser((current) => (current?.user_id === updatedUser.user_id ? updatedUser : current));
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
  const shouldShowWelcome = !displayMessages.length && !chatPending && !pendingQuestion;
  const activeTraceArtifact =
    findTraceArtifact(traceArtifacts, activeTraceId)
    || findTraceArtifact(traceArtifacts, latestTrace?.trace_id || latestResponse?.trace?.trace_id || null);
  const inspectorResponse = activeTraceArtifact?.response || latestResponse;
  const inspectorTrace = activeTraceArtifact?.trace || latestTrace;
  const inspectorSqlAudit = activeTraceArtifact?.sql_audit || latestSqlAudit;
  const inspectorQueryLogs = mergeQueryLogs(activeTraceArtifact?.query_log || null, latestQueryLogs);
  const workspaceDomain = resolveDisplayDomain({
    response: inspectorResponse,
    sessionState,
    queryLog: activeTraceArtifact?.query_log || latestQueryLogs[0] || null,
  });

  const contextChips = buildContextChips(sessionState);
  const isAdmin = (currentUser?.roles || []).includes("admin");
  const showAdminCenter = isAdmin && viewMode === "admin";
  const showInspector = viewMode === "workspace";

  if (!currentUser) {
    return (
      <AuthScreen
        authMode={authMode}
        authError={authError}
        authPending={authPending}
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
                <div className="brand-meta">智能自然语言查数平台</div>
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
                <button className="primary-button" type="button" onClick={() => void createSession()} disabled={chatPending}>
                  新建会话
                </button>
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
                    const displayDomain = resolveDisplayDomain({ sessionState: session.last_state });
                    const tags = [
                      displayDomain,
                      session.status === "archived" ? "archived" : null,
                    ].filter(Boolean);
                    return (
                      <div key={session.id} className={`session-item${session.id === selectedSessionId ? " is-active" : ""}`}>
                        <div className="session-item-top">
                          <button className="session-item-trigger" type="button" onClick={() => void handleSelectSession(session.id)} disabled={chatPending}>
                            <div className="session-item-title">{formatSessionTitle(session.title)}</div>
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
                            disabled={chatPending}
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
              indexActionPending={adminIndexActionPending}
              indexActionMessage={adminIndexActionMessage}
              userForm={userForm}
              onUserFormChange={setUserForm}
              onSaveUser={() => void handleAdminUserSave()}
              onToggleUser={(user) => void handleAdminToggleUser(user)}
              onToggleChitchat={(user) => void handleAdminToggleChitchat(user)}
              onResetPassword={(user) => void handleAdminResetPassword(user)}
              onDeleteUser={(user) => void handleAdminDeleteUser(user)}
              onReplayLog={(log) => void handleAdminReplayLog(log)}
              onReloadMetadata={() => void handleAdminIndexAction("reload")}
              onPrewarmVector={() => void handleAdminIndexAction("prewarm")}
              onReloadAndPrewarm={() => void handleAdminIndexAction("reload_prewarm")}
              onRefresh={() => token && void loadAdminData(token)}
            />
          </main>
        ) : (
          <>
            <main className="main-column">
              <section className="hero-panel workspace-hero-panel">
                <div className="workspace-toolbar-top">
                  <div className="workspace-toolbar-copy">
                    <div className="workspace-toolbar-title-row">
                      <span className="workspace-toolbar-tag">工作台</span>
                      <div className="workspace-toolbar-title">{selectedSession?.title || "直接输入你的业务问题"}</div>
                    </div>
                    <div className={`workspace-toolbar-meta${workspaceError ? " is-error" : ""}`}>
                      {workspaceError
                        ? workspaceError
                        : selectedSession
                          ? `${workspaceDomain || "上下文未建立"} · 更新于 ${formatDate(selectedSession.updated_at)}`
                          : "支持自然语言问数、上下文追问、SQL 审阅和 Trace 排查"}
                    </div>
                  </div>

                  <div className="toolbar-stats workspace-toolbar-stats">
                    <span className="toolbar-stat">
                      当前域
                      <strong>{workspaceDomain || "等待上下文"}</strong>
                    </span>
                    <span className="toolbar-stat">
                      会话数
                      <strong>{String(sessions.length)}</strong>
                    </span>
                    <span className="toolbar-stat">
                      结果行数
                      <strong>{String(inspectorResponse?.execution?.row_count ?? inspectorSqlAudit?.row_count ?? 0)}</strong>
                    </span>
                  </div>
                </div>

                {contextChips.length ? (
                  <div className="context-strip workspace-context-strip">
                    {contextChips.map((chip) => (
                      <span className="context-chip" key={chip}>
                        {chip}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="context-strip workspace-context-strip">
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
                          系统会按会话上下文自动补足语义，生成 SQL、执行结果和 Trace。
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
                      {displayMessages.map((message) => {
                        const messageArtifact = message.trace_id ? findTraceArtifact(traceArtifacts, message.trace_id) : null;
                        return (
                          <article key={message.id} className={`message${message.role === "user" ? " is-user" : ""}`}>
                            <div className="message-avatar">{message.role === "user" ? "U" : "AI"}</div>
                            <div className="message-body">
                              <div className="message-meta">
                                <span>{message.role === "user" ? "你" : "Text2SQL"}</span>
                                <span>{formatDate(message.created_at)}</span>
                              </div>
                              <div className="message-card">{message.content}</div>
                              {message.role === "assistant" && chatPending && pendingProgress.length && message.id.startsWith("pending-assistant-") ? (
                                <PendingProgressCard events={pendingProgress} />
                              ) : null}
                              {message.role === "assistant" && messageArtifact ? (
                                <ConversationResultCard
                                  artifact={messageArtifact}
                                  isActive={messageArtifact.trace_id === activeTraceId}
                                  token={token}
                                  currentUser={currentUser}
                                  canInspect={showInspector}
                                  onSelect={() => focusInspector(messageArtifact.trace_id)}
                                />
                              ) : null}
                            </div>
                          </article>
                        );
                      })}
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
                      disabled={chatPending}
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          void handleSend();
                        }
                      }}
                      placeholder="输入业务问题，例如：查询26年MDL工厂top10投入型号及其物量"
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
              <aside ref={inspectorRef} className={`inspector${inspectorAttention ? " is-attention" : ""}`}>
                <div className="inspector-head">
                  <div>
                    <div className="panel-title">会话详情</div>
                    <div className="panel-subtitle">
                      {workspaceDomain || "等待上下文"}
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
                  {activeTab === "result" && (
                    <ResultPanel
                      latestResponse={inspectorResponse}
                      workspaceError={workspaceError}
                      token={token}
                      latestTrace={inspectorTrace}
                      latestQueryLog={inspectorQueryLogs[0] || null}
                      currentUser={currentUser}
                    />
                  )}
                  {activeTab === "sql" && (
                    <SqlPanel
                      latestResponse={inspectorResponse}
                      latestSqlAudit={inspectorSqlAudit}
                      latestTrace={inspectorTrace}
                      sessionState={sessionState}
                    />
                  )}
                  {activeTab === "trace" && <TracePanel latestTrace={inspectorTrace} latestQueryLogs={inspectorQueryLogs} />}
                  {activeTab === "state" && <StatePanel latestResponse={inspectorResponse} sessionState={sessionState} />}
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

function buildSessionTitle(question: string) {
  const normalized = question.trim();
  return normalized.length > 18 ? `${normalized.slice(0, 18)}...` : normalized;
}

function AuthScreen(props: {
  authMode: AuthMode;
  authError: string;
  authPending: boolean;
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
          <div className="auth-title">智能数据问答助手</div>
          <div className="auth-copy">
            只需自然语言提问，即可快速获取业务洞察。基于真实数据模型智能推理，让每一次查询都清晰、透明、可追溯。
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
  indexActionPending: "" | "reload" | "prewarm" | "reload_prewarm";
  indexActionMessage: string;
  userForm: UserUpsertPayload;
  onUserFormChange: (value: UserUpsertPayload) => void;
  onSaveUser: () => void;
  onToggleUser: (user: UserContext) => void;
  onToggleChitchat: (user: UserContext) => void;
  onResetPassword: (user: UserContext) => void;
  onDeleteUser: (user: UserContext) => void;
  onReplayLog: (log: RuntimeQueryLogRecord) => void;
  onReloadMetadata: () => void;
  onPrewarmVector: () => void;
  onReloadAndPrewarm: () => void;
  onRefresh: () => void;
}) {
  const vectorStatus = props.runtimeStatus?.vector_retrieval;
  const retrievalCorpusStatus = props.runtimeStatus?.retrieval_corpus;
  const vectorSyncStatus = retrievalCorpusStatus?.vector_sync;
  const runtimeEntries = props.runtimeStatus
    ? [
        ["业务库", describeHealth(props.runtimeStatus.business_database)],
        ["运行时库", describeHealth(props.runtimeStatus.runtime_database)],
        ["LLM", describeHealth(props.runtimeStatus.llm)],
        ["向量检索", describeVectorHealth(vectorStatus)],
        ["语料索引", describeCorpusHealth(retrievalCorpusStatus)],
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
              <MetaRow label="物理表数" value={String(props.metadataOverview?.table_count || 0)} />
              <MetaRow label="示例数" value={String(props.metadataOverview?.example_count || 0)} />
              <MetaRow label="Trace 数" value={String(props.metadataOverview?.trace_count || 0)} />
            </div>
          </article>

          <article className="detail-card admin-card">
            <div className="panel-row">
              <div className="detail-title">检索索引</div>
              <div className="admin-inline-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={props.onReloadMetadata}
                  disabled={Boolean(props.indexActionPending)}
                >
                  {props.indexActionPending === "reload" ? "重载中" : "重载元数据"}
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={props.onPrewarmVector}
                  disabled={Boolean(props.indexActionPending)}
                >
                  {props.indexActionPending === "prewarm" ? "预热中" : "重建向量索引"}
                </button>
                <button
                  className="primary-button"
                  type="button"
                  onClick={props.onReloadAndPrewarm}
                  disabled={Boolean(props.indexActionPending)}
                >
                  {props.indexActionPending === "reload_prewarm" ? "处理中" : "重载并重建"}
                </button>
              </div>
            </div>
            <div className="detail-copy">
              样例、知识库和 join pattern 更新后，可在这里显式刷新检索语料并同步向量索引。
            </div>
            <div className="meta-stack">
              <MetaRow label="当前状态" value={describeVectorWarmStatus(vectorStatus)} />
              <MetaRow label="Provider" value={vectorStatus?.provider || "-"} />
              <MetaRow label="模型" value={vectorStatus?.model || "-"} />
              <MetaRow label="已索引文档" value={String(vectorStatus?.indexed_document_count ?? 0)} />
              <MetaRow label="当前语料文档" value={String(retrievalCorpusStatus?.document_count ?? 0)} />
              <MetaRow label="待重建" value={describePendingRebuild(vectorSyncStatus?.pending_rebuild)} />
              <MetaRow label="上次同步" value={formatDate(vectorSyncStatus?.vector_sync_last_updated_at) || "-"} />
              <MetaRow label="本次重建文档" value={String(vectorSyncStatus?.rebuilt_document_count ?? 0)} />
              <MetaRow label="本次复用文档" value={String(vectorSyncStatus?.reused_document_count ?? 0)} />
              <MetaRow label="最后错误" value={vectorSyncStatus?.error || vectorStatus?.last_index_error || "-"} />
            </div>
            {props.indexActionMessage ? <div className="detail-copy admin-status-message">{props.indexActionMessage}</div> : null}
          </article>
        </div>

        <article className="detail-card admin-card admin-card-full" id="admin-users">
          <div className="detail-title">用户管理</div>
          <div className="detail-copy">系统会根据用户名自动生成内部 `user_id`。`chitchat` 权限用于控制闲聊回复，只有在后端开启 `ENABLE_CHITCHAT_MODE=true` 时才会生效。</div>

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
            <label className="toggle-chip">
              <input
                type="checkbox"
                checked={props.userForm.roles.includes(CHITCHAT_ROLE)}
                onChange={(event) =>
                  props.onUserFormChange({
                    ...props.userForm,
                    roles: toggleRole(props.userForm.roles, CHITCHAT_ROLE, event.target.checked),
                  })
                }
              />
              <span>闲聊权限</span>
            </label>
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
                    <button className="secondary-button" type="button" onClick={() => props.onToggleChitchat(user)}>
                      {user.roles.includes(CHITCHAT_ROLE) ? "移除闲聊权限" : "授予闲聊权限"}
                    </button>
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
                  {(() => {
                    const promptSummary = normalizePromptSummary(log.prompt_context_summary);
                    const selectedSources = promptSummary.selectedSources.join(", ");
                    const knowledgeChars = promptSummary.businessKnowledgeChars ? `${promptSummary.businessKnowledgeChars} chars` : "";
                    const fewShotUsed = promptSummary.fewShotUsed == null ? "" : promptSummary.fewShotUsed ? "few-shot" : "no few-shot";
                    return (
                      <>
                  <div>
                    <div className="admin-item-title">{log.question || "未记录问题"}</div>
                    <div className="admin-item-meta">
                      {log.subject_domain || "unknown"} · {formatDate(log.created_at)} · {log.trace_id}
                      {selectedSources ? ` · ${selectedSources}` : ""}
                    </div>
                  </div>
                  <div className="mini-tags">
                    <span className="mini-tag">{describeResponseStatus(log.answer_status || "unknown")}</span>
                    <span className="mini-tag">{String(log.row_count ?? 0)} rows</span>
                    {promptSummary.businessKnowledgeSource ? <span className="mini-tag">{promptSummary.businessKnowledgeSource}</span> : null}
                    {promptSummary.joinPatternIds.map((joinPatternId) => (
                      <span className="mini-tag" key={joinPatternId}>{joinPatternId}</span>
                    ))}
                    {knowledgeChars ? <span className="mini-tag">{knowledgeChars}</span> : null}
                    {fewShotUsed ? <span className="mini-tag">{fewShotUsed}</span> : null}
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
                      </>
                    );
                  })()}
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
                  <span className="mini-tag">{describeResponseStatus(replayAnswer?.status || "unknown")}</span>
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
                  <strong>{props.replayResult.response.context_validation.valid ? "通过" : "失败"}</strong>
                </div>
                <div className="compact-stat">
                  <span>SQL 校验</span>
                  <strong>{props.replayResult.response.sql_validation.valid ? "通过" : "失败"}</strong>
                </div>
                <div className="compact-stat">
                  <span>执行状态</span>
                  <strong>{describeResponseStatus(replayExecution?.status || "unknown")}</strong>
                </div>
                <div className="compact-stat">
                  <span>Prompt上下文</span>
                  <strong>{props.replayResult.diff?.prompt_context_changed ? "有变化" : "稳定"}</strong>
                </div>
              </div>

              {props.replayResult.diff?.replay_prompt_context_summary ? (
                <div className="detail-card">
                  <div className="detail-title">Prompt 上下文摘要</div>
                  <pre className="json-block">
                    {JSON.stringify(props.replayResult.diff.replay_prompt_context_summary, null, 2)}
                  </pre>
                </div>
              ) : null}

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
                            <td key={column}>{formatResultCell(row[column])}</td>
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

function PendingProgressCard(props: { events: ProgressEvent[] }) {
  if (!props.events.length) {
    return null;
  }
  const view = buildPendingProgressView(props.events);
  return (
    <div className="message-result-card is-pending">
      <div className="message-result-head">
        <div className="message-result-summary">
          <strong>执行进度</strong>
          <span className="progress-current-icon" aria-hidden="true">{view.currentStageIcon}</span>
          <span>{view.currentStageLabel}</span>
          <span>{view.responseStatusLabel}</span>
        </div>
      </div>
      <div className="progress-current-note">{view.currentStageNote}</div>
      <div className="progress-meter" aria-hidden="true">
        <div className="progress-meter-fill" style={{ width: `${view.progressPercent}%` }} />
      </div>
      {view.latest.type === "failed" && view.latest.detail && view.latest.detail !== view.currentStageNote ? (
        <div className="message-result-note">{view.latest.detail}</div>
      ) : null}
      <div className="progress-step-list">
        <div className={`progress-step-item is-${view.currentStep.tone}`}>
          <span className={`progress-step-icon is-${view.currentStep.tone}`} aria-hidden="true">
            {view.currentStep.icon}
          </span>
          <div className="progress-step-copy">
            <span className="progress-step-label">{view.currentStep.label}</span>
            <span className="progress-step-note">{view.currentStep.note}</span>
          </div>
          <span className={`progress-step-badge is-${view.currentStep.tone}`}>{view.currentStep.badge}</span>
        </div>
      </div>
      <div className="message-result-note">Trace: {view.latest.trace_id}</div>
    </div>
  );
}

function ConversationResultCard(props: {
  artifact: SessionTraceWorkspaceRecord;
  isActive: boolean;
  token: string | null;
  currentUser: UserContext | null;
  canInspect: boolean;
  onSelect: () => void;
}) {
  const response = props.artifact.response;
  const answer = response?.answer;
  const execution = response?.execution;
  const queryLog = props.artifact.query_log;
  const domain = resolveDisplayDomain({ response, queryLog });
  const status = execution?.status || answer?.status || queryLog?.answer_status || "unknown";
  const rowCount = execution?.row_count ?? props.artifact.sql_audit?.row_count ?? queryLog?.row_count ?? 0;
  const showRowCount = Boolean(execution) || !isTerminalNonSqlStatus(answer?.status || queryLog?.answer_status);
  const resultColumns = execution?.columns || [];
  const resultRows = execution?.rows || [];
  const canDownload = Boolean(
    props.token
    && props.artifact.trace?.trace_id
    && (response?.sql || props.artifact.sql_audit?.sql_text),
  );

  return (
    <div className={`message-result-card${props.isActive ? " is-active" : ""}`}>
      <div className="message-result-head">
        <div className="message-result-summary">
          <strong>{domain || "等待上下文"}</strong>
          <span>{describeResponseStatus(status)}</span>
          <span>{showRowCount ? `结果 ${rowCount} 行` : "未进入 SQL"}</span>
        </div>
        <div className="message-result-actions">
          {props.canInspect ? (
            <button className="secondary-button message-result-button" type="button" onClick={props.onSelect}>
              查看详情
            </button>
          ) : null}
          {canDownload ? (
            <button
              className="secondary-button message-result-button"
              type="button"
              onClick={() => {
                void downloadTraceCsv(props.token!, props.artifact.trace!.trace_id);
              }}
            >
              下载
            </button>
          ) : null}
        </div>
      </div>

      {answer?.detail ? <div className="message-result-note">{answer.detail}</div> : null}
      {answer?.follow_up_hint ? <div className="message-result-note">下一步：{answer.follow_up_hint}</div> : null}

      {resultRows.length ? (
        <div className="message-result-table-wrap">
          <table className="message-result-table">
            <thead>
              <tr>
                {resultColumns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {resultRows.map((row, index) => (
                <tr key={`${props.artifact.trace_id}-${index}`}>
                  {resultColumns.map((column) => (
                    <td key={column}>{formatResultCell(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}


function ResultPanel(props: {
  latestResponse: ChatResponse | null;
  workspaceError: string;
  token: string | null;
  latestTrace: TraceRecord | null;
  latestQueryLog: RuntimeQueryLogRecord | null;
  currentUser: UserContext | null;
}) {
  if (props.workspaceError && !props.latestResponse) {
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
  const questionContext = props.latestResponse.question_context;
  const contextSummary = props.latestResponse.context_summary;
  const promptSummary = normalizePromptSummary(getPromptContextSummaryFromTrace(props.latestTrace));
  const requestElapsedMs =
    getRequestElapsedMs(props.latestTrace) ??
    props.latestQueryLog?.total_elapsed_ms ??
    execution?.elapsed_ms ??
    null;

  return (
    <section className="tab-panel">
      {props.workspaceError ? (
        <div className="detail-card subtle-card">
          <div className="detail-title">提示</div>
          <div className="detail-copy">{props.workspaceError}</div>
        </div>
      ) : null}

      <div className="detail-card accent-card">
        <div className="panel-row">
          <div className="detail-title">回答</div>
          {props.token && props.latestTrace && props.latestResponse.sql ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                void downloadTraceCsv(props.token!, props.latestTrace!.trace_id);
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
          <strong>{describeResponseStatus(execution?.status || answer?.status || "unknown")}</strong>
        </div>
        <div className="compact-stat">
          <span>返回行数</span>
          <strong>{execution ? String(execution.row_count ?? 0) : "-"}</strong>
        </div>
        <div className="compact-stat">
          <span>耗时</span>
          <strong>{formatElapsedSeconds(requestElapsedMs)}</strong>
        </div>
      </div>

      <div className="detail-card">
        <div className="detail-title">结果展示</div>
        <div className="detail-copy">
          {execution?.rows?.length ? "完整结果已展示在对话消息中。" : "当前没有结果行可展示。"}
        </div>
      </div>

      <div className="detail-card">
        <div className="detail-title">问题上下文</div>
        <div className="meta-stack">
          <MetaRow label="原始问题" value={questionContext?.original_question || "-"} />
          <MetaRow label="完整问题" value={questionContext?.effective_question || "-"} />
          <MetaRow label="上下文关系" value={describeContextRelation(questionContext?.context_relation)} />
          <MetaRow label="上下文决策" value={describeQuestionDecision(questionContext?.decision)} />
          <MetaRow label="业务域" value={questionContext?.subject_domain || props.latestResponse.classification.subject_domain || "-"} />
          <MetaRow label="来源" value={questionContext?.source || "-"} />
        </div>
        {questionContext?.semantic_brief ? (
          <div className="detail-copy">{questionContext.semantic_brief}</div>
        ) : null}
      </div>

      <div className="detail-card">
        <div className="detail-title">分类与上下文</div>
        <div className="meta-stack">
          <MetaRow label="问题类型" value={props.latestResponse.classification.question_type || "-"} />
          <MetaRow label="业务域" value={props.latestResponse.classification.subject_domain || "-"} />
          <MetaRow label="上下文摘要" value={describeContextSummary(contextSummary)} />
          <MetaRow label="原因码" value={props.latestResponse.classification.reason_code || "-"} />
        </div>
      </div>

      <div className="detail-card">
        <div className="detail-title">检索摘要</div>
        <div className="meta-stack">
          <MetaRow label="上下文校验" value={props.latestResponse.context_validation.valid ? "通过" : "未通过"} />
          <MetaRow label="业务域" value={(retrieval?.domains || []).join(", ") || "-"} />
          <MetaRow label="指标" value={(retrieval?.metrics || []).join(", ") || "-"} />
          <MetaRow label="知识来源" value={promptSummary.businessKnowledgeSource || "-"} />
          <MetaRow label="Join Pattern" value={promptSummary.joinPatternIds.join(", ") || "-"} />
        </div>
      </div>
    </section>
  );
}

function SqlPanel(props: {
  latestResponse: ChatResponse | null;
  latestSqlAudit: RuntimeSqlAuditRecord | null;
  latestTrace: TraceRecord | null;
  sessionState: SessionState | null;
}) {
  const sql = props.latestResponse?.sql || props.latestSqlAudit?.sql_text || "";
  const contextSummary =
    props.latestResponse?.context_summary ||
    props.sessionState?.last_context_summary ||
    null;
  const promptSummary = normalizePromptSummary(getPromptContextSummaryFromTrace(props.latestTrace));
  const selectedSources = promptSummary.selectedSources.length
    ? promptSummary.selectedSources
    : contextSummary?.tables || [];
  const tableSchemasCount = promptSummary.tableSchemasCount ?? (selectedSources.length ? selectedSources.length : null);

  if (!sql && !contextSummary) {
    return (
      <section className="tab-panel">
        <div className="empty-card subtle-card">这里会展示 LLM 生成 SQL、校验信息和上下文摘要。</div>
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
        <div className="detail-title">SQL 输入上下文</div>
        <div className="meta-stack">
          <MetaRow label="可用表" value={selectedSources.join(", ") || "-"} />
          <MetaRow label="表结构数" value={formatOptionalNumber(tableSchemasCount)} />
          <MetaRow label="业务知识" value={formatPromptKnowledge(promptSummary)} />
          <MetaRow label="样例" value={formatPromptExamples(promptSummary)} />
          <MetaRow label="时间解析" value={formatOptionalNumber(promptSummary.timeResolutionCount)} />
        </div>
      </div>

      <div className="detail-card">
        <div className="detail-title">上下文摘要</div>
        <pre className="json-block">{JSON.stringify(contextSummary || {}, null, 2)}</pre>
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
              <MetaRow key={log.trace_id} label={log.question || "未记录问题"} value={describeResponseStatus(log.answer_status || "unknown")} />
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
                <div className="trace-step-status">{describeResponseStatus(step.status)}</div>
              </div>
              {step.detail ? <div className="trace-step-copy">{step.detail}</div> : null}
              {step.metadata && Object.keys(step.metadata).length ? (
                <pre className="json-block">{JSON.stringify(step.metadata, null, 2)}</pre>
              ) : null}
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

function findTraceArtifact(
  items: SessionTraceWorkspaceRecord[],
  traceId: string | null | undefined,
) {
  if (!traceId) {
    return null;
  }
  return items.find((item) => item.trace_id === traceId) || null;
}

function getPromptContextSummaryFromTrace(trace: TraceRecord | null | undefined) {
  if (!trace?.steps?.length) {
    return null;
  }
  const buildPromptStep = trace.steps.find((step) => step.name === "build_sql_prompt");
  if (isRecord(buildPromptStep?.metadata?.context_summary)) {
    return buildPromptStep.metadata.context_summary;
  }
  if (isRecord(buildPromptStep?.metadata?.prompt_context_summary)) {
    return buildPromptStep.metadata.prompt_context_summary;
  }
  const generateSqlStep = trace.steps.find((step) => step.name === "generate_sql" || step.name === "sql_generation");
  if (isRecord(generateSqlStep?.metadata?.prompt_context_summary)) {
    return generateSqlStep.metadata.prompt_context_summary;
  }
  return isRecord(generateSqlStep?.metadata?.context_summary)
    ? generateSqlStep.metadata.context_summary
    : null;
}

function normalizePromptSummary(summary: Record<string, unknown> | null | undefined) {
  return {
    selectedSources: getStringArrayValue(summary?.selected_sources),
    tableSchemasCount: getNumberValue(summary?.table_schemas_count),
    businessKnowledgeChars: typeof summary?.business_knowledge_chars === "number" ? summary.business_knowledge_chars : null,
    fewShotUsed: typeof summary?.few_shot_used === "boolean" ? summary.few_shot_used : null,
    retrievedExampleCount: getNumberValue(summary?.retrieved_example_count),
    retrievedExampleIds: getStringArrayValue(summary?.retrieved_example_ids),
    timeResolutionCount: getNumberValue(summary?.time_resolution_count),
    businessKnowledgeSource: getStringValue(summary?.business_knowledge_source),
    joinPatternIds: getStringArrayValue(summary?.join_pattern_ids),
  };
}

function getNumberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatOptionalNumber(value: number | null) {
  return value == null ? "-" : String(value);
}

function formatPromptKnowledge(summary: ReturnType<typeof normalizePromptSummary>) {
  const chars = summary.businessKnowledgeChars == null ? "-" : `${summary.businessKnowledgeChars} chars`;
  return summary.businessKnowledgeSource ? `${summary.businessKnowledgeSource} · ${chars}` : chars;
}

function formatPromptExamples(summary: ReturnType<typeof normalizePromptSummary>) {
  if (summary.retrievedExampleCount == null) {
    return "-";
  }
  const ids = summary.retrievedExampleIds.slice(0, 3).join(", ");
  return ids ? `${summary.retrievedExampleCount} · ${ids}` : String(summary.retrievedExampleCount);
}

function describeContextRelation(value: string | null | undefined) {
  if (value === "follow_up") {
    return "追问";
  }
  if (value === "ambiguous") {
    return "不明确";
  }
  if (value === "new") {
    return "新问题";
  }
  return "-";
}

function describeQuestionDecision(value: string | null | undefined) {
  if (value === "answerable") {
    return "可回答";
  }
  if (value === "clarification_needed") {
    return "需澄清";
  }
  if (value === "invalid") {
    return "无效";
  }
  return "-";
}

function describeContextSummary(contextSummary?: ContextSummary | null) {
  const tables = contextSummary?.tables || [];
  const limit = contextSummary?.limit;
  const tableText = tables.length ? `${tables.length} 表` : "等待检索选表";
  const limitText = limit ? `limit ${limit}` : "无行数限制";
  return `${tableText} · ${limitText}`;
}

function resolveDisplayDomain(input: {
  response?: ChatResponse | null;
  sessionState?: SessionState | null;
  queryLog?: RuntimeQueryLogRecord | null;
}) {
  return firstKnownDomain([
    input.response?.context_summary?.subject_domain,
    input.response?.classification?.subject_domain,
    input.response?.question_context?.subject_domain,
    ...(input.response?.retrieval?.domains || []),
    input.sessionState?.subject_domain,
    input.sessionState?.topic,
    input.queryLog?.subject_domain,
    inferDomainFromTables(input.response?.context_summary?.tables || input.sessionState?.tables || []),
  ]);
}

function firstKnownDomain(values: Array<string | null | undefined>) {
  for (const value of values) {
    const normalized = (value || "").trim();
    if (normalized && normalized !== "unknown") {
      return normalized;
    }
  }
  return "";
}

function inferDomainFromTables(tables: string[]) {
  const domains = new Set<string>();
  for (const table of tables) {
    const normalized = table.trim();
    if (["daily_inventory", "oms_inventory"].includes(normalized)) {
      domains.add("inventory");
    }
    if (["v_demand", "p_demand"].includes(normalized)) {
      domains.add("demand");
    }
    if (["daily_PLAN", "monthly_plan_approved", "weekly_rolling_plan", "production_actuals"].includes(normalized)) {
      domains.add("plan_actual");
    }
    if (normalized === "sales_financial_perf") {
      domains.add("sales_financial");
    }
    if (["product_attributes", "product_mapping"].includes(normalized)) {
      domains.add("dimension");
    }
  }
  if (domains.size === 1) {
    return Array.from(domains)[0];
  }
  const primaryDomains = Array.from(domains).filter((domain) => domain !== "dimension");
  return primaryDomains.length === 1 ? primaryDomains[0] : "";
}

function getStringArrayValue(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function getStringValue(value: unknown) {
  return typeof value === "string" && value.trim().length > 0 ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mergeQueryLogs(
  selectedLog: RuntimeQueryLogRecord | null,
  latestQueryLogs: RuntimeQueryLogRecord[],
) {
  if (!selectedLog) {
    return latestQueryLogs;
  }
  const nextItems = [selectedLog, ...latestQueryLogs.filter((item) => item.trace_id !== selectedLog.trace_id)];
  return nextItems.slice(0, 5);
}

function upsertTraceArtifact(
  items: SessionTraceWorkspaceRecord[],
  artifact: SessionTraceWorkspaceRecord,
) {
  const existing = items.find((item) => item.trace_id === artifact.trace_id);
  const nextItems = items.filter((item) => item.trace_id !== artifact.trace_id);
  nextItems.unshift({
    trace_id: artifact.trace_id,
    response: artifact.response ?? existing?.response ?? null,
    trace: artifact.trace ?? existing?.trace ?? null,
    sql_audit: artifact.sql_audit ?? existing?.sql_audit ?? null,
    query_log: artifact.query_log ?? existing?.query_log ?? null,
  });
  return nextItems;
}

function resolvePendingMessages(
  current: ChatMessage[],
  payload: {
    pendingUserId: string;
    pendingAssistantId: string;
    sessionId: string;
    assistantContent: string;
    assistantTraceId?: string;
  },
) {
  return normalizeMessages(
    current.map((message) => {
      if (message.id === payload.pendingUserId) {
        return {
          ...message,
          session_id: payload.sessionId,
        };
      }
      if (message.id === payload.pendingAssistantId) {
        return {
          ...message,
          session_id: payload.sessionId,
          content: payload.assistantContent,
          trace_id: payload.assistantTraceId ?? message.trace_id,
        };
      }
      return message;
    }),
  );
}

function buildPendingProgressView(events: ProgressEvent[]) {
  const latest = events[events.length - 1];
  const currentStageIndex = PROGRESS_BASE_STAGES.indexOf(latest.stage as (typeof PROGRESS_BASE_STAGES)[number]);
  const totalStageCount = PROGRESS_BASE_STAGES.length + 1;
  const completedCount = latest.type === "completed"
    ? totalStageCount
    : Math.max(currentStageIndex, 0);
  const activeCount = latest.type === "failed" ? 0 : 1;
  const progressPercent = latest.type === "completed"
    ? 100
    : Math.max(6, Math.min(99, Math.round(((completedCount + activeCount) / totalStageCount) * 100)));
  const currentTone = classifyProgressTone(latest);
  const currentStageMeta = getProgressStageMeta(latest.stage);
  const currentStep: PendingProgressStep = {
    stage: latest.stage,
    label: describeProgressStage(latest.stage),
    note: describeProgressStepNote(latest.stage, latest, currentTone),
    icon: currentStageMeta.icon,
    tone: currentTone,
    badge: describeProgressBadge(latest),
  };

  return {
    latest,
    currentStep,
    progressPercent,
    currentStageIcon: currentStageMeta.icon,
    currentStageLabel: describeProgressStage(latest.stage),
    currentStageNote: describeProgressCurrentNote(latest),
    responseStatusLabel: describeResponseStatus(latest.status),
  };
}

function getProgressStageMeta(stage: string) {
  return PROGRESS_STAGE_META[stage] || {
    label: stage,
    note: "系统正在推进当前阶段。",
    icon: "·",
  };
}

function describeProgressStepNote(
  stage: string,
  event: ProgressEvent,
  tone: string,
) {
  if (tone === "failed" && event.detail) {
    return event.detail;
  }
  if (tone === "active" && stage === "execution") {
    return "正在执行 SQL，并等待数据库返回结果。";
  }
  if (tone === "active" && stage === "sql_generation") {
    return "正在根据上下文生成 SQL 语句。";
  }
  if (tone === "active" && stage === "question_analysis") {
    return "正在识别追问关系，并整理完整问题上下文。";
  }
  return getProgressStageMeta(stage).note;
}

function describeProgressCurrentNote(event: ProgressEvent) {
  if (event.type === "failed" && event.detail) {
    return event.detail;
  }
  return getProgressStageMeta(event.stage).note;
}

function isTerminalNonSqlStatus(status: string | null | undefined) {
  const normalized = (status || "").toLowerCase();
  return ["clarification_needed", "invalid", "chat"].includes(normalized);
}

function classifyProgressTone(event: ProgressEvent) {
  const normalized = event.status.toLowerCase();
  if (event.type === "failed" || ["failed", "error", "invalid", "denied"].includes(normalized)) {
    return "failed" as const;
  }
  if (event.type === "completed") {
    return "completed" as const;
  }
  if (["completed", "success", "ok"].includes(normalized)) {
    return "completed" as const;
  }
  if (["skipped"].includes(normalized)) {
    return "skipped" as const;
  }
  if (["running", "queued"].includes(normalized)) {
    return "active" as const;
  }
  return "active" as const;
}

function describeProgressBadge(event: ProgressEvent) {
  const normalized = event.status.toLowerCase();
  if (event.type === "failed" || ["failed", "error", "invalid", "denied"].includes(normalized)) {
    return "失败";
  }
  if (event.type === "completed") {
    return "已完成";
  }
  if (["completed", "success", "ok"].includes(normalized)) {
    return "已完成";
  }
  if (["skipped"].includes(normalized)) {
    return "已跳过";
  }
  if (["queued"].includes(normalized)) {
    return "排队中";
  }
  if (["running"].includes(normalized)) {
    return "进行中";
  }
  return describeResponseStatus(event.status);
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

function describeProgressStage(stage: string) {
  return getProgressStageMeta(stage).label;
}

function formatSessionTitle(title?: string | null) {
  const normalized = (title || "").trim();
  return normalized || "新对话";
}

function formatResultCell(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) && !Number.isInteger(value) ? value.toFixed(2) : String(value);
  }
  if (typeof value === "string" && /^-?\d+\.\d+$/.test(value.trim())) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue.toFixed(2) : value;
  }
  return String(value);
}

function describeResponseStatus(status: string) {
  const normalized = status.toLowerCase();
  if (["running"].includes(normalized)) {
    return "进行中";
  }
  if (["queued"].includes(normalized)) {
    return "排队中";
  }
  if (["success", "completed", "ok"].includes(normalized)) {
    return "已完成";
  }
  if (["no_data", "empty", "empty_result"].includes(normalized)) {
    return "无结果";
  }
  if (["timeout"].includes(normalized)) {
    return "执行超时";
  }
  if (["blocked"].includes(normalized)) {
    return "已拦截";
  }
  if (["not_configured"].includes(normalized)) {
    return "未配置";
  }
  if (["sql_missing"].includes(normalized)) {
    return "SQL缺失";
  }
  if (["permission_denied"].includes(normalized)) {
    return "无权限";
  }
  if (["db_error"].includes(normalized)) {
    return "数据库错误";
  }
  if (["stub"].includes(normalized)) {
    return "规划完成";
  }
  if (["clarification_needed"].includes(normalized)) {
    return "需澄清";
  }
  if (["chat"].includes(normalized)) {
    return "闲聊";
  }
  if (["skipped"].includes(normalized)) {
    return "已跳过";
  }
  if (["accepted"].includes(normalized)) {
    return "已接收";
  }
  if (["pending"].includes(normalized)) {
    return "待处理";
  }
  if (["failed", "error", "invalid", "denied"].includes(normalized)) {
    return "失败";
  }
  if (["completed_with_warning"].includes(normalized)) {
    return "已完成，有告警";
  }
  return status;
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

function describeVectorHealth(value: RuntimeStatus["vector_retrieval"] | null | undefined) {
  if (!value) {
    return "-";
  }
  if (!value.enabled) {
    return "未启用";
  }
  if (value.indexing) {
    return value.ready ? "后台刷新中" : "预热中";
  }
  if (value.ready && value.last_index_error) {
    return "已就绪，上次刷新失败";
  }
  if (value.ready) {
    return "已就绪";
  }
  if (value.last_index_error) {
    return "预热失败";
  }
  return "等待初始化";
}

function describeCorpusHealth(value: RuntimeStatus["retrieval_corpus"] | null | undefined) {
  if (!value) {
    return "-";
  }
  return `${value.document_count} 篇文档`;
}

function describeVectorWarmStatus(value: RuntimeStatus["vector_retrieval"] | null | undefined) {
  if (!value) {
    return "-";
  }
  if (!value.enabled) {
    return "向量检索未启用";
  }
  if (value.indexing) {
    return value.ready ? "后台刷新中，旧索引仍可用" : "初次预热中";
  }
  if (value.ready && value.last_index_error) {
    return "已就绪，但最近一次刷新失败";
  }
  if (value.ready) {
    return "已就绪";
  }
  if (value.last_index_error) {
    return "预热失败";
  }
  return "等待初始化";
}

function describePendingRebuild(value: boolean | null | undefined) {
  if (value == null) {
    return "-";
  }
  return value ? "是" : "否";
}

function getRequestElapsedMs(trace: TraceRecord | null | undefined) {
  if (!trace?.steps?.length) {
    return null;
  }
  const chatTotalStep = trace.steps.find((step) => step.name === "chat_total");
  const elapsedMs = chatTotalStep?.metadata?.elapsed_ms;
  return typeof elapsedMs === "number" && Number.isFinite(elapsedMs) ? elapsedMs : null;
}

function formatElapsedSeconds(elapsedMs: number | null | undefined) {
  if (elapsedMs == null || !Number.isFinite(elapsedMs)) {
    return "-";
  }
  const seconds = elapsedMs / 1000;
  if (seconds < 10) {
    return `${seconds.toFixed(2)} 秒`;
  }
  if (seconds < 100) {
    return `${seconds.toFixed(1)} 秒`;
  }
  return `${Math.round(seconds)} 秒`;
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

async function downloadTraceCsv(token: string, traceId: string) {
  try {
    const csv = await api.downloadTraceResult(token, traceId);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `trace-${traceId}.csv`;
    anchor.click();
    window.URL.revokeObjectURL(url);
  } catch {
    return;
  }
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

function toggleRole(roles: string[], roleName: string, enabled: boolean) {
  const nextRoles = roles.map((item) => item.trim()).filter(Boolean);
  const hasTargetRole = nextRoles.includes(roleName);
  if (enabled && !hasTargetRole) {
    return [...nextRoles, roleName];
  }
  if (!enabled && hasTargetRole) {
    return nextRoles.filter((item) => item !== roleName);
  }
  return nextRoles;
}

export default App;
