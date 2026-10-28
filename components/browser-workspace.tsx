"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { AudioOutlined, SafetyCertificateOutlined, PlusOutlined, DownloadOutlined, DeleteOutlined, SoundOutlined, FileTextOutlined, FolderOpenOutlined } from "@ant-design/icons";
import { clearCases, deleteCase, download, inspectAudio, listCases, makeBackup, maxAudioBytes, parseBackup, putCase, putCases, reportHtml, vowels, type LocalAudio, type LocalCase, type Vowel } from "@/lib/browser-store";
import "./browser-workspace.css";

const steps = [
  { key: "持续时间", question: "这种嗓音不适持续多久了？", options: ["不足 2 周", "2–4 周", "1–3 个月", "超过 3 个月"] },
  { key: "症状模式", question: "声音的变化通常在什么情况下出现？", options: ["一直持续", "时好时坏", "用嗓后加重", "晨起更明显"] },
  { key: "吸烟史", question: "请记录吸烟情况。如有吸烟史，可在备注补充年限与每日支数。", options: ["从不吸烟", "已经戒烟", "偶尔吸烟", "长期吸烟"] },
  { key: "饮酒史", question: "请记录饮酒情况。如有饮酒史，可在备注补充年限与饮用量。", options: ["从不饮酒", "已经戒酒", "偶尔饮酒", "经常饮酒"] },
];
const formatDate = (date: string) => new Date(date).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const vowelName = (key: Vowel) => key === "v" ? "ü" : key;
const emergency = /呼吸困难|喘不过气|无法呼吸|窒息|喉头水肿|持续大量.*出血|大出血|咯血|突发.*听力.*下降|突然.*听不见|意识.*异常|昏迷|严重过敏/;
const emergencyReply = "这些描述可能需要紧急评估。请立即停止采集，尽快前往线下医疗机构；如有呼吸困难、持续大量出血、意识异常或症状快速加重，请立即就医或呼叫当地急救服务。";
const localGuide = "这里是本地资料整理向导，使用固定问题，不调用大语言模型、不提供诊断。请先记录病史，再按需要采集六元音。";

function AudioPlayer({ audio }: { audio: LocalAudio }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => { const value = URL.createObjectURL(audio.blob); if (ref.current) ref.current.src = value; return () => URL.revokeObjectURL(value); }, [audio.blob]);
  return <audio ref={ref} controls preload="none" aria-label="回放本地录音" />;
}

function Notes({ item, disabled, save }: { item: LocalCase; disabled: boolean; save: (notes: string) => Promise<void> }) {
  const [notes, setNotes] = useState(item.notes);
  return <form onSubmit={(event) => { event.preventDefault(); void save(notes); }} className="local-notes">
    <label htmlFor="case-notes">补充备注</label>
    <textarea id="case-notes" value={notes} maxLength={20000} disabled={disabled} onChange={(event) => setNotes(event.target.value)} placeholder="可记录既往检查、用嗓情况、吸烟与饮酒详情。编辑后请保存。" />
    <button className="local-button secondary" disabled={disabled || notes === item.notes}>保存备注</button>
    {notes !== item.notes && <small>有尚未保存的备注，请保存后再切换病例。</small>}
  </form>;
}

