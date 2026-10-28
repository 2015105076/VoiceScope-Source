"use client";

import { Button, Checkbox, Tabs } from "antd";
import { createContext, FormEvent, ReactNode, useContext, useEffect, useState } from "react";

export type AuthUser = {
  id: string;
  username: string;
  display_name: string;
  role: "patient" | "doctor" | "admin";
  status: "active" | "disabled";
  created_at: string;
};

type Consent = { accepted: boolean; version: string; created_at: string } | null;
type AuthPayload = { user: AuthUser; csrf_token: string; consent: Consent; required_consent_version: string };
type AuthContextValue = AuthPayload & { logout: () => Promise<void>; setConsent: (accepted: boolean) => Promise<void> };

const apiBaseUrl = (process.env.NEXT_PUBLIC_VOICESCOPE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const AuthContext = createContext<AuthContextValue | null>(null);
let csrfToken = "";

function saveSession(payload: AuthPayload | null) {
  csrfToken = payload?.csrf_token ?? "";
  if (typeof window !== "undefined") {
    if (csrfToken) window.sessionStorage.setItem("voicescope-csrf", csrfToken);
    else window.sessionStorage.removeItem("voicescope-csrf");
  }
}

export async function authFetch(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  const token = csrfToken || (typeof window !== "undefined" ? window.sessionStorage.getItem("voicescope-csrf") ?? "" : "");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && token) headers.set("X-CSRF-Token", token);
  return fetch(path.startsWith("http") ? path : `${apiBaseUrl}${path}`, { ...options, headers, credentials: "include" });
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthGate");
  return value;
}

export default function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthPayload | null>(null);
  const [checking, setChecking] = useState(true);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [consentAccepted, setConsentAccepted] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void authFetch("/api/auth/me").then(async (response) => {
      if (!response.ok) return;
      const payload = await response.json() as AuthPayload;
      saveSession(payload); setSession(payload);
    }).catch(() => {
      // 后端离线时保留登录页，由用户下一次操作显示可读错误。
    }).finally(() => setChecking(false));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true); setError("");
    try {
      const body = mode === "register"
        ? { username, password, display_name: displayName, consent_accepted: consentAccepted, consent_version: "2026-09-14-v1" }
        : { username, password };
      const response = await authFetch(`/api/auth/${mode}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json() as AuthPayload & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || "认证失败");
      saveSession(payload); setSession(payload); setPassword("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "认证失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function logout() {
    await authFetch("/api/auth/logout", { method: "POST" });
    saveSession(null); setSession(null); setPassword("");
  }

  async function setConsent(accepted: boolean) {
    const response = await authFetch("/api/auth/consent", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accepted, version: session?.required_consent_version }) });
    const data = await response.json() as { consent?: Consent; detail?: string };
    if (!response.ok) throw new Error(data.detail || "授权更新失败");
    setSession((current) => current ? { ...current, consent: data.consent ?? null } : current);
  }

  if (checking) return <main className="auth-screen"><div className="auth-card"><h1>VoiceScope</h1><p>正在校验安全会话…</p></div></main>;
  if (!session) return <main className="auth-screen">
    <section className="auth-card">
      <div className="auth-brand"><span>VoiceScope</span><h1>安全访问</h1><p>问诊、录音与报告将与当前账户安全绑定。</p></div>
      <Tabs activeKey={mode} onChange={(key) => { setMode(key as "login" | "register"); setError(""); }} items={[{ key: "login", label: "登录" }, { key: "register", label: "患者注册" }]} />
      <form className="auth-form" onSubmit={submit}>
        {mode === "register" && <label>姓名 / 称呼<input required maxLength={64} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
        <label>账号<input required minLength={3} maxLength={64} autoComplete="username" placeholder="小写字母、数字或 _.-" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <label>密码<input required minLength={12} maxLength={128} type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="至少 12 位，包含字母和数字" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {mode === "register" && <Checkbox checked={consentAccepted} onChange={(event) => setConsentAccepted(event.target.checked)}>
          我已阅读并授权系统使用问诊与嗓音数据完成本次筛查、报告与授权医生复核（版本 2026-09-14-v1）。
        </Checkbox>}
        {error && <p className="auth-error">{error}</p>}
        <Button type="primary" htmlType="submit" block loading={submitting} disabled={mode === "register" && !consentAccepted}>{mode === "login" ? "登录" : "同意并创建账户"}</Button>
      </form>
      <small>医生与管理员账户由管理员创建，不开放自助注册。</small>
    </section>
  </main>;

  const value: AuthContextValue = { ...session, logout, setConsent };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
