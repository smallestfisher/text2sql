import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Login } from "./Login";
import { SemanticStudio } from "./SemanticStudio";
import { SystemSettings } from "./SystemSettings";
import workspaceIllustration from "./assets/workspace-illustration.svg";
import { api, isAuthFailure } from "./api";
import type {
  ChatMessage,
  ChatResponse,
  ChatSession,
  AdminMetricsSummary,
  AdminUserRecord,
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
const VIEW_MODE_KEY = "text2sql.frontend.view_mode";
const THEME_KEY = "text2sql.frontend.theme";
const ADMIN_TABLE_PAGE_SIZE = 5;
const PROMPTS = [
  "本月销售额相比上月增长了多少？",
  "各产品线的销售趋势如何？",
  "哪个区域的业绩表现最好？",
  "客户数量的变化趋势是什么？",
];
const WORKSPACE_FEATURES = [
  {
    icon: "chat",
    title: "自然语言提问",
    description: "像聊天一样描述问题，无需任何 SQL 基础",
  },
  {
    icon: "database",
    title: "多轮对话追问",
    description: "支持上下文理解，持续追问更深入",
  },
  {
    icon: "trend",
    title: "趋势与分析洞察",
    description: "自动生成图表，快速发现趋势与异常",
  },
  {
    icon: "spark",
    title: "结果解释与建议",
    description: "AI 帮你解读结果，提供业务建议",
  },
] as const;
const ADMIN_SIDEBAR_LINKS = [
  { href: "#admin-overview", icon: "pie", label: "数据总览" },
  { href: "#admin-runtime", icon: "server", label: "运行状态" },
  { href: "#admin-index", icon: "search", label: "检索索引" },
  { href: "#admin-semantic", icon: "database", label: "语义资产" },
  { href: "#admin-settings", icon: "server", label: "系统设置" },
  { href: "#admin-users", icon: "users", label: "用户管理" },
  { href: "#admin-logs", icon: "document", label: "日志审计" },
] as const;
const ADMIN_DASHBOARD_SECTION_LABELS: Record<string, string> = {
  runtime_status: "运行状态",
  metrics: "指标汇总",
  metadata_overview: "元数据概览",
  users: "用户列表",
  roles: "角色列表",
  query_logs: "日志审计",
  feedback_summary: "反馈汇总",
  evaluation_summary: "评测汇总",
  runtime_sessions: "会话列表",
};
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
type ThemeMode = "light" | "dark";

const emptyUserForm: UserUpsertPayload = {
  username: "",
  password: "",
  roles: ["viewer"],
  is_active: true,
};

function readStoredViewMode(): ViewMode {
  const storedViewMode = window.localStorage.getItem(VIEW_MODE_KEY);
  return storedViewMode === "admin" ? "admin" : "workspace";
}

function readStoredThemeMode(): ThemeMode {
  return window.localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
}

function applyThemeMode(mode: ThemeMode) {
  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
}

function isAdminUser(user: UserContext | null | undefined) {
  return (user?.roles || []).includes("admin");
}

function App() {
  const [token, setToken] = useState<string | null>(() => window.localStorage.getItem(TOKEN_KEY));
  const [authInitializing, setAuthInitializing] = useState(() => Boolean(window.localStorage.getItem(TOKEN_KEY)));
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [authError, setAuthError] = useState("");
  const [authPending, setAuthPending] = useState(false);
  const [currentUser, setCurrentUser] = useState<UserContext | null>(null);
  const [viewMode, setViewModeState] = useState<ViewMode>(() => readStoredViewMode());
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    const storedThemeMode = readStoredThemeMode();
    applyThemeMode(storedThemeMode);
    return storedThemeMode;
  });

  function setViewMode(mode: ViewMode) {
    setViewModeState(mode);
    window.localStorage.setItem(VIEW_MODE_KEY, mode);
  }

  function toggleThemeMode() {
    setThemeMode((current) => {
      const next = current === "dark" ? "light" : "dark";
      window.localStorage.setItem(THEME_KEY, next);
      applyThemeMode(next);
      return next;
    });
  }

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
  const bootRunRef = useRef(0);
  const adminLoadRunRef = useRef(0);
  const adminLoadAbortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const inspectorRef = useRef<HTMLElement | null>(null);

  const [adminPending, setAdminPending] = useState(false);
  const [adminError, setAdminError] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [adminMetrics, setAdminMetrics] = useState<AdminMetricsSummary | null>(null);
  const [metadataOverview, setMetadataOverview] = useState<MetadataOverview | null>(null);
  const [adminUsers, setAdminUsers] = useState<AdminUserRecord[]>([]);
  const [adminUserCount, setAdminUserCount] = useState(0);
  const [adminUserPage, setAdminUserPage] = useState(1);
  const [adminRoles, setAdminRoles] = useState<RoleRecord[]>([]);
  const [adminLogs, setAdminLogs] = useState<RuntimeQueryLogRecord[]>([]);
  const [adminLogCount, setAdminLogCount] = useState(0);
  const [adminLogPage, setAdminLogPage] = useState(1);
  const [adminFeedbackSummary, setAdminFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [adminEvalSummary, setAdminEvalSummary] = useState<EvaluationSummary | null>(null);
  const [adminSessions, setAdminSessions] = useState<ChatSession[]>([]);
  const [adminSessionCount, setAdminSessionCount] = useState(0);
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

  useLayoutEffect(() => {
    applyThemeMode(themeMode);
  }, [themeMode]);

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
      return () => {
        adminLoadAbortRef.current?.abort();
      };
    }
    return undefined;
  }, [token, viewMode, currentUser, adminUserPage, adminLogPage]);

  useEffect(() => {
    if (!inspectorAttention) {
      return;
    }
    const timer = window.setTimeout(() => setInspectorAttention(false), 900);
    return () => window.clearTimeout(timer);
  }, [inspectorAttention]);

  async function boot() {
    const runId = ++bootRunRef.current;
    const storedToken = window.localStorage.getItem(TOKEN_KEY);
    const storedSessionId = window.localStorage.getItem(SESSION_KEY) || null;

    setAuthInitializing(Boolean(storedToken));
    try {
      try {
        const status = await api.bootstrapStatus();
        if (bootRunRef.current !== runId) {
          return;
        }
        setAuthMode(status.has_users ? "login" : "bootstrap");
      } catch (error) {
        if (bootRunRef.current !== runId) {
          return;
        }
        setAuthError(errorMessage(error));
      }

      if (!storedToken) {
        setToken(null);
        setCurrentUser(null);
        return;
      }

      try {
        const me = await api.me(storedToken);
        if (bootRunRef.current !== runId) {
          return;
        }
        setToken(storedToken);
        setCurrentUser(me);
        setAuthError("");
        setWorkspaceError("");
        if (!isAdminUser(me) && readStoredViewMode() === "admin") {
          setViewMode("workspace");
        }
        setAuthInitializing(false);
        void restoreWorkspace(storedToken, storedSessionId, runId);
      } catch (error) {
        if (bootRunRef.current !== runId) {
          return;
        }
        if (isAuthFailure(error)) {
          clearAuth();
          return;
        }
        setAuthError(errorMessage(error));
      }
    } finally {
      if (bootRunRef.current === runId) {
        setAuthInitializing(false);
      }
    }
  }

  async function initializeWorkspace(authToken: string, preferredSessionId = window.localStorage.getItem(SESSION_KEY) || null) {
    setWorkspaceError("");
    let me: UserContext;
    try {
      me = await api.me(authToken);
    } catch (error) {
      if (isAuthFailure(error)) {
        clearAuth();
        return;
      }
      setWorkspaceError(errorMessage(error));
      return;
    }

    setCurrentUser(me);
    if (!isAdminUser(me) && readStoredViewMode() === "admin") {
      setViewMode("workspace");
    }
    try {
      await refreshSessions(authToken, preferredSessionId);
    } catch (error) {
      setWorkspaceError(errorMessage(error));
    }
  }

  async function restoreWorkspace(authToken: string, preferredSessionId: string | null, bootRunId?: number) {
    try {
      await refreshSessions(authToken, preferredSessionId);
    } catch (error) {
      if (bootRunId !== undefined && bootRunRef.current !== bootRunId) {
        return;
      }
      setWorkspaceError(errorMessage(error));
    }
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
    setWorkspaceError("");
    try {
      if (authMode === "bootstrap") {
        await api.bootstrapAdmin(username, password);
      }
      const loginResponse = await api.login(username, password);
      window.localStorage.setItem(TOKEN_KEY, loginResponse.access_token);
      setToken(loginResponse.access_token);
      setCurrentUser(loginResponse.user);
      setViewMode("workspace");
      setAuthInitializing(false);
      try {
        await refreshSessions(loginResponse.access_token, selectedSessionId);
      } catch (error) {
        setWorkspaceError(errorMessage(error));
      }
    } catch (error) {
      setAuthError(errorMessage(error));
    } finally {
      setAuthPending(false);
    }
  }

  function clearAuth() {
    bootRunRef.current += 1;
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(SESSION_KEY);
    setToken(null);
    setAuthInitializing(false);
    setCurrentUser(null);
    setViewMode("workspace");
    setAuthError("");
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
    const runId = ++adminLoadRunRef.current;
    adminLoadAbortRef.current?.abort();
    const controller = new AbortController();
    adminLoadAbortRef.current = controller;
    setAdminPending(true);
    setAdminError("");
    const userPage = Math.max(1, adminUserPage);
    const logPage = Math.max(1, adminLogPage);
    try {
      const dashboard = await api.adminDashboard(authToken, {
        userLimit: ADMIN_TABLE_PAGE_SIZE,
        userOffset: (userPage - 1) * ADMIN_TABLE_PAGE_SIZE,
        logLimit: ADMIN_TABLE_PAGE_SIZE,
        logOffset: (logPage - 1) * ADMIN_TABLE_PAGE_SIZE,
        signal: controller.signal,
      });
      if (runId !== adminLoadRunRef.current) {
        return;
      }
      const maxUserPage = Math.max(1, Math.ceil(dashboard.users.count / ADMIN_TABLE_PAGE_SIZE));
      const maxLogPage = Math.max(1, Math.ceil(dashboard.query_logs.count / ADMIN_TABLE_PAGE_SIZE));
      if (userPage > maxUserPage) {
        setAdminUserPage(maxUserPage);
        return;
      }
      if (logPage > maxLogPage) {
        setAdminLogPage(maxLogPage);
        return;
      }
      setRuntimeStatus(dashboard.runtime_status);
      setAdminMetrics(dashboard.metrics);
      setMetadataOverview(dashboard.metadata_overview);
      setAdminUsers(dashboard.users.users);
      setAdminUserCount(dashboard.users.count);
      setAdminRoles(dashboard.roles);
      setAdminLogs(dashboard.query_logs.query_logs);
      setAdminLogCount(dashboard.query_logs.count);
      setAdminFeedbackSummary(dashboard.feedback_summary);
      setAdminEvalSummary(dashboard.evaluation_summary);
      setAdminSessions(dashboard.runtime_sessions.sessions);
      setAdminSessionCount(dashboard.runtime_sessions.count);
      setAdminError(formatAdminDashboardSectionErrors(dashboard.section_errors));
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setAdminError(errorMessage(error));
    } finally {
      if (runId === adminLoadRunRef.current) {
        setAdminPending(false);
      }
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
  const resultRowCount = inspectorResponse?.execution?.row_count ?? inspectorSqlAudit?.row_count ?? 0;
  const workspaceHeading = buildWorkspaceHeading({
    domain: workspaceDomain,
    response: inspectorResponse,
    queryLog: activeTraceArtifact?.query_log || latestQueryLogs[0] || null,
    sessionTitle: selectedSession?.title,
    fallbackTitle: shouldShowWelcome ? "新建会话" : "工作台",
  });

  const isAdmin = (currentUser?.roles || []).includes("admin");
  const showAdminCenter = isAdmin && viewMode === "admin";
  const showInspector = viewMode === "workspace" && !shouldShowWelcome;

  if (authInitializing || (token && !currentUser)) {
    return (
      <main className="auth-restore-screen">
        <div className="auth-restore-panel">
          <QueryMindLogo className="auth-restore-logo" />
          <div>
            <div className="auth-restore-title">正在恢复登录状态</div>
            <div className="auth-restore-copy">
              {workspaceError || authError || "正在校验本地会话，请稍候。"}
            </div>
          </div>
          {!authInitializing && (workspaceError || authError) ? (
            <div className="auth-restore-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  setAuthError("");
                  setWorkspaceError("");
                  void boot();
                }}
              >
                重试
              </button>
              <button className="primary-button" type="button" onClick={clearAuth}>
                返回登录
              </button>
            </div>
          ) : null}
        </div>
      </main>
    );
  }

  if (!currentUser) {
    return (
      <Login
        authMode={authMode}
        authError={authError}
        authPending={authPending}
        themeMode={themeMode}
        onThemeToggle={toggleThemeMode}
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
        <div className="mobile-title">{showAdminCenter ? "管理台" : "QueryMind"}</div>
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
          <div className="brand-panel">
            <div className="brand-lockup">
              <QueryMindLogo className="brand-mark" />
              <div className="brand-copy-block">
                <div className="brand-name">QueryMind</div>
                <div className="brand-meta">问 数 大 脑</div>
              </div>
            </div>
          </div>

          <div className={`view-switch${isAdmin ? "" : " is-single"}`}>
            <button
              className={`view-switch-button${viewMode === "workspace" ? " is-active" : ""}`}
              type="button"
              onClick={() => setViewMode("workspace")}
            >
              <AppIcon name="home" />
              用户工作台
            </button>
            {isAdmin ? (
              <button
                className={`view-switch-button${viewMode === "admin" ? " is-active" : ""}`}
                type="button"
                onClick={() => setViewMode("admin")}
              >
                <AppIcon name="settings" />
                管理中心
              </button>
            ) : null}
          </div>

          {showAdminCenter ? (
            <div className="sidebar-panel admin-sidebar-panel">
              <div className="panel-row">
                <div className="panel-title">管理导航</div>
                <div className="section-count">{ADMIN_SIDEBAR_LINKS.length}</div>
              </div>

              <nav className="admin-sidebar-nav" aria-label="管理中心导航">
                {ADMIN_SIDEBAR_LINKS.map((item) => (
                  <a key={item.href} href={item.href} onClick={() => setSidebarOpen(false)}>
                    <AppIcon name={item.icon} />
                    <span>{item.label}</span>
                  </a>
                ))}
              </nav>

              <div className="admin-sidebar-overview">
                <div className="panel-title">数据概览</div>
                <div className="admin-sidebar-stats">
                  <div>
                    <span>用户</span>
                    <strong>{String(adminUserCount)}</strong>
                  </div>
                  <div>
                    <span>会话</span>
                    <strong>{String(adminSessionCount)}</strong>
                  </div>
                  <div>
                    <span>日志</span>
                    <strong>{String(adminLogCount)}</strong>
                  </div>
                  <div>
                    <span>反馈</span>
                    <strong>{String(adminMetrics?.feedbacks.total ?? adminFeedbackSummary?.total ?? 0)}</strong>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <>
              <button className="primary-button new-session-button" type="button" onClick={() => void createSession()} disabled={chatPending}>
                <AppIcon name="plus" />
                新建会话
              </button>

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
                    <div className="empty-card session-empty-card">
                      <AppIcon name="chat" />
                      <strong>暂无会话记录</strong>
                      <span>开始提问，探索你的数据洞察</span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          <div className="user-panel">
            <div className="user-head">
              <div className="user-avatar">{(currentUser.username || currentUser.user_id).slice(0, 1).toUpperCase()}</div>
              <div>
                <div className="user-name">{currentUser.username || currentUser.user_id}</div>
                <div className="user-meta">{(currentUser.roles || []).includes("admin") ? "超级管理员" : ((currentUser.roles || []).join(", ") || "viewer")}</div>
              </div>
            </div>
            <button className="logout-button" type="button" onClick={clearAuth} aria-label="退出登录" title="退出登录">
              <AppIcon name="logout" />
            </button>
          </div>
        </aside>

        {showAdminCenter ? (
          <main className="main-column admin-main">
            <AdminView
              pending={adminPending}
              error={adminError}
              runtimeStatus={runtimeStatus}
              adminMetrics={adminMetrics}
              metadataOverview={metadataOverview}
              adminUsers={adminUsers}
              adminUserCount={adminUserCount}
              adminUserPage={adminUserPage}
              adminSessions={adminSessions}
              adminSessionCount={adminSessionCount}
              adminRoles={adminRoles}
              adminLogs={adminLogs}
              adminLogCount={adminLogCount}
              adminLogPage={adminLogPage}
              currentUserId={currentUser.user_id}
              feedbackSummary={adminFeedbackSummary}
              evaluationSummary={adminEvalSummary}
              replayPendingTraceId={adminReplayPendingTraceId}
              replayResult={adminReplayResult}
              indexActionPending={adminIndexActionPending}
              indexActionMessage={adminIndexActionMessage}
              themeMode={themeMode}
              token={token || ""}
              onAuthFailure={clearAuth}
              userForm={userForm}
              onUserFormChange={setUserForm}
              onThemeToggle={toggleThemeMode}
              onSaveUser={() => void handleAdminUserSave()}
              onToggleUser={(user) => void handleAdminToggleUser(user)}
              onResetPassword={(user) => void handleAdminResetPassword(user)}
              onDeleteUser={(user) => void handleAdminDeleteUser(user)}
              onUserPageChange={setAdminUserPage}
              onLogPageChange={setAdminLogPage}
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
                  <div className="workspace-breadcrumb">
                    <AppIcon name="home" />
                    <strong>工作台</strong>
                  </div>

                  <div className="workspace-actions">
                    <button className="toolbar-button" type="button" onClick={() => token && void initializeWorkspace(token)}>
                      <AppIcon name="refresh" />
                      刷新
                    </button>
                    <button
                      className={`toolbar-button theme-toggle-button${themeMode === "dark" ? " is-active" : ""}`}
                      type="button"
                      onClick={toggleThemeMode}
                      title="切换主题"
                    >
                      <AppIcon name={themeMode === "dark" ? "moon" : "sun"} />
                      {themeMode === "dark" ? "深色模式" : "浅色模式"}
                    </button>
                  </div>
                </div>

              </section>

              {!shouldShowWelcome ? (
                <section className="workspace-session-summary">
                  <h1 title={workspaceHeading.title}>{workspaceHeading.title}</h1>
                  {workspaceHeading.subtitle ? (
                    <div className="workspace-session-subtitle" title={workspaceHeading.subtitle}>
                      {workspaceHeading.subtitle}
                    </div>
                  ) : null}
                  <div className="toolbar-stats workspace-toolbar-stats">
                    <span className="toolbar-stat">
                      <AppIcon name="database" />
                      数据域: <strong>{workspaceDomain || "-"}</strong>
                    </span>
                    <span className="toolbar-stat">
                      <AppIcon name="chat" />
                      会话数: <strong>{String(displayMessages.length)}</strong>
                    </span>
                    <span className="toolbar-stat">
                      <AppIcon name="table" />
                      结果行数: <strong>{String(resultRowCount)}</strong>
                    </span>
                    <span className="toolbar-stat">
                      <AppIcon name="clock" />
                      更新于 <strong>{selectedSession ? formatDate(selectedSession.updated_at) : "-"}</strong>
                    </span>
                  </div>
                  {workspaceError ? <div className="workspace-toolbar-meta is-error">{workspaceError}</div> : null}
                </section>
              ) : null}

              <section className="conversation-panel">
                <div className="thread-scroll" ref={threadRef}>
                  {shouldShowWelcome ? (
                    <div className="welcome-shell">
                      <div className="welcome-hero">
                        <h1>
                          欢迎使用 <span>QueryMind</span>
                        </h1>
                        <p>用自然语言描述你的业务问题，无需编写 SQL，即可快速获取洞察与分析。</p>
                        <img className="workspace-illustration" src={workspaceIllustration} alt="" aria-hidden="true" />
                      </div>

                      <div className="welcome-prompt-title">
                        <AppIcon name="spark" />
                        <span>试试这些示例问题，快速开始</span>
                      </div>

                      <div className="prompt-grid">
                        {PROMPTS.map((prompt) => (
                          <button key={prompt} className="prompt-card" type="button" onClick={() => void handleSend(prompt)}>
                            <AppIcon name={prompt.includes("区域") ? "location" : prompt.includes("客户") ? "users" : "trend"} />
                            <span className="prompt-card-title">{prompt}</span>
                          </button>
                        ))}
                      </div>

                      <div className="workspace-feature-strip">
                        {WORKSPACE_FEATURES.map((feature) => (
                          <div className="workspace-feature" key={feature.title}>
                            <div className="workspace-feature-icon">
                              <AppIcon name={feature.icon} />
                            </div>
                            <div>
                              <div className="workspace-feature-title">{feature.title}</div>
                              <div className="workspace-feature-copy">{feature.description}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="thread-list">
                      {displayMessages.map((message) => {
                        const messageArtifact = message.trace_id ? findTraceArtifact(traceArtifacts, message.trace_id) : null;
                        const hasAssistantResult = message.role === "assistant" && Boolean(messageArtifact);
                        return (
                          <article key={message.id} className={`message${message.role === "user" ? " is-user" : ""}`}>
                            <div className="message-avatar">{message.role === "user" ? <AppIcon name="user" /> : <QueryMindLogo className="message-logo" />}</div>
                            <div className="message-body">
                              <div className="message-meta">
                                <span>{message.role === "user" ? "你" : "QueryMind"}</span>
                                <span>{formatDate(message.created_at)}</span>
                              </div>
                              {!hasAssistantResult ? <div className="message-card">{message.content}</div> : null}
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
                      placeholder="输入你的业务问题，例如：本月销售额相比上月增长了多少？"
                    />

                    <div className="composer-footer">
                      <div className="composer-hints">
                        <span className="hint-chip">
                          <AppIcon name="database" />
                          数据分析
                        </span>
                        <span className="hint-chip">
                          <AppIcon name="bolt" />
                          快捷指令
                        </span>
                      </div>

                      <div className="composer-submit-row">
                        <span>Enter 发送，Shift + Enter 换行</span>
                        <button className="send-button" type="submit" disabled={chatPending}>
                          <AppIcon name="send" />
                          {chatPending ? "处理中" : "发送"}
                        </button>
                      </div>
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
                      {workspaceDomain || "-"}
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


function AdminView(props: {
  pending: boolean;
  error: string;
  runtimeStatus: RuntimeStatus | null;
  adminMetrics: AdminMetricsSummary | null;
  metadataOverview: MetadataOverview | null;
  adminUsers: AdminUserRecord[];
  adminUserCount: number;
  adminUserPage: number;
  adminSessions: ChatSession[];
  adminSessionCount: number;
  adminRoles: RoleRecord[];
  adminLogs: RuntimeQueryLogRecord[];
  adminLogCount: number;
  adminLogPage: number;
  currentUserId: string;
  feedbackSummary: FeedbackSummary | null;
  evaluationSummary: EvaluationSummary | null;
  replayPendingTraceId: string | null;
  replayResult: EvaluationReplayResult | null;
  indexActionPending: "" | "reload" | "prewarm" | "reload_prewarm";
  indexActionMessage: string;
  themeMode: ThemeMode;
  token: string;
  onAuthFailure: () => void;
  userForm: UserUpsertPayload;
  onUserFormChange: (value: UserUpsertPayload) => void;
  onThemeToggle: () => void;
  onSaveUser: () => void;
  onToggleUser: (user: UserContext) => void;
  onResetPassword: (user: UserContext) => void;
  onDeleteUser: (user: UserContext) => void;
  onUserPageChange: (page: number) => void;
  onLogPageChange: (page: number) => void;
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
  const healthyRuntimeCount = runtimeEntries.filter(([, value]) => {
    const normalized = value.toLowerCase();
    return normalized.includes("已") || normalized.includes("就绪") || normalized.includes("连接") || normalized.includes("ok");
  }).length;
  const healthPercent = runtimeEntries.length ? Math.round((healthyRuntimeCount / runtimeEntries.length) * 100) : 0;
  const userDelta = formatMetricDelta(props.adminMetrics?.users.delta);
  const sessionDelta = formatMetricDelta(props.adminMetrics?.sessions.delta);
  const queryLogDelta = formatMetricDelta(props.adminMetrics?.query_logs.delta);
  const feedbackDelta = formatMetricDelta(props.adminMetrics?.feedbacks.delta);
  const adminMetricCards = [
    {
      icon: "users",
      title: "用户总数",
      value: String(props.adminMetrics?.users.total ?? props.adminUserCount),
      note: "较昨日",
      delta: userDelta.text,
      deltaTone: userDelta.tone,
      tone: "blue",
    },
    {
      icon: "chat",
      title: "运行会话",
      value: String(props.adminMetrics?.sessions.total ?? props.adminSessionCount),
      note: "较昨日",
      delta: sessionDelta.text,
      deltaTone: sessionDelta.tone,
      tone: "purple",
    },
    {
      icon: "document",
      title: "查询日志",
      value: String(props.adminMetrics?.query_logs.total ?? props.adminLogCount),
      note: "较昨日",
      delta: queryLogDelta.text,
      deltaTone: queryLogDelta.tone,
      tone: "blue",
    },
    {
      icon: "feedback",
      title: "反馈",
      value: String(props.adminMetrics?.feedbacks.total ?? props.feedbackSummary?.total ?? 0),
      note: "较昨日",
      delta: feedbackDelta.text,
      deltaTone: feedbackDelta.tone,
      tone: "orange",
    },
    {
      icon: "heart",
      title: "系统健康",
      value: `${healthPercent || 100}%`,
      note: "状态良好",
      delta: `${healthyRuntimeCount}/${runtimeEntries.length || 6}`,
      deltaTone: "status",
      tone: "green",
    },
  ];
  const visibleUsers = props.adminUsers;
  const visibleLogs = props.adminLogs;

  return (
    <div className="admin-dashboard">
      <section className="admin-page-head">
        <div>
          <div className="admin-page-badge">管理中心</div>
          <h1>系统监控与用户管理</h1>
          <p>统一管理数据源、模型能力、用户权限、查询日志与系统运行状态，保障企业数据分析安全、稳定、可审计。</p>
        </div>

        <div className="admin-head-actions">
          <button className="toolbar-button" type="button" onClick={props.onRefresh} disabled={props.pending}>
            <AppIcon name="refresh" />
            {props.pending ? "刷新中" : "刷新数据"}
          </button>
          <button
            className={`toolbar-button theme-toggle-button${props.themeMode === "dark" ? " is-active" : ""}`}
            type="button"
            onClick={props.onThemeToggle}
            title="切换主题"
          >
            <AppIcon name={props.themeMode === "dark" ? "moon" : "sun"} />
            {props.themeMode === "dark" ? "深色模式" : "浅色模式"}
          </button>
        </div>
      </section>

      {props.error ? <div className="detail-card accent-card">{props.error}</div> : null}

      <section id="admin-overview" className="admin-metric-grid">
        {adminMetricCards.map((metric) => (
          <article className={`admin-metric-card is-${metric.tone}`} key={metric.title}>
            <div className="admin-metric-icon">
              <AppIcon name={metric.icon} />
            </div>
            <div>
              <div className="admin-metric-title">{metric.title}</div>
              <div className="admin-metric-value">{metric.value}</div>
              <div className="admin-metric-note">
                <span>{metric.note}</span>
                <strong className={`is-${metric.deltaTone}`}>{metric.delta}</strong>
              </div>
            </div>
          </article>
        ))}
      </section>

      <section className="admin-overview-grid">
        <article id="admin-runtime" className="admin-panel admin-runtime-panel">
          <div className="admin-panel-title">
            <AppIcon name="server" />
            运行状态
          </div>
          <div className="admin-status-list">
            {runtimeEntries.length ? (
              runtimeEntries.map(([label, value]) => (
                <div className="admin-status-row" key={label}>
                  <span>{label}</span>
                  <strong className={value.includes("错误") || value.includes("失败") ? "is-danger" : "is-ok"}>
                    {value}
                  </strong>
                </div>
              ))
            ) : (
              <div className="empty-card subtle-card">暂无运行状态数据。</div>
            )}
          </div>
        </article>

        <article id="admin-metadata" className="admin-panel">
          <div className="admin-panel-title">
            <AppIcon name="pie" />
            元数据概览
          </div>
          <div className="admin-meta-table">
            <span>语义版本</span>
            <strong>{props.metadataOverview?.semantic_version || "-"}</strong>
            <span>业务域数</span>
            <strong>{String(props.metadataOverview?.semantic_domains.length || 0)}</strong>
            <span>物理表数</span>
            <strong>{String(props.metadataOverview?.table_count || 0)}</strong>
            <span>示例数</span>
            <strong>{String(props.metadataOverview?.example_count || 0)}</strong>
            <span>Trace 数</span>
            <strong>{String(props.metadataOverview?.trace_count || 0)}</strong>
          </div>
        </article>

        <article id="admin-index" className="admin-panel admin-index-panel">
          <div className="admin-panel-head">
            <div className="admin-panel-title">
              <AppIcon name="search" />
              检索索引
            </div>
            <div className="admin-inline-actions">
              <button className="secondary-button" type="button" onClick={props.onReloadMetadata} disabled={Boolean(props.indexActionPending)}>
                重载元数据
              </button>
              <button className="secondary-button" type="button" onClick={props.onPrewarmVector} disabled={Boolean(props.indexActionPending)}>
                重建向量索引
              </button>
              <button className="primary-button" type="button" onClick={props.onReloadAndPrewarm} disabled={Boolean(props.indexActionPending)}>
                重载并重建
              </button>
            </div>
          </div>

          <div className="admin-meta-table">
            <span>Provider</span>
            <strong>{vectorStatus?.provider || "-"}</strong>
            <span>模型</span>
            <strong>{vectorStatus?.model || "-"}</strong>
            <span>状态</span>
            <strong>{describeVectorWarmStatus(vectorStatus)}</strong>
            <span>已索引文档</span>
            <strong>{String(vectorStatus?.indexed_document_count ?? 0)}</strong>
            <span>当前语料文档</span>
            <strong>{String(retrievalCorpusStatus?.document_count ?? 0)}</strong>
            <span>待重建</span>
            <strong>{describePendingRebuild(vectorSyncStatus?.pending_rebuild)}</strong>
            <span>上次同步</span>
            <strong>{formatDate(vectorSyncStatus?.vector_sync_last_updated_at) || "-"}</strong>
          </div>
          {props.indexActionMessage ? <div className="detail-copy admin-status-message">{props.indexActionMessage}</div> : null}
        </article>
      </section>

      <section className="admin-bottom-grid">
        <article id="admin-users" className="admin-panel admin-users-panel">
          <div className="admin-panel-title">
            <AppIcon name="users" />
            用户管理
          </div>

          <div className="admin-user-create-row">
            <input
              value={props.userForm.username}
              onChange={(event) => props.onUserFormChange({ ...props.userForm, username: event.target.value })}
              placeholder="用户名"
            />
            <input
              type="password"
              value={props.userForm.password || ""}
              onChange={(event) => props.onUserFormChange({ ...props.userForm, password: event.target.value })}
              placeholder="密码"
            />
            <input
              value={props.userForm.roles.join(", ")}
              onChange={(event) =>
                props.onUserFormChange({
                  ...props.userForm,
                  roles: event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                })
              }
              placeholder="角色"
            />
            <button className="primary-button" type="button" onClick={props.onSaveUser}>
              保存用户
            </button>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-data-table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>用户 ID</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleUsers.length ? (
                  visibleUsers.map((user) => {
                    const isCurrentUser = user.user_id === props.currentUserId;
                    const isAdminAccount = (user.roles || []).includes("admin");
                    const deleteDisabledReason = isAdminAccount
                      ? "管理员账号不能删除"
                      : isCurrentUser
                        ? "不能删除当前登录用户"
                        : undefined;
                    return (
                      <tr key={user.user_id}>
                        <td>{user.username || user.user_id}</td>
                        <td>{formatUserRoles(user.roles)}</td>
                        <td><span className={`admin-status-chip${user.is_active ? " is-ok" : ""}`}>{user.is_active ? "活跃" : "离线"}</span></td>
                        <td className="admin-cell-muted">{user.user_id}</td>
                        <td>{formatDate(user.updated_at || user.created_at)}</td>
                        <td>
                          <div className="admin-row-actions">
                            <button type="button" onClick={() => props.onResetPassword(user)} aria-label="重置密码">•••</button>
                            <button
                              type="button"
                              disabled={isCurrentUser}
                              onClick={() => props.onToggleUser(user)}
                              title={isCurrentUser ? "不能禁用当前登录用户" : undefined}
                            >
                              {user.is_active ? "禁用" : "启用"}
                            </button>
                            <button
                              className="is-danger"
                              type="button"
                              disabled={Boolean(deleteDisabledReason)}
                              onClick={() => props.onDeleteUser(user)}
                              title={deleteDisabledReason}
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={6}>暂无用户。</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <PaginationControls
            className="admin-table-foot"
            page={props.adminUserPage}
            pageSize={ADMIN_TABLE_PAGE_SIZE}
            total={props.adminUserCount}
            currentCount={visibleUsers.length}
            itemLabel="人"
            disabled={props.pending}
            onPageChange={props.onUserPageChange}
          />
        </article>

        <article id="admin-logs" className="admin-panel admin-logs-panel">
          <div className="admin-panel-title">
            <AppIcon name="document" />
            日志 / 审计
          </div>

          <div className="admin-table-wrap">
            <table className="admin-data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>用户</th>
                  <th>类型</th>
                  <th>描述</th>
                </tr>
              </thead>
              <tbody>
                {visibleLogs.length ? (
                  visibleLogs.map((log) => (
                    <tr key={log.trace_id}>
                      <td>{formatDate(log.created_at)}</td>
                      <td>{log.user_id || "system"}</td>
                      <td><span className="admin-type-chip">{describeResponseStatus(log.answer_status || "查询")}</span></td>
                      <td>{log.question || `Trace ${log.trace_id}`}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4}>暂无查询日志。</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
	          <PaginationControls
	            className="admin-table-foot"
	            page={props.adminLogPage}
	            pageSize={ADMIN_TABLE_PAGE_SIZE}
	            total={props.adminLogCount}
	            currentCount={visibleLogs.length}
	            itemLabel="条"
	            disabled={props.pending}
	            onPageChange={props.onLogPageChange}
	          />

          {props.replayResult ? (
            <div className="admin-replay-panel">
              <div className="panel-row">
                <div>
                  <div className="detail-title">复跑结果</div>
                  <div className="admin-item-meta">{props.replayResult.question}</div>
                </div>
                <div className="mini-tags">
                  <span className="mini-tag">{describeResponseStatus(replayAnswer?.status || "unknown")}</span>
                  <span className="mini-tag">{String(replayExecution?.row_count ?? 0)} rows</span>
                </div>
              </div>
            </div>
          ) : null}
        </article>
      </section>

      <section id="admin-semantic" className="admin-semantic-section">
        <SemanticStudio token={props.token} onAuthFailure={props.onAuthFailure} />
      </section>

      <section id="admin-settings" className="admin-semantic-section">
        <SystemSettings token={props.token} onAuthFailure={props.onAuthFailure} />
      </section>
    </div>
  );

}

function PaginationControls(props: {
  className?: string;
  page: number;
  pageSize: number;
  total: number;
  currentCount: number;
  itemLabel: string;
  disabled?: boolean;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(props.total / props.pageSize));
  const page = Math.min(Math.max(1, props.page), totalPages);
  const start = props.total === 0 ? 0 : (page - 1) * props.pageSize + 1;
  const end = props.total === 0 ? 0 : start + props.currentCount - 1;
  return (
    <div className={props.className || "pagination-controls"}>
      <span>
        {start}-{end} / 共 {props.total} {props.itemLabel}
      </span>
      <div className="admin-pagination">
        <button
          type="button"
          disabled={props.disabled || page <= 1}
          onClick={() => props.onPageChange(page - 1)}
        >
          上一页
        </button>
        <strong>{page} / {totalPages}</strong>
        <button
          type="button"
          disabled={props.disabled || page >= totalPages}
          onClick={() => props.onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </div>
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

function QueryMindLogo(props: { className?: string }) {
  return (
    <svg className={props.className} viewBox="0 0 58 58" aria-hidden="true">
      <defs>
        <linearGradient id="app-logo-main" x1="11" y1="7" x2="49" y2="51" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6DA0FF" />
          <stop offset="0.55" stopColor="#4D78FF" />
          <stop offset="1" stopColor="#6A54F4" />
        </linearGradient>
      </defs>
      <path d="M29 4L51 16.5V41.5L29 54L7 41.5V16.5L29 4Z" fill="url(#app-logo-main)" />
      <path d="M29 14L42 21.5V36.5L29 44L16 36.5V21.5L29 14Z" fill="#F9FBFF" fillOpacity="0.95" />
      <path d="M29 21L36 25V33L29 37L22 33V25L29 21Z" fill="url(#app-logo-main)" />
      <path d="M39 38.5L50 45L40.7 50.3L30 44.1L39 38.5Z" fill="#5B4DF0" fillOpacity="0.95" />
    </svg>
  );
}

function AppIcon(props: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (props.name) {
    case "home":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M4 10.5L12 4L20 10.5V20H6.5V13H17.5V20" />
        </svg>
      );
    case "settings":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M12 15.5A3.5 3.5 0 1 0 12 8.5A3.5 3.5 0 0 0 12 15.5Z" />
          <path {...common} d="M19.4 15A8.3 8.3 0 0 0 20 12L22 10.5L20 7L17.6 8A8.3 8.3 0 0 0 15 6.5L14.6 4H9.4L9 6.5A8.3 8.3 0 0 0 6.4 8L4 7L2 10.5L4 12A8.3 8.3 0 0 0 4.6 15L3.2 17.2L6.8 19.2L8.7 17.6A8.3 8.3 0 0 0 12 18.3A8.3 8.3 0 0 0 15.3 17.6L17.2 19.2L20.8 17.2L19.4 15Z" />
        </svg>
      );
    case "briefcase":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect {...common} x="4" y="7" width="16" height="12" rx="2" />
          <path {...common} d="M9 7V5.5C9 4.7 9.7 4 10.5 4H13.5C14.3 4 15 4.7 15 5.5V7M4 12H20" />
        </svg>
      );
    case "plus":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M12 5V19M5 12H19" />
        </svg>
      );
    case "refresh":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M20 12A8 8 0 0 1 6.6 17.9M4 12A8 8 0 0 1 17.4 6.1" />
          <path {...common} d="M17 2V6.5H21.5M7 21.5V17H2.5" />
        </svg>
      );
    case "sun":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="12" cy="12" r="4" />
          <path {...common} d="M12 2.5V5M12 19V21.5M21.5 12H19M5 12H2.5M18.7 5.3L16.9 7.1M7.1 16.9L5.3 18.7M18.7 18.7L16.9 16.9M7.1 7.1L5.3 5.3" />
        </svg>
      );
    case "moon":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M20 15.2A7.8 7.8 0 0 1 8.8 4A8.5 8.5 0 1 0 20 15.2Z" />
        </svg>
      );
    case "help":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="12" cy="12" r="9" />
          <path {...common} d="M9.7 9.4C10 8 10.9 7.2 12.4 7.2C14.1 7.2 15.2 8.2 15.2 9.7C15.2 11 14.4 11.8 13.2 12.4C12.3 12.9 12 13.4 12 14.4" />
          <path d="M12 18H12.01" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
      );
    case "database":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <ellipse {...common} cx="12" cy="6" rx="7" ry="3" />
          <path {...common} d="M5 6V18C5 19.7 8.1 21 12 21C15.9 21 19 19.7 19 18V6M5 12C5 13.7 8.1 15 12 15C15.9 15 19 13.7 19 12" />
        </svg>
      );
    case "server":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect {...common} x="4" y="5" width="16" height="6" rx="2" />
          <rect {...common} x="4" y="13" width="16" height="6" rx="2" />
          <path {...common} d="M8 8H8.01M8 16H8.01" />
        </svg>
      );
    case "pie":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M12 3V12H21C21 7 17 3 12 3Z" />
          <path {...common} d="M12 12V3A9 9 0 1 0 21 12H12Z" />
        </svg>
      );
    case "search":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="11" cy="11" r="6" />
          <path {...common} d="M16 16L21 21" />
        </svg>
      );
    case "trend":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M4 17L9 12L13 15L20 7" />
          <path {...common} d="M14 7H20V13" />
        </svg>
      );
    case "location":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M12 21S18 15.6 18 10A6 6 0 1 0 6 10C6 15.6 12 21 12 21Z" />
          <circle {...common} cx="12" cy="10" r="2" />
        </svg>
      );
    case "users":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M16 20C15.4 17.7 13.8 16.5 12 16.5C10.2 16.5 8.6 17.7 8 20M12 13A3 3 0 1 0 12 7A3 3 0 0 0 12 13Z" />
          <path {...common} d="M4.5 18C4.9 16.4 6 15.5 7.4 15.3M16.6 15.3C18 15.5 19.1 16.4 19.5 18M7.5 12.2A2.2 2.2 0 1 1 7.5 7.8M16.5 12.2A2.2 2.2 0 1 0 16.5 7.8" />
        </svg>
      );
    case "chat":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M5 6.5H19V16H12L8 19.5V16H5V6.5Z" />
          <path {...common} d="M8.5 11H8.51M12 11H12.01M15.5 11H15.51" />
        </svg>
      );
    case "document":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M7 3H14L19 8V21H7V3Z" />
          <path {...common} d="M14 3V8H19M10 13H16M10 17H16" />
        </svg>
      );
    case "feedback":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M7 7H17V15H12L8.5 18V15H7V7Z" />
          <path {...common} d="M10 10.5H10.01M12 10.5H12.01M14 10.5H14.01" />
        </svg>
      );
    case "heart":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 20S5 15.8 5 10.2C5 7.8 6.8 6 9 6C10.3 6 11.3 6.7 12 7.8C12.7 6.7 13.7 6 15 6C17.2 6 19 7.8 19 10.2C19 15.8 12 20 12 20Z" fill="currentColor" />
        </svg>
      );
    case "spark":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 3L14.6 9.4L21 12L14.6 14.6L12 21L9.4 14.6L3 12L9.4 9.4L12 3Z" fill="currentColor" />
        </svg>
      );
    case "table":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect {...common} x="4" y="5" width="16" height="14" rx="2" />
          <path {...common} d="M4 10H20M9 5V19M15 5V19" />
        </svg>
      );
    case "clock":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="12" cy="12" r="8" />
          <path {...common} d="M12 8V12L15 14" />
        </svg>
      );
    case "user":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="12" cy="8.5" r="3.5" />
          <path {...common} d="M5.5 20C6.4 16.8 8.8 15 12 15C15.2 15 17.6 16.8 18.5 20" />
        </svg>
      );
    case "bolt":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M13 2L5 13H11L10 22L19 10H13L13 2Z" fill="currentColor" />
        </svg>
      );
    case "send":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M21 3L10 14" />
          <path {...common} d="M21 3L14 21L10 14L3 10L21 3Z" />
        </svg>
      );
    case "panel":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect {...common} x="6" y="5" width="12" height="14" rx="2" />
          <path {...common} d="M9 8H15M9 12H15M9 16H13" />
        </svg>
      );
    case "download":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M12 4V15M8 11L12 15L16 11M5 20H19" />
        </svg>
      );
    case "chevron":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M7 10L12 15L17 10" />
        </svg>
      );
    case "logout":
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path {...common} d="M10 6H6V18H10" />
          <path {...common} d="M14 8L18 12L14 16" />
          <path {...common} d="M18 12H9" />
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle {...common} cx="12" cy="12" r="8" />
        </svg>
      );
  }
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
      <div className="message-result-layout">
        <QueryMindLogo className="result-logo" />
        <div className="message-result-main">
          <div className="message-result-head">
            <div className="message-result-answer">
              {answer?.summary || answer?.detail || "已完成本次查询。"}
            </div>
            <div className="message-result-actions">
              {props.canInspect ? (
                <button className="icon-button" type="button" onClick={props.onSelect} aria-label="查看详情">
                  <AppIcon name="panel" />
                </button>
              ) : null}
              {canDownload ? (
                <button
                  className="icon-button"
                  type="button"
                  onClick={() => {
                    void downloadTraceCsv(props.token!, props.artifact.trace!.trace_id);
                  }}
                  aria-label="下载结果"
                >
                  <AppIcon name="download" />
                </button>
              ) : null}
            </div>
          </div>

          {answer?.detail && answer.detail !== answer.summary ? <div className="message-result-note">{answer.detail}</div> : null}

          <div className="message-result-summary">
            <span>{domain || "-"}</span>
            <span>{describeResponseStatus(status)}</span>
            <span>{showRowCount ? `${rowCount} 行结果` : "未进入 SQL"}</span>
          </div>

          {resultRows.length ? (
            <>
              <div className="message-result-section-title">查询结果</div>
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
            </>
          ) : null}

          {answer?.follow_up_hint ? <div className="message-result-note">下一步：{answer.follow_up_hint}</div> : null}
          <div className="message-result-footer">{formatDate(props.artifact.trace?.created_at || queryLog?.created_at)}</div>
        </div>
      </div>
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

