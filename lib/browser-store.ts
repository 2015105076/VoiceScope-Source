/** Browser edition: no backend, external API, cookie or network persistence. */
export const vowels = ["a", "o", "e", "i", "u", "v"] as const;
export type Vowel = typeof vowels[number];
export type LocalMessage = { role: "user" | "assistant"; text: string; at: string };
export type AudioMetrics = { seconds: number; rmsDb: number | null; clipped: number; warnings: string[] };
export type LocalAudio = { blob: Blob; filename: string; metrics: AudioMetrics; at: string };
export type LocalCase = {
  id: string; alias: string; age: string; gender: string; location: string; complaint: string;
  createdAt: string; updatedAt: string; notes: string;
  answers: Record<string, string>; messages: LocalMessage[]; audio: Partial<Record<Vowel, LocalAudio>>;
};
const DB = "voicescope-browser-local-v1";
const STORE = "cases";
export const maxAudioBytes = 15 * 1024 * 1024;

function openStore(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) { reject(new Error("当前浏览器不支持本地数据库，请使用新版浏览器。")); return; }
    const request = indexedDB.open(DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "id" });
    request.onerror = () => reject(new Error("无法打开本地存储，请检查浏览器隐私设置或磁盘空间。"));
    request.onblocked = () => reject(new Error("本地存储被其他标签页占用，请关闭其他 VoiceScope 页面后重试。"));
    request.onsuccess = () => resolve(request.result);
  });
}

async function transaction<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openStore();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const request = operation(tx.objectStore(STORE));
    tx.oncomplete = () => { db.close(); resolve(request.result); };
    tx.onabort = tx.onerror = () => { db.close(); reject(new Error("本地保存失败，可能存储空间不足。请先导出备份，再检查浏览器设置。")); };
  });
}

export async function listCases(): Promise<LocalCase[]> {
  const items = await transaction("readonly", (store) => store.getAll());
  return (items as LocalCase[]).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
export async function putCase(item: LocalCase) { await transaction("readwrite", (store) => store.put(item)); }
export async function deleteCase(id: string) { await transaction("readwrite", (store) => store.delete(id)); }
export async function clearCases() { await transaction("readwrite", (store) => store.clear()); }
export async function putCases(items: LocalCase[]) {
  const db = await openStore();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    for (const item of items) tx.objectStore(STORE).put(item);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onabort = tx.onerror = () => { db.close(); reject(new Error("恢复备份失败，未写入本次导入的数据。")); };
  });
}

