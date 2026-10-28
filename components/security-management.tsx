"use client";

import { Alert, Button, Modal, Tag } from "antd";
import { FormEvent, useState } from "react";
import { authFetch, AuthUser } from "@/components/auth-context";

type Case = { conversation_id: string; title: string };
type Grant = { id: string; conversation_id: string; grantee_user_id: string; display_name: string; expires_at: string | null; revoked_at: string | null };
type Audit = { id: string; actor_username: string | null; actor_role: string | null; action: string; request_path: string | null; outcome: string; status_code: number | null; created_at: string };

async function api<T>(path: string, options?: RequestInit) {
  const response = await authFetch(path, options);
  const data = await response.json().catch(() => ({})) as T & { detail?: string };
  if (!response.ok) throw new Error(data.detail || "安全管理服务不可用");
  return data;
}

export default function SecurityManagement({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [cases, setCases] = useState<Case[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [account, setAccount] = useState({ username: "", password: "", display_name: "", role: "doctor" });
  const [grant, setGrant] = useState({ conversation_id: "", doctor_user_id: "", expires_at: "" });
  const [owner, setOwner] = useState({ conversation_id: "", patient_user_id: "" });

  async function load() {
    setLoading(true); setMessage("");
    try {
      const [userData, caseData, grantData, auditData] = await Promise.all([
        api<{ users: AuthUser[] }>("/api/admin/users"),
        api<{ conversations: Case[] }>("/api/conversations"),
        api<{ grants: Grant[] }>("/api/admin/case-grants"),
        api<{ entries: Audit[] }>("/api/admin/audit?limit=100"),
      ]);
      setUsers(userData.users); setCases(caseData.conversations); setGrants(grantData.grants); setAudit(auditData.entries);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    } finally { setLoading(false); }
  }

  async function createAccount(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/admin/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(account) });
      setAccount({ username: "", password: "", display_name: "", role: "doctor" });
      setMessage("账户已创建。"); await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建失败"); }
  }

  async function grantCase(event: FormEvent) {
    event.preventDefault();
    try {
      await api(`/api/admin/cases/${grant.conversation_id}/grants`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ doctor_user_id: grant.doctor_user_id, expires_at: grant.expires_at ? new Date(grant.expires_at).toISOString() : null }) });
      setMessage("病例授权已生效。"); await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "授权失败"); }
  }

  async function bindOwner(event: FormEvent) {
    event.preventDefault();
    try {
      await api(`/api/admin/cases/${owner.conversation_id}/owner`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ patient_user_id: owner.patient_user_id, replace: false }) });
      setMessage("旧病例已安全绑定患者账户。"); await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "绑定失败"); }
  }

  async function toggleUser(user: AuthUser) {
    try {
      await api(`/api/admin/users/${user.id}/status`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: user.status === "active" ? "disabled" : "active" }) });
      setMessage(user.status === "active" ? "账户已停用，现有会话已失效。" : "账户已恢复。"); await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "账户状态更新失败"); }
  }

  return <Modal open={open} onCancel={onClose} afterOpenChange={(visible) => { if (visible) void load(); }} width={1100} title="账户、病例授权与访问审计" footer={<Button onClick={onClose}>关闭</Button>}>
    {message && <Alert showIcon closable message={message} onClose={() => setMessage("")} type={/失败|无权|不存在/.test(message) ? "error" : "success"} />}
    <div className="security-admin-grid">
      <form onSubmit={createAccount}><h3>创建账户</h3><input required placeholder="账号" minLength={3} value={account.username} onChange={(event) => setAccount({ ...account, username: event.target.value })} /><input required placeholder="姓名 / 称呼" value={account.display_name} onChange={(event) => setAccount({ ...account, display_name: event.target.value })} /><input required type="password" minLength={12} placeholder="临时密码（12 位以上）" value={account.password} onChange={(event) => setAccount({ ...account, password: event.target.value })} /><select value={account.role} onChange={(event) => setAccount({ ...account, role: event.target.value })}><option value="doctor">医生</option><option value="patient">患者</option><option value="admin">管理员</option></select><Button htmlType="submit" type="primary">创建</Button></form>
      <form onSubmit={grantCase}><h3>授权医生访问病例</h3><select required value={grant.conversation_id} onChange={(event) => setGrant({ ...grant, conversation_id: event.target.value })}><option value="">选择病例</option>{cases.map((item) => <option key={item.conversation_id} value={item.conversation_id}>{item.title} · {item.conversation_id.slice(0, 8)}</option>)}</select><select required value={grant.doctor_user_id} onChange={(event) => setGrant({ ...grant, doctor_user_id: event.target.value })}><option value="">选择医生</option>{users.filter((user) => user.role === "doctor" && user.status === "active").map((user) => <option key={user.id} value={user.id}>{user.display_name} ({user.username})</option>)}</select><label>到期时间（可选）<input type="datetime-local" value={grant.expires_at} onChange={(event) => setGrant({ ...grant, expires_at: event.target.value })} /></label><Button htmlType="submit" type="primary">授权</Button></form>
      <form onSubmit={bindOwner}><h3>归属旧病例</h3><select required value={owner.conversation_id} onChange={(event) => setOwner({ ...owner, conversation_id: event.target.value })}><option value="">选择病例</option>{cases.map((item) => <option key={item.conversation_id} value={item.conversation_id}>{item.title} · {item.conversation_id.slice(0, 8)}</option>)}</select><select required value={owner.patient_user_id} onChange={(event) => setOwner({ ...owner, patient_user_id: event.target.value })}><option value="">选择患者账户</option>{users.filter((user) => user.role === "patient").map((user) => <option key={user.id} value={user.id}>{user.display_name} ({user.username})</option>)}</select><Button htmlType="submit">绑定（不覆盖已有归属）</Button></form>
    </div>
    <section className="security-list"><div><h3>账户</h3><Button size="small" loading={loading} onClick={() => void load()}>刷新</Button></div>{users.map((user) => <article key={user.id}><strong>{user.display_name}</strong><span>{user.username}</span><Tag>{user.role}</Tag><Tag color={user.status === "active" ? "green" : "red"}>{user.status}</Tag><Button size="small" danger={user.status === "active"} onClick={() => void toggleUser(user)}>{user.status === "active" ? "停用" : "恢复"}</Button></article>)}</section>
    <section className="security-list"><h3>病例授权记录</h3>{grants.map((item) => <article key={item.id}><strong>{item.display_name}</strong><span>{item.conversation_id.slice(0, 12)}</span><Tag color={item.revoked_at ? "default" : "blue"}>{item.revoked_at ? "已撤回" : "有效"}</Tag>{!item.revoked_at && <Button danger size="small" onClick={() => void api(`/api/admin/case-grants/${item.id}`, { method: "DELETE" }).then(() => load()).catch((error: Error) => setMessage(error.message))}>撤回</Button>}</article>)}</section>
    <section className="security-audit"><h3>最近访问审计</h3><div>{audit.map((item) => <article key={item.id}><time>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</time><b>{item.actor_username || "未认证"}</b><span>{item.action} · {item.request_path}</span><Tag color={item.outcome === "success" ? "green" : "red"}>{item.status_code ?? item.outcome}</Tag></article>)}</div></section>
  </Modal>;
}
