import { FormEvent, useEffect, useState } from "react";
import loginIllustration from "./assets/login-illustration.svg";
import "./login.css";

type AuthMode = "login" | "bootstrap";
type ThemeMode = "light" | "dark";

interface LoginProps {
  authMode: AuthMode;
  authError: string;
  authPending: boolean;
  themeMode: ThemeMode;
  onThemeToggle: () => void;
  onSubmit: (username: string, password: string) => Promise<void>;
}

const REMEMBER_ME_KEY = "text2sql.frontend.remember_username";

type FeatureIcon = "chat" | "trace" | "chart";

const features: Array<{ icon: FeatureIcon; title: string; description: string }> = [
  {
    icon: "chat",
    title: "自然语言问数",
    description: "像对话一样提问，快速获得业务结果",
  },
  {
    icon: "trace",
    title: "可追溯分析",
    description: "保留查询路径与执行依据，结果更可靠",
  },
  {
    icon: "chart",
    title: "结果洞察与解释",
    description: "辅助理解关键指标变化，支持业务决策",
  },
];

export function Login(props: LoginProps) {
  const [username, setUsername] = useState(() => {
    // 从 localStorage 恢复上次记住的用户名
    return window.localStorage.getItem(REMEMBER_ME_KEY) || "";
  });
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(() => {
    // 如果有保存的用户名，默认勾选记住我
    return Boolean(window.localStorage.getItem(REMEMBER_ME_KEY));
  });
  const [showForgotPassword, setShowForgotPassword] = useState(false);

  useEffect(() => {
    // 当 authMode 变化时重置表单
    if (props.authMode === "bootstrap") {
      setUsername("");
      setPassword("");
      setRememberMe(false);
    }
  }, [props.authMode]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    // 处理记住我功能
    if (rememberMe && username.trim()) {
      window.localStorage.setItem(REMEMBER_ME_KEY, username.trim());
    } else {
      window.localStorage.removeItem(REMEMBER_ME_KEY);
    }

    void props.onSubmit(username, password);
  };

  const submitLabel = props.authPending
    ? "登录中..."
    : props.authMode === "bootstrap"
      ? "创建并登录"
      : "登录";

  const isFormDisabled = props.authPending;

  return (
    <main className="login-page">
      <header className="login-header">
        <div className="login-brand" aria-label="QueryMind 问数大脑">
          <LogoMark />
          <div className="brand-copy">
            <div className="brand-title">QueryMind</div>
            <div className="brand-subtitle">问 数 大 脑</div>
          </div>
        </div>

        <nav className="header-actions" aria-label="登录页工具">
          <button
            className={`header-action theme-toggle-button${props.themeMode === "dark" ? " is-active" : ""}`}
            type="button"
            onClick={props.onThemeToggle}
            title="切换主题"
          >
            {props.themeMode === "dark" ? <MoonIcon /> : <SunIcon />}
            <span>{props.themeMode === "dark" ? "深色模式" : "浅色模式"}</span>
          </button>
        </nav>
      </header>

      <section className="login-shell">
        <section className="login-showcase" aria-label="产品介绍">
          <div className="login-badge">
            <SparkIcon />
            <span>企业级智能数据问答与分析平台</span>
          </div>

          <h1 className="login-title">
            让每一次业务提问，
            <br />
            都得到<span>可信</span>的数据答案
          </h1>

          <p className="login-description">
            用自然语言连接业务与数据，无需编写 SQL，
            <br />
            即可完成查询、分析、追踪与结果解释。
          </p>

          <div className="feature-list">
            {features.map((feature) => (
              <div className="feature-item" key={feature.title}>
                <div className={`feature-icon feature-icon-${feature.icon}`}>
                  <FeatureGlyph icon={feature.icon} />
                </div>
                <div>
                  <div className="feature-title">{feature.title}</div>
                  <div className="feature-description">{feature.description}</div>
                </div>
              </div>
            ))}
          </div>

          <img className="login-illustration" src={loginIllustration} alt="" aria-hidden="true" />
        </section>

        <section className="login-card" aria-label="登录表单">
          <h2>
            {props.authMode === "bootstrap" ? "创建管理员账户" : "欢迎回来"}
          </h2>
          <p>
            {props.authMode === "bootstrap"
              ? "首次使用，请设置管理员账户信息"
              : "登录 QueryMind 用户工作台"}
          </p>

          {props.authMode === "bootstrap" && (
            <div className="bootstrap-notice">
              <InfoIcon />
              <span>密码建议至少 8 位，包含字母和数字</span>
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="form-field">
              <label htmlFor="username">用户名</label>
              <div className={`input-shell${props.authError ? " has-error" : ""}`}>
                <UserIcon />
                <input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="请输入用户名"
                  autoComplete="username"
                  disabled={isFormDisabled}
                  required
                />
              </div>
            </div>

            <div className="form-field">
              <label htmlFor="password">密码</label>
              <div className={`input-shell${props.authError ? " has-error" : ""}`}>
                <LockIcon />
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="请输入密码"
                  autoComplete={props.authMode === "bootstrap" ? "new-password" : "current-password"}
                  disabled={isFormDisabled}
                  required
                />
                <button
                  className="password-toggle"
                  type="button"
                  onClick={() => setShowPassword((current) => !current)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  disabled={isFormDisabled}
                >
                  {showPassword ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
            </div>

            {props.authMode === "login" && (
              <div className="form-options">
                <label className="remember-option">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(event) => setRememberMe(event.target.checked)}
                    disabled={isFormDisabled}
                  />
                  <span>记住我</span>
                </label>
                <button
                  className="forgot-password"
                  type="button"
                  onClick={() => setShowForgotPassword(true)}
                  disabled={isFormDisabled}
                >
                  忘记密码？
                </button>
              </div>
            )}

            {props.authError && (
              <div className="form-error" role="alert">
                <AlertIcon />
                <span>{props.authError}</span>
              </div>
            )}

            <button className="login-submit" type="submit" disabled={isFormDisabled}>
              {props.authPending && <SpinnerIcon />}
              <span>{submitLabel}</span>
            </button>
          </form>
        </section>
      </section>

      {/* 忘记密码弹窗 */}
      {showForgotPassword && (
        <div className="modal-backdrop" onClick={() => setShowForgotPassword(false)}>
          <div className="forgot-password-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <h3>忘记密码</h3>
              <button
                className="modal-close"
                type="button"
                onClick={() => setShowForgotPassword(false)}
                aria-label="关闭"
              >
                <CloseIcon />
              </button>
            </div>
            <div className="modal-body">
              <p>请联系系统管理员重置您的密码。</p>
              <p className="modal-hint">管理员可以在管理中心的用户管理模块重置任何用户的密码。</p>
            </div>
            <div className="modal-footer">
              <button
                className="modal-button"
                type="button"
                onClick={() => setShowForgotPassword(false)}
              >
                我知道了
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function LogoMark() {
  return (
    <svg className="logo-mark" viewBox="0 0 58 58" aria-hidden="true">
      <defs>
        <linearGradient id="logo-main" x1="11" y1="7" x2="49" y2="51" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6DA0FF" />
          <stop offset="0.55" stopColor="#4D78FF" />
          <stop offset="1" stopColor="#6A54F4" />
        </linearGradient>
      </defs>
      <path d="M29 4L51 16.5V41.5L29 54L7 41.5V16.5L29 4Z" fill="url(#logo-main)" />
      <path d="M29 14L42 21.5V36.5L29 44L16 36.5V21.5L29 14Z" fill="#F9FBFF" fillOpacity="0.95" />
      <path d="M29 21L36 25V33L29 37L22 33V25L29 21Z" fill="url(#logo-main)" />
      <path d="M39 38.5L50 45L40.7 50.3L30 44.1L39 38.5Z" fill="#5B4DF0" fillOpacity="0.95" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M12 2.8V5.1M12 18.9V21.2M21.2 12H18.9M5.1 12H2.8M18.5 5.5L16.9 7.1M7.1 16.9L5.5 18.5M18.5 18.5L16.9 16.9M7.1 7.1L5.5 5.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 15.2A7.8 7.8 0 0 1 8.8 4A8.5 8.5 0 1 0 20 15.2Z" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12.4 3.2L14.7 9.3L20.8 11.6L14.7 13.9L12.4 20L10.1 13.9L4 11.6L10.1 9.3L12.4 3.2Z" fill="currentColor" />
      <path d="M5.6 3.8L6.5 6.2L8.9 7.1L6.5 8L5.6 10.4L4.7 8L2.3 7.1L4.7 6.2L5.6 3.8Z" fill="currentColor" opacity="0.68" />
    </svg>
  );
}

function FeatureGlyph({ icon }: { icon: FeatureIcon }) {
  if (icon === "chat") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6.2 6.6H17.8C19.4 6.6 20.5 7.7 20.5 9.2V14.1C20.5 15.6 19.4 16.7 17.8 16.7H12.5L8.8 20V16.7H6.2C4.6 16.7 3.5 15.6 3.5 14.1V9.2C3.5 7.7 4.6 6.6 6.2 6.6Z" fill="currentColor" />
        <circle cx="8.9" cy="11.7" r="1.2" fill="#FFFFFF" />
        <circle cx="12" cy="11.7" r="1.2" fill="#FFFFFF" />
        <circle cx="15.1" cy="11.7" r="1.2" fill="#FFFFFF" />
      </svg>
    );
  }

  if (icon === "trace") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 18.3V7.2L12 4.3L17 7.2V18.3L12 21.2L7 18.3Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <circle cx="12" cy="4.3" r="2" fill="currentColor" />
        <circle cx="7" cy="18.3" r="2" fill="currentColor" />
        <circle cx="17" cy="18.3" r="2" fill="currentColor" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4.8" y="12.2" width="3.8" height="6.6" rx="1" fill="currentColor" />
      <rect x="10.1" y="8.2" width="3.8" height="10.6" rx="1" fill="currentColor" />
      <rect x="15.4" y="5" width="3.8" height="13.8" rx="1" fill="currentColor" />
    </svg>
  );
}

function UserIcon() {
  return (
    <svg className="input-icon" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8.5" r="3.8" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M4.8 20.2C5.7 16.7 8.4 14.6 12 14.6C15.6 14.6 18.3 16.7 19.2 20.2" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg className="input-icon" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="5.4" y="10.2" width="13.2" height="9.6" rx="2.2" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M8.5 10.1V7.7C8.5 5.5 9.9 4 12 4C14.1 4 15.5 5.5 15.5 7.7V10.1" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3.5 12C5.5 8.4 8.3 6.6 12 6.6C15.7 6.6 18.5 8.4 20.5 12C18.5 15.6 15.7 17.4 12 17.4C8.3 17.4 5.5 15.6 3.5 12Z" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="2.7" fill="none" stroke="currentColor" strokeWidth="1.9" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3.5 12C5.5 8.4 8.3 6.6 12 6.6C15.7 6.6 18.5 8.4 20.5 12C18.5 15.6 15.7 17.4 12 17.4C8.3 17.4 5.5 15.6 3.5 12Z" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="2.7" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M3 3L21 21" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M12 8V8.01M12 11V17" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M12 7V13M12 16V16.01" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg className="spinner-icon" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="48" strokeDashoffset="12" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 6L18 18M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3.5L18.5 6.2V11.3C18.5 15.2 16.2 18.6 12 20.5C7.8 18.6 5.5 15.2 5.5 11.3V6.2L12 3.5Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="M12 8.5V12.7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="15.8" r="1.1" fill="currentColor" />
    </svg>
  );
}