export async function inspectAudio(blob: Blob): Promise<AudioMetrics> {
  if (!blob.size || blob.size > maxAudioBytes) throw new Error("请选择非空音频文件，每段不超过 15 MB。");
  const context = new AudioContext();
  try {
    const audio = await context.decodeAudioData(await blob.arrayBuffer());
    if (audio.duration > 120) throw new Error("每段录音最多 120 秒，请裁剪后再导入。");
    // Assess each channel independently so opposite-phase stereo cannot cancel.
    let squares = 0, clipped = 0, samples = 0;
    for (let channel = 0; channel < audio.numberOfChannels; channel++) {
      const data = audio.getChannelData(channel);
      for (const value of data) { squares += value * value; clipped += Math.abs(value) >= 0.995 ? 1 : 0; }
      samples += data.length;
    }
    const rms = Math.sqrt(squares / Math.max(samples, 1));
    const fraction = clipped / Math.max(samples, 1);
    const warnings: string[] = [];
    if (audio.duration < 0.5) warnings.push("录音较短，建议重新录制。");
    if (rms <= 1e-5) warnings.push("录音接近静音，请检查麦克风。");
    if (fraction >= 0.01) warnings.push("录音可能存在削波失真，请适当远离麦克风重录。");
    return { seconds: audio.duration, rmsDb: rms > 0 ? 20 * Math.log10(rms) : null, clipped: fraction, warnings };
  } catch (error) {
    if (error instanceof DOMException) throw new Error("浏览器无法解码这段音频，请改用 WAV、MP3 或当前浏览器录制的文件。");
    throw error;
  } finally { await context.close(); }
}

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("无法读取录音备份。"));
    reader.readAsDataURL(blob);
  });
}
export async function makeBackup(items: LocalCase[]): Promise<Blob> {
  const total = items.reduce((sum, item) => sum + Object.values(item.audio).reduce((n, audio) => n + (audio?.blob.size ?? 0), 0), 0);
  if (total > 100 * 1024 * 1024) throw new Error("全部录音超过 100 MB，请分别导出每个病例。");
  const cases = await Promise.all(items.map(async (item) => ({ ...item, audio: Object.fromEntries(await Promise.all(
    Object.entries(item.audio).map(async ([key, audio]) => [key, { ...audio, blob: undefined, type: audio.blob.type, base64: await readBlob(audio.blob) }]),
  )) })));
  return new Blob([JSON.stringify({ format: "voicescope-local", version: 1, exportedAt: new Date().toISOString(), cases })], { type: "application/json" });
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("备份格式不正确。");
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 4000): string {
  if (typeof value !== "string" || value.length > max) throw new Error("备份包含无效文本或过长字段。");
  return value;
}
function timestamp(value: unknown): string {
  const date = text(value, 64);
  if (!Number.isFinite(Date.parse(date))) throw new Error("备份时间字段不正确。");
  return date;
}
export async function parseBackup(file: File): Promise<LocalCase[]> {
  if (file.size > 150 * 1024 * 1024) throw new Error("备份文件不能超过 150 MB。");
  const data = record(JSON.parse(await file.text()));
  if (data.format !== "voicescope-local" || data.version !== 1 || !Array.isArray(data.cases) || data.cases.length > 500) throw new Error("请选择此平台导出的版本 1 备份文件（最多 500 个病例）。");
  const results: LocalCase[] = [];
  let total = 0;
  for (const entry of data.cases) {
    const item = record(entry);
    const audio: LocalCase["audio"] = {};
    for (const [key, value] of Object.entries(record(item.audio))) {
      if (!vowels.includes(key as Vowel)) throw new Error("备份中有无法识别的元音。");
      const source = record(value);
      const base64 = text(source.base64, Math.ceil(maxAudioBytes / 3) * 4 + 4);
      const type = text(source.type, 100);
      if (!/^audio\/[a-z0-9.+-]+(?:;codecs=[a-z0-9., -]+)?$/i.test(type)) throw new Error("备份中的录音格式不受支持。");
      const binary = atob(base64);
      total += binary.length;
      if (binary.length > maxAudioBytes || total > 100 * 1024 * 1024) throw new Error("备份音频过大，请分病例恢复。");
      const blob = new Blob([Uint8Array.from(binary, (ch) => ch.charCodeAt(0))], { type });
      audio[key as Vowel] = { blob, filename: text(source.filename, 200), at: timestamp(source.at), metrics: await inspectAudio(blob) };
    }
    if (!Array.isArray(item.messages) || item.messages.length > 2000) throw new Error("备份对话格式不正确。");
    const messages: LocalMessage[] = item.messages.map((value) => {
      const message = record(value);
      if (message.role !== "user" && message.role !== "assistant") throw new Error("备份对话角色不正确。");
      return { role: message.role, text: text(message.text), at: timestamp(message.at) };
    });
    const answers = Object.fromEntries(Object.entries(record(item.answers)).map(([key, value]) => [text(key, 80), text(value, 1000)]));
    results.push({ id: crypto.randomUUID(), alias: text(item.alias, 64), age: text(item.age, 3), gender: text(item.gender, 16), location: text(item.location, 64), complaint: text(item.complaint, 500), notes: text(item.notes, 20000), createdAt: timestamp(item.createdAt), updatedAt: new Date().toISOString(), answers, messages, audio });
  }
  return results;
}

const escape = (value: string) => value.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
export function reportHtml(item: LocalCase): Blob {
  const rows = Object.entries(item.answers).map(([key, value]) => `<tr><th>${escape(key)}</th><td>${escape(value)}</td></tr>`).join("");
  const audioRows = vowels.map((key) => {
    const audio = item.audio[key];
    return `<tr><th>/${key === "v" ? "ü" : key}/</th><td>${audio ? `${audio.metrics.seconds.toFixed(1)} 秒；${escape(audio.metrics.warnings.join("；") || "未触发本地技术提醒")}` : "未采集"}</td></tr>`;
  }).join("");
  const messages = item.messages.map((message) => `<p><b>${message.role === "user" ? "补充记录" : "本地向导"}</b>：${escape(message.text)}</p>`).join("");
  return new Blob([`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'"><title>VoiceScope 本地记录</title><style>body{max-width:850px;margin:40px auto;padding:20px;font:16px/1.8 system-ui;color:#16352e}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd;text-align:left}p{white-space:pre-wrap}small{color:#52645e}@media print{body{margin:0}}</style><h1>VoiceScope 本地记录</h1><p>称呼：${escape(item.alias)}　年龄：${escape(item.age)}　性别：${escape(item.gender)}　地区：${escape(item.location)}</p><p>主诉：${escape(item.complaint)}</p><small>浏览器本地版 · ${escape(new Date().toLocaleString("zh-CN"))}<br>这是个人资料整理记录，未运行疾病分类模型、未完成医生复核，不是诊断报告。</small><h2>问诊记录</h2><table>${rows}</table><h2>六元音采集</h2><p>技术检查仅涉及时长、电平和削波；录音时长不等同于最长发声时间，不能据此判断疾病。</p><table>${audioRows}</table><h2>补充与备注</h2>${messages}<p>${escape(item.notes)}</p></html>`], { type: "text/html;charset=utf-8" });
}
export function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