function buildWorkspaceHeading(input: {
  domain?: string | null;
  response?: ChatResponse | null;
  queryLog?: RuntimeQueryLogRecord | null;
  sessionTitle?: string | null;
  fallbackTitle: string;
}) {
  const domainLabel = formatDomainLabel(input.domain);
  const title = domainLabel ? `${domainLabel}分析` : input.fallbackTitle;
  const subtitleCandidates = [
    input.response?.question_context?.semantic_brief,
    input.response?.context_summary?.semantic_brief,
    input.queryLog?.semantic_brief,
    input.response?.question_context?.effective_question,
    input.queryLog?.effective_question,
    input.queryLog?.question,
    input.sessionTitle,
  ];
  const subtitle = firstChineseText(subtitleCandidates) || firstText(subtitleCandidates);
  return {
    title,
    subtitle: subtitle && subtitle !== title ? subtitle : "",
  };
}

function formatDomainLabel(domain?: string | null) {
  const normalized = (domain || "").trim();
  const labels: Record<string, string> = {
    demand: "需求",
    inventory: "库存",
    plan_actual: "计划实绩",
    sales_financial: "销售财务",
    dimension: "维度",
  };
  return labels[normalized] || normalized;
}

function firstText(values: Array<string | null | undefined>) {
  for (const value of values) {
    const normalized = (value || "").trim();
    if (normalized) {
      return normalized;
    }
  }
  return "";
}