export default function BrowserWorkspace() {
  const [items, setItems] = useState<LocalCase[]>([]);
  const [activeId, setActiveId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [tab, setTab] = useState<"interview" | "audio" | "report">("interview");
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState("");
  const [recording, setRecording] = useState<Vowel | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [persistent, setPersistent] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const alive = useRef(true);
  const allItems = useRef(items);
  const item = items.find((entry) => entry.id === activeId) ?? null;
  const locked = busy || recording !== null;
  const nextStep = item ? steps.find((step) => !item.answers[step.key]) : undefined;
  const urgent = item ? emergency.test([item.complaint, ...item.messages.filter((msg) => msg.role === "user").map((msg) => msg.text)].join(" ")) : false;
  const audioCount = item ? Object.keys(item.audio).length : 0;

  function updateItems(next: LocalCase[]) { allItems.current = next; setItems(next); }
  useEffect(() => {
    alive.current = true;
    void listCases().then((data) => { if (alive.current) { updateItems(data); setActiveId(data[0]?.id ?? ""); } })
      .catch((caught) => setError(String(caught instanceof Error ? caught.message : caught)))
      .finally(() => setLoading(false));
    void navigator.storage?.persisted?.().then(setPersistent).catch(() => {});
    return () => {
      alive.current = false;
      if (timer.current) clearInterval(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    if (recording) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [recording]);

  async function run(task: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await task(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作失败，请重试。"); }
    finally { setBusy(false); }
  }

  async function save(next: LocalCase) {
    const saved = { ...next, updatedAt: new Date().toISOString() };
    await putCase(saved);
    const data = [...allItems.current.filter((entry) => entry.id !== next.id), saved].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    updateItems(data); setNotice("已保存到当前浏览器");
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const field = (name: string) => String(data.get(name) ?? "").trim();
    if (!field("alias") || !field("complaint")) { setError("请填写称呼和主诉。"); return; }
    const now = new Date().toISOString();
    const next: LocalCase = { id: crypto.randomUUID(), alias: field("alias"), age: field("age"), gender: field("gender"), location: field("location"), complaint: field("complaint"), createdAt: now, updatedAt: now, notes: "", answers: {}, audio: {}, messages: [{ role: "assistant", text: localGuide, at: now }] };
    await run(async () => { await save(next); setActiveId(next.id); setTab("interview"); setCreateOpen(false); setMessage(""); });
  }

  async function answer(key: string, value: string) {
    if (!item) return;
    const now = new Date().toISOString();
    const answers = { ...item.answers, [key]: value };
    const following = steps.find((step) => !answers[step.key]);
    await run(() => save({ ...item, answers, messages: [...item.messages, { role: "user", text: `${key}：${value}`, at: now }, { role: "assistant", text: following?.question ?? "病史选项已记录。你可以继续添加备注或进入六元音采集。这里不会自动给出疾病判断。", at: now }] }));
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!item || !message.trim()) return;
    const value = message.trim(); const now = new Date().toISOString();
    const reply = emergency.test(value) ? emergencyReply : `补充内容已在本机保存。${nextStep?.question ?? "你可以继续采集录音或导出个人记录。本地向导不进行疾病诊断。"}`;
    await run(async () => { await save({ ...item, messages: [...item.messages, { role: "user", text: value, at: now }, { role: "assistant", text: reply, at: now }] }); setMessage(""); });
  }

  async function attachAudio(caseId: string, key: Vowel, blob: Blob, filename: string) {
    await run(async () => {
      const metrics = await inspectAudio(blob);
      const current = allItems.current.find((entry) => entry.id === caseId);
      if (!current) throw new Error("病例已被移除，录音未保存。");
      await save({ ...current, audio: { ...current.audio, [key]: { blob, filename, metrics, at: new Date().toISOString() } } });
    });
  }

  async function startRecording(key: Vowel) {
    if (!item) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { setError("当前浏览器不支持录音，请使用新版浏览器，或上传本地音频。"); return; }
    if (item.audio[key] && !window.confirm(`重新录制将替换 /${vowelName(key)}/ 的原录音，是否继续？`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const source = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      stream.current = source;
      const preferred = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((mime) => MediaRecorder.isTypeSupported(mime));
      const active = new MediaRecorder(source, preferred ? { mimeType: preferred } : undefined);
      const chunks: Blob[] = []; let bytes = 0;
      const targetId = item.id;
      active.ondataavailable = (event) => { chunks.push(event.data); bytes += event.data.size; if (bytes > maxAudioBytes && active.state === "recording") active.stop(); };
      const cleanup = () => { source.getTracks().forEach((track) => track.stop()); if (timer.current) clearInterval(timer.current); timer.current = null; recorder.current = null; setRecording(null); };
      let failed = false;
      active.onerror = () => { failed = true; cleanup(); setError("录音中断，请检查麦克风后重试。"); };
      active.onstop = () => {
        cleanup();
        if (!alive.current || failed) return;
        const blob = new Blob(chunks, { type: active.mimeType || preferred || "audio/webm" });
        const extension = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
        void attachAudio(targetId, key, blob, `vowel-${key}.${extension}`);
      };
      recorder.current = active; active.start(500); setElapsed(0); setRecording(key);
      let seconds = 0;
      timer.current = setInterval(() => { seconds++; setElapsed(seconds); if (seconds >= 60 && active.state === "recording") active.stop(); }, 1000);
    } catch { stream.current?.getTracks().forEach((track) => track.stop()); setError("无法使用麦克风，请检查浏览器权限，或使用本地文件上传。"); }
    finally { setBusy(false); }
  }

  async function backup(all: boolean) {
    if (!window.confirm("备份包含问诊资料和原始录音，文件将下载到你的电脑。请妥善保管，是否继续？")) return;
    await run(async () => { const records = all ? await listCases() : item ? [item] : []; download(await makeBackup(records), `VoiceScope-backup-${new Date().toISOString().slice(0, 10)}.json`); setNotice("备份已生成，请确认浏览器已完成下载。"); });
  }

  async function restore(file: File) {
    if (!window.confirm("备份会恢复为新的本地病例，不覆盖已有记录。只导入你信任且有权使用的备份，是否继续？")) return;
    await run(async () => { const records = await parseBackup(file); await putCases(records); updateItems(await listCases()); if (records[0]) setActiveId(records[0].id); setNotice(`已在本机恢复 ${records.length} 个病例。`); });
  }

  return <main className="local-shell">
    <aside className="local-sidebar">
      <div className="local-brand"><span className="local-brand-icon"><SoundOutlined /></span><div><strong>VoiceScope</strong><small>嗓音健康 · 本地工作台</small></div></div>
      <span className="local-edition"><span />浏览器本地版</span>
      <button className="local-button create" disabled={locked || loading} onClick={() => setCreateOpen(true)}><PlusOutlined /> 新建问诊记录</button>
      <label className="local-search"><span>本地病例</span><input aria-label="搜索本地病例" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索称呼或主诉" /></label>
      <nav className="local-case-list" aria-label="本地病例列表">{items.filter((entry) => `${entry.alias} ${entry.complaint}`.includes(search)).map((entry) => <button key={entry.id} disabled={locked} className={entry.id === activeId ? "selected" : ""} onClick={() => { setActiveId(entry.id); setMessage(""); setError(""); setNotice(""); }}><strong>{entry.alias}</strong><span>{entry.complaint}</span><small>{formatDate(entry.updatedAt)}</small></button>)}{!loading && !items.length && <p className="local-sidebar-empty">还没有本地记录<br />新建后只保存在这台设备</p>}</nav>
      <div className="local-sidebar-bottom"><button disabled={locked || !items.length} onClick={() => void backup(true)}><DownloadOutlined /> 导出全部备份</button><button disabled={locked} onClick={() => fileRef.current?.click()}><FolderOpenOutlined /> 从本机恢复备份</button><button onClick={() => setPrivacyOpen(true)}><SafetyCertificateOutlined /> 数据与隐私说明</button></div>
      <input ref={fileRef} hidden type="file" accept=".json,application/json" aria-label="恢复本地备份" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void restore(file); }} />
      <small className="local-sidebar-foot">无需注册 · 无云端数据库</small>
    </aside>
    <section className="local-main">
      <header className="local-header"><div><span className="local-eyebrow">PERSONAL VOICE WORKSPACE</span><h1>智能嗓音问诊<span>本地记录</span></h1></div><button className="local-privacy-pill" onClick={() => setPrivacyOpen(true)}><SafetyCertificateOutlined /> 数据保存在你的浏览器</button></header>
      <div className="local-content">
        <div className="local-storage-banner"><SafetyCertificateOutlined /><p><strong>资料留在你的电脑上</strong>问诊和录音仅存于当前浏览器，不上传 GitHub，也不调用第三方模型。请定期导出备份；清理浏览器数据可能丢失记录。</p></div>
        {error && <div role="alert" className="local-alert error">{error}</div>}
        {notice && <div role="status" className="local-alert success">{notice}</div>}
        {loading ? <div className="local-empty"><h2>正在读取本地记录…</h2></div> : !item ? <div className="local-empty"><div className="local-empty-icon"><SoundOutlined /></div><span className="local-eyebrow">YOUR DATA, YOUR DEVICE</span><h2>从一份本地记录开始</h2><p>整理嗓音不适、采集六元音、保存个人记录。<br />打开网页即可使用，资料由你自己保管。</p><button className="local-button" onClick={() => setCreateOpen(true)} disabled={busy}><PlusOutlined /> 创建第一份记录</button><div className="local-feature-grid"><article><FileTextOutlined /><strong>结构化问诊</strong><span>用固定问题整理病史</span></article><article><AudioOutlined /><strong>六元音采集</strong><span>录制、回放与技术提醒</span></article><article><DownloadOutlined /><strong>本地备份</strong><span>导出记录，自己保管</span></article></div><p className="local-limitation">本地版未接入疾病分类模型或大语言模型，不能替代医生诊疗。</p></div> : <>
          <section className="local-case-header"><div><span className="local-eyebrow">当前本地记录</span><h2>{item.alias}<small>{[item.age && `${item.age} 岁`, item.gender, item.location].filter(Boolean).join(" · ")}</small></h2><p>{item.complaint}</p></div><div><small>最近保存 {formatDate(item.updatedAt)}</small><button className="local-icon-button danger" aria-label="删除当前病例" title="删除当前病例" disabled={locked} onClick={() => { if (window.confirm("删除此病例及全部录音？此操作只删除当前浏览器中的记录，已导出的备份需自行处理。")) void run(async () => { await deleteCase(item.id); const data = await listCases(); updateItems(data); setActiveId(data[0]?.id ?? ""); setNotice("当前本地病例已删除。"); }); }}><DeleteOutlined /></button></div></section>
          {urgent && <div role="alert" className="local-alert error"><strong>请优先线下就医。</strong>{emergencyReply}</div>}
          <div className="local-tabs" role="tablist" aria-label="工作台功能">{([ ["interview", "01", "问诊记录"], ["audio", "02", "六元音采集"], ["report", "03", "个人记录导出"] ] as const).map(([key, number, label]) => <button role="tab" aria-selected={tab === key} key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}><span>{number}</span>{label}</button>)}</div>
          {tab === "interview" && <div className="local-work-grid"><section className="local-card"><div className="local-card-title"><h3>本地问诊向导</h3><span className="local-tag">固定问题 · 无模型调用</span></div><p className="local-muted">记录病史与补充说明。自动回复仅用于整理流程，不提供疾病判断。</p><div className="local-dialogue">{item.messages.map((msg, index) => <article key={index} className={msg.role}><small>{msg.role === "assistant" ? "本地向导" : "我的记录"} · {formatDate(msg.at)}</small><p>{msg.text}</p></article>)}</div>{nextStep && <div className="local-question"><strong>{nextStep.question}</strong><div>{nextStep.options.map((option) => <button disabled={locked} key={option} onClick={() => void answer(nextStep.key, option)}>{option}</button>)}</div></div>}<form className="local-message-form" onSubmit={send}><textarea aria-label="补充问诊内容" placeholder="补充你的情况，只记录在本机…" value={message} maxLength={2000} disabled={locked} onChange={(event) => setMessage(event.target.value)} /><button className="local-button" disabled={locked || !message.trim()}>保存补充</button></form></section><aside className="local-card local-summary"><h3>病史摘要</h3><dl><dt>主诉</dt><dd>{item.complaint}</dd>{steps.map((step) => <div key={step.key}><dt>{step.key}</dt><dd>{item.answers[step.key] || "待补充"}</dd>{item.answers[step.key] && <button className="local-text-button" disabled={locked} onClick={() => { const value = window.prompt(`修改${step.key}`, item.answers[step.key]); if (value?.trim()) void answer(step.key, value.trim().slice(0, 1000)); }}>修改</button>}</div>)}</dl><Notes key={item.id} item={item} disabled={locked} save={(notes) => run(() => save({ ...item, notes }))} /></aside></div>}
          {tab === "audio" && <><section className="local-card local-audio-intro"><div><h3>六元音录音</h3><p>选择元音，舒适、自然地持续发声。每段最长录制 60 秒，也可导入本地音频（每段 ≤15 MB、≤120 秒）。</p><small>本地技术提醒仅检查时长、音量和削波，不评估疾病、背景噪声或病理性声音。</small></div><span className="local-audio-count">{audioCount}<small>/ 6 段已保存</small></span></section><div className="local-audio-grid">{vowels.map((key) => { const audio = item.audio[key]; return <article className="local-card local-vowel" key={key}><div className="local-vowel-title"><strong>/{vowelName(key)}/</strong><span className="local-tag">{recording === key ? `录制中 ${elapsed} 秒` : audio ? "本机已保存" : "等待采集"}</span></div>{audio ? <><AudioPlayer audio={audio} /><p>{audio.metrics.seconds.toFixed(1)} 秒 · 电平 {audio.metrics.rmsDb?.toFixed(1) ?? "静音"} dBFS</p>{audio.metrics.warnings.map((warning) => <small className="local-warning" key={warning}>{warning}</small>)}{!audio.metrics.warnings.length && <small className="local-muted">未触发时长、电平或削波提醒</small>}</> : <div className="local-audio-placeholder"><AudioOutlined /><span>这段声音只保存在你的电脑</span></div>}<div className="local-vowel-actions">{recording === key ? <button className="local-button stop" onClick={() => { if (recorder.current?.state === "recording") recorder.current.stop(); }}>停止并保存</button> : <button className="local-button" disabled={locked || urgent} onClick={() => void startRecording(key)}><AudioOutlined /> {audio ? "重新录制" : "开始录音"}</button>}<label className={`local-upload${locked || urgent ? " disabled" : ""}`}>导入音频<input disabled={locked || urgent} type="file" accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; if (audio && !window.confirm(`导入将替换 /${vowelName(key)}/ 的原录音，是否继续？`)) return; void attachAudio(item.id, key, new Blob([file], { type: file.type || "audio/wav" }), file.name.slice(0, 200)); }} /></label>{audio && <button className="local-icon-button danger" aria-label={`删除 ${vowelName(key)} 录音`} disabled={locked} onClick={() => { if (window.confirm("删除这段本地录音？")) void run(async () => { const rest = { ...item.audio }; delete rest[key]; await save({ ...item, audio: rest }); }); }}><DeleteOutlined /></button>}</div></article>; })}</div><section className="local-card local-model-boundary"><h3>疾病分类模型暂不可用</h3><p>现有 AST + WavLM 嗓音模型依赖 Python 环境，尚未移植到浏览器。本地版不会生成预测概率、诊断标签或伪造分析结果。</p></section></>}
          {tab === "report" && <section className="local-card local-report"><span className="local-eyebrow">PERSONAL RECORD</span><h3>你的记录，由你保管</h3><p>导出包含主诉、结构化病史、补充记录、备注和录音技术信息的 HTML 文档，可在浏览器打印或另存为 PDF。</p><div className="local-report-summary"><article><strong>{Object.keys(item.answers).length}</strong><span>项病史记录</span></article><article><strong>{audioCount}</strong><span>段本地录音</span></article><article><strong>{item.messages.filter((msg) => msg.role === "user").length}</strong><span>条补充与回答</span></article></div><div className="local-report-actions"><button className="local-button" disabled={locked} onClick={() => { download(reportHtml(item), `VoiceScope-record-${item.id.slice(0, 8)}.html`); setNotice("记录已生成，下载完成后可打开并打印。"); }}><FileTextOutlined /> 导出个人记录</button><button className="local-button secondary" disabled={locked} onClick={() => void backup(false)}><DownloadOutlined /> 备份此病例与录音</button></div><p className="local-limitation">这是一份个人资料整理记录，未运行疾病分类模型、未完成医生复核，不是诊断报告。HTML 记录不含录音文件；录音包含在 JSON 备份中。</p></section>}
        </>}
        <footer className="local-footer">VoiceScope · 浏览器本地版<span>仅供记录与研究参考，不能替代医生诊疗</span></footer>
      </div>
    </section>
    {createOpen && <div className="local-modal-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="new-case-title" className="local-modal"><button className="local-modal-close" aria-label="关闭新建记录" disabled={busy} onClick={() => setCreateOpen(false)}>×</button><span className="local-eyebrow">NEW LOCAL RECORD</span><h2 id="new-case-title">新建问诊记录</h2><p className="local-muted">建议使用化名，不必填写真实姓名或详细地址。</p>{error && <div role="alert" className="local-alert error">{error}</div>}<form onSubmit={(event) => void create(event)}><label>称呼 / 化名<input name="alias" maxLength={64} required autoFocus placeholder="例如：小林" /></label><div className="local-form-row"><label>年龄（选填）<input name="age" type="number" min="0" max="120" /></label><label>性别（选填）<select name="gender"><option value="">不填写</option><option>男</option><option>女</option><option>其他 / 不便透露</option></select></label></div><label>地区（选填）<input name="location" maxLength={64} placeholder="仅省市，无需详细地址" /></label><label>主要不适 / 主诉<textarea name="complaint" maxLength={500} required placeholder="例如：声音嘶哑一段时间" /></label><label className="local-consent"><input required type="checkbox" />我理解资料保存在当前浏览器，需要自行备份；本地版不提供疾病诊断。</label><button className="local-button" disabled={busy}>创建并保存在本机</button></form></section></div>}
    {privacyOpen && <div className="local-modal-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="local-privacy-title" className="local-modal"><button className="local-modal-close" aria-label="关闭隐私说明" disabled={busy} onClick={() => setPrivacyOpen(false)}>×</button><SafetyCertificateOutlined className="local-privacy-icon" /><h2 id="local-privacy-title">数据留在当前浏览器</h2><ul><li>问诊、备注和录音使用浏览器本地数据库保存，无云端同步，不上传 GitHub 或平台服务器。</li><li>本地版不连接第三方模型，也不读取电脑上原平台的数据。现有 Python 模型、医生复核和多账户权限仅在原完整版中提供。</li><li>不同设备、浏览器和浏览器配置之间不共享记录。清理网站数据、无痕模式退出或浏览器回收存储，可能导致资料丢失，请定期备份。</li><li>本地数据库未额外加密，也没有账户隔离。能使用此浏览器配置的人可能读取记录，请使用私人电脑并保护系统账户。</li><li>导出的备份含健康资料与录音，请自行妥善保管。网页只删除当前浏览器的数据，不能删除你已有的下载或其他副本。</li><li>打开网页仍会向 GitHub 请求页面资源，托管服务可能记录访问 IP 等常规信息；问诊和录音不会随这些请求发送。</li></ul><button className="local-button secondary" disabled={busy} onClick={() => void run(async () => { const granted = await navigator.storage?.persist?.(); setPersistent(Boolean(granted)); setNotice(granted ? "浏览器已授予持久存储，仍需定期备份。" : "浏览器未授予持久存储，请定期导出备份。"); })}>{persistent ? "已授予持久存储" : "请求浏览器保留本地数据"}</button><p className="local-muted">持久存储可降低自动回收风险，无法阻止手动清理数据。</p><button className="local-button danger-button" disabled={locked || !items.length} onClick={() => { if (window.confirm("清空当前浏览器的所有病例和录音？删除后无法恢复，除非你已有备份。")) void run(async () => { await clearCases(); updateItems([]); setActiveId(""); setNotice("当前浏览器中的全部病例和录音已删除。"); setPrivacyOpen(false); }); }}>清空此浏览器的全部病例与录音</button></section></div>}
  </main>;
}