function firstChineseText(values: Array<string | null | undefined>) {
  for (const value of values) {
    const normalized = (value || "").trim();
    if (normalized && containsChineseText(normalized)) {
      return normalized;
    }
  }
  return "";
}

function containsChineseText(value: string) {
  return /[\u3400-\u9fff]/.test(value);
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
  return ["clarification_needed", "invalid"].includes(normalized);
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
  if (value.ready) {
    return "已就绪";
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
  if (value.ready) {
    return "已就绪";
  }
  return "等待初始化";
}

function describePendingRebuild(value: boolean | null | undefined) {
  if (value == null) {
    return "-";
  }
  return value ? "是" : "否";
}

function formatUserRoles(roles: string[] | null | undefined) {
  const normalized = roles || [];
  if (normalized.includes("admin")) {
    return "超级管理员";
  }
  return normalized.join(", ") || "viewer";
}

function formatMetricDelta(delta: number | null | undefined): { text: string; tone: "positive" | "negative" | "flat" } {
  if (typeof delta !== "number" || !Number.isFinite(delta) || delta === 0) {
    return { text: "0 -", tone: "flat" };
  }
  return delta > 0
    ? { text: `+${delta} ↑`, tone: "positive" }
    : { text: `${delta} ↓`, tone: "negative" };
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

function formatAdminDashboardSectionErrors(sectionErrors?: Record<string, string>) {
  const entries = Object.entries(sectionErrors ?? {}).filter(([, message]) => Boolean(message));
  if (!entries.length) {
    return "";
  }
  return `部分管理数据加载失败：${entries
    .map(([key, message]) => `${ADMIN_DASHBOARD_SECTION_LABELS[key] ?? key} ${message}`)
    .join("；")}`;
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
