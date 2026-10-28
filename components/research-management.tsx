"use client";

import { Alert, Button, Empty, Modal, Tag } from "antd";
import { FormEvent, useState } from "react";
import { authFetch, useAuth } from "@/components/auth-context";

type ReviewStatus = "pending" | "in_review" | "completed" | "needs_information";
type QueueFilter = "all" | ReviewStatus;

type Review = {
  id?: string;
  review_status: ReviewStatus;
  reviewer: string | null;
  opinion: string | null;
  recommendation: string | null;
  version: number;
  created_at: string | null;
};

type Item = {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status?: string;
  patient: { name: string; gender: string; age: number | null };
  quality: { status: string; issues: string[] };
  review: Review;
  audio_count: number;
  examination_count: number;
  review_eligible?: boolean;
};

type Message = { id: number; role: "user" | "assistant"; content: string; created_at: string };
type CasePoint = { label: string; value: string; status: string };
type AudioAsset = {
  id: string;
  vowel_key: string | null;
  original_filename: string;
  media_type: string;
  size_bytes: number;
  created_at: string;
  playback_url: string;
};
type Examination = { id: string; examination_type: string; examination_date: string | null; findings: string; clinician: string; created_at: string };
type Analysis = {
  id: string;
  analysis_type: string;
  created_at: string;
  result: {
    request_id?: string;
    predicted_label?: string;
    predicted_probability?: number;
    probabilities?: Array<{ label: string; probability: number }>;
    warnings?: string[];
    disclaimer?: string;
  };
};
type Detail = {
  conversation: { conversation_id: string; facts: Record<string, unknown>; messages: Message[] };
  interview_summary: {
    patient: { name?: string; age?: number; gender?: string; location?: string; chief_complaint?: string };
    smoking_history?: { smoking_status?: string; smoking_years?: number | null; cigarettes_per_day?: number | null } | null;
    alcohol_history?: { drinking_status?: string; drinking_years?: number | null; standard_drinks_per_day?: number | null } | null;
    case_points: { points?: Record<string, CasePoint>; urgent?: boolean; collected_count?: number };
    user_message_count: number;
  };
  audio_assets: AudioAsset[];
  analyses: Analysis[];
  labels: Array<Record<string, unknown>>;
  examinations: Examination[];
  follow_ups: Array<Record<string, unknown>>;
  reviews: Review[];
  quality: { status: string; issues: string[] };
};
type Model = { id: string; model_name: string; version: string };
type Counts = Record<QueueFilter, number>;

const vowelNames: Record<string, string> = { a: "/a/ 啊", o: "/o/ 喔", e: "/e/ 鹅", i: "/i/ 衣", u: "/u/ 乌", v: "/ü/ 迂" };

function reviewLabel(status: ReviewStatus) {
  return { pending: "待复核", in_review: "复核中", completed: "已完成", needs_information: "需补资料" }[status];
}

function reviewColor(status: ReviewStatus) {
  return { pending: "orange", in_review: "blue", completed: "green", needs_information: "magenta" }[status];
}

function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function historyText(status?: string, years?: number | null, amount?: number | null, unit?: string) {
  if (!status) return "未登记";
  const details = [years != null ? `${years} 年` : "", amount != null ? `${amount} ${unit}` : ""].filter(Boolean).join("，");
  return `${status}${details ? `（${details}）` : ""}`;
}

export default function ResearchManagement({ apiBaseUrl, open, onClose }: { apiBaseUrl: string; open: boolean; onClose: () => void }) {
  const auth = useAuth();
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [counts, setCounts] = useState<Counts>({ all: 0, pending: 0, in_review: 0, completed: 0, needs_information: 0 });
  const [reviewFilter, setReviewFilter] = useState<QueueFilter>("pending");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [gender, setGender] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [review, setReview] = useState({ review_status: "completed" as Exclude<ReviewStatus, "pending">, reviewer: auth.user.display_name, opinion: "", recommendation: "" });
  const [label, setLabel] = useState({ label_type: "临床诊断", label_value: "", label_status: "preliminary", annotator: auth.user.display_name, source: "人工标注", notes: "" });
  const [exam, setExam] = useState({ examination_type: "喉镜", examination_date: "", findings: "", clinician: auth.user.display_name });
  const [follow, setFollow] = useState({ follow_up_date: "", outcome_status: "completed", outcome_notes: "", recorder: auth.user.display_name });
  const [model, setModel] = useState({ model_name: "", version: "", model_type: "", artifact_reference: "", status: "draft" });
  const [experiment, setExperiment] = useState({ model_version_id: "", dataset_version: "", split_strategy: "patient-level 7:1:2", random_seed: "42", run_status: "completed" });

  const api = async <T,>(path: string, options?: RequestInit) => {
    const response = await authFetch(`${apiBaseUrl}${path}`, options);
    const data = await response.json().catch(() => ({})) as T & { detail?: string };
    if (!response.ok) throw new Error(data.detail || "医生复核服务不可用");
    return data;
  };

  const loadCase = async (id: string) => {
    setSelected(id);
    setDetail(null);
    try {
      const raw = await api<Partial<Detail>>(`/api/research/cases/${id}`);
      const facts = raw.conversation?.facts ?? {};
      const patientInfo = (facts.patient_info ?? {}) as Detail["interview_summary"]["patient"];
      const messages = raw.conversation?.messages ?? [];
      const next: Detail = {
        conversation: raw.conversation ?? { conversation_id: id, facts, messages },
        interview_summary: raw.interview_summary ?? {
          patient: patientInfo,
          smoking_history: (facts.smoking_history ?? null) as Detail["interview_summary"]["smoking_history"],
          alcohol_history: (facts.alcohol_history ?? null) as Detail["interview_summary"]["alcohol_history"],
          case_points: (facts.case_points ?? { points: {} }) as Detail["interview_summary"]["case_points"],
          user_message_count: messages.filter((item) => item.role === "user").length,
        },
        audio_assets: raw.audio_assets ?? [],
        analyses: (raw.analyses ?? []).map((item) => {
          const legacy = item as Analysis & { result_json?: string };
          if (legacy.result || !legacy.result_json) return legacy;
          try { return { ...legacy, result: JSON.parse(legacy.result_json) as Analysis["result"] }; }
          catch { return { ...legacy, result: {} }; }
        }),
        labels: raw.labels ?? [],
        examinations: raw.examinations ?? [],
        follow_ups: raw.follow_ups ?? [],
        reviews: raw.reviews ?? [],
        quality: raw.quality ?? { status: "unchecked", issues: [] },
      };
      setDetail(next);
      const latest = next.reviews.at(-1);
      setReview(() => ({
        review_status: latest?.review_status === "in_review" || latest?.review_status === "needs_information" ? latest.review_status : "completed",
        reviewer: auth.user.display_name,
        opinion: "",
        recommendation: "",
      }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "病例加载失败");
    }
  };

  const load = async (nextFilter: QueueFilter = reviewFilter, preferredId = selected) => {
    setLoading(true);
    setMessage("");
    try {
      const query = new URLSearchParams({ review_status: nextFilter, review_queue: "true" });
      if (name) query.set("name", name);
      if (gender) query.set("gender", gender);
      if (dateFrom) query.set("date_from", dateFrom);
      if (dateTo) query.set("date_to", dateTo);
      const [caseData, modelData] = await Promise.all([
        api<{ cases: Item[]; counts?: Counts }>(`/api/research/cases?${query}`),
        api<{ models: Model[] }>("/api/research/models"),
      ]);
      const normalizedCases = caseData.cases.map((item) => ({
        ...item,
        patient: item.patient ?? { name: item.title.split("·")[0]?.trim() || "未登记", gender: "未说明", age: null },
        review: item.review ?? { review_status: "pending", reviewer: null, opinion: null, recommendation: null, version: 0, created_at: null },
        audio_count: item.audio_count ?? 0,
        examination_count: item.examination_count ?? 0,
      }));
      const derivedCounts: Counts = {
        all: normalizedCases.length,
        pending: normalizedCases.filter((item) => item.review.review_status === "pending").length,
        in_review: normalizedCases.filter((item) => item.review.review_status === "in_review").length,
        completed: normalizedCases.filter((item) => item.review.review_status === "completed").length,
        needs_information: normalizedCases.filter((item) => item.review.review_status === "needs_information").length,
      };
      setItems(normalizedCases);
      setCounts(caseData.counts ?? derivedCounts);
      setModels(modelData.models);
      const nextId = normalizedCases.some((item) => item.conversation_id === preferredId) ? preferredId : normalizedCases[0]?.conversation_id;
      if (nextId) await loadCase(nextId);
      else { setSelected(""); setDetail(null); }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  const switchFilter = (value: QueueFilter) => {
    setReviewFilter(value);
    void load(value, "");
  };

  const submitReview = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return setMessage("请先选择病例");
    setSaving(true);
    setMessage("");
    try {
      await api(`/api/research/cases/${selected}/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(review),
      });
      setReview((current) => ({ ...current, opinion: "", recommendation: "" }));
      await load(reviewFilter, selected);
      setMessage("复核意见已保存，历史版本已保留。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "复核保存失败");
    } finally {
      setSaving(false);
    }
  };

  const postSupplement = async (path: string, body: object, reset: () => void) => {
    if (!selected) return setMessage("请先选择病例");
    try {
      await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      reset();
      await load(reviewFilter, selected);
      setMessage("资料已补录。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    }
  };

  const submitSupplement = (event: FormEvent, path: string, body: object, reset: () => void) => {
    event.preventDefault();
    void postSupplement(path, body, reset);
  };

  const latestVoice = detail?.analyses.filter((item) => item.analysis_type === "voice_analysis").at(-1)?.result;
  const patient = detail?.interview_summary.patient;
  const points = Object.values(detail?.interview_summary.case_points.points ?? {});
  const selectedItem = items.find((item) => item.conversation_id === selected);
  const download = `${apiBaseUrl}/api/research/export?format=csv&name=${encodeURIComponent(name)}&gender=${encodeURIComponent(gender)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`;
  const reportRequestId = latestVoice?.request_id;
  const patientReport = selected && reportRequestId ? `${apiBaseUrl}/api/conversations/${selected}/screening-report?analysis_request_id=${encodeURIComponent(reportRequestId)}&format=html` : "";
  const professionalReport = selected && reportRequestId ? `${apiBaseUrl}/api/research/cases/${selected}/screening-report?analysis_request_id=${encodeURIComponent(reportRequestId)}&format=html` : "";

  return <Modal
    className="research-modal doctor-review-modal"
    open={open}
    onCancel={onClose}
    afterOpenChange={(visible) => { if (visible) { setReviewFilter("pending"); void load("pending", ""); } }}
    width={1280}
    title={<div className="review-title"><span>医生复核工作台</span><small>录音、问诊、检查与意见留痕</small></div>}
    footer={<Button onClick={onClose}>关闭</Button>}
  >
    <div className="review-status-tabs" role="tablist" aria-label="复核状态筛选">
      {(["pending", "in_review", "needs_information", "completed", "all"] as QueueFilter[]).map((status) => (
        <button type="button" role="tab" aria-selected={reviewFilter === status} className={reviewFilter === status ? "active" : ""} key={status} onClick={() => switchFilter(status)}>
          {status === "all" ? "全部" : reviewLabel(status)}<b>{counts[status]}</b>
        </button>
      ))}
    </div>

    <div className="research-toolbar">
      <input placeholder="患者姓名" aria-label="患者姓名" value={name} onChange={(event) => setName(event.target.value)} />
      <input type="date" aria-label="开始日期" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
      <input type="date" aria-label="结束日期" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
      <select aria-label="患者性别" value={gender} onChange={(event) => setGender(event.target.value)}><option value="">全部性别</option><option value="男">男</option><option value="女">女</option><option value="其他/不便透露">其他/不便透露</option></select>
      <Button type="primary" loading={loading} onClick={() => void load(reviewFilter, "")}>检索病例</Button>
      {auth.user.role === "admin" && <a href={download} download>导出匿名 CSV</a>}
    </div>
    {message && <Alert className="review-alert" type={message.includes("失败") || message.includes("不可用") ? "error" : "success"} showIcon message={message} closable onClose={() => setMessage("")} />}

    <div className="research-grid doctor-review-grid">
      <aside className="research-cases review-queue" aria-label="病例复核队列">
        <div className="queue-heading"><h3>病例队列</h3><span>{items.length} 例</span></div>
        {!loading && items.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选下暂无病例" />}
        {items.map((item) => {
          return <button type="button" key={item.conversation_id} className={item.conversation_id === selected ? "research-case active" : "research-case"} onClick={() => void loadCase(item.conversation_id)}>
            <span className="case-main"><strong>{item.patient.name}</strong><small>{item.patient.age == null ? "年龄未登记" : `${item.patient.age} 岁`} · {item.patient.gender} · {item.title.split("·").slice(1).join("·").trim() || "待补主诉"}</small></span>
            <Tag color={reviewColor(item.review.review_status)}>{reviewLabel(item.review.review_status)}</Tag>
            <span className="case-meta">{formatTime(item.updated_at)} · 录音 {item.audio_count} · 检查 {item.examination_count}</span>
          </button>;
        })}
      </aside>

      <section className="research-detail doctor-review-detail">
        {!detail || !selectedItem ? <Empty description={loading ? "正在加载病例…" : "请选择一例病例"} /> : <>
          <header className="case-review-header">
            <div><h2>{patient?.name || selectedItem.patient.name}</h2><p>病例号 {selected.slice(0, 12).toUpperCase()} · 建档 {formatTime(selectedItem.created_at)}</p></div>
            <div className="case-review-actions">
              {reportRequestId && <Button size="small" href={patientReport} target="_blank" rel="noreferrer">患者版报告</Button>}
              {reportRequestId && <Button size="small" type="primary" href={professionalReport} target="_blank" rel="noreferrer">专业版报告</Button>}
              <Tag color={reviewColor(selectedItem.review.review_status)}>{reviewLabel(selectedItem.review.review_status)}</Tag>
            </div>
          </header>

          {detail.interview_summary.case_points.urgent && <Alert type="error" showIcon message="问诊记录包含危险信号，请优先核实线下处置情况。" />}

          <div className="review-summary-grid">
            <article><span>患者概况</span><strong>{patient?.age ?? "—"} 岁 · {patient?.gender || "未登记"}</strong><small>{patient?.location || "地区未登记"}</small></article>
            <article><span>主诉</span><strong>{patient?.chief_complaint || "未登记"}</strong><small>问诊对话 {detail.interview_summary.user_message_count} 条</small></article>
            <article><span>采集质量</span><strong>{detail.quality.status === "ready" ? "资料链完整" : "资料待补充"}</strong><small>{detail.quality.issues.join("；") || "录音、标签、检查及随访齐全"}</small></article>
            <article><span>模型提示</span><strong>{latestVoice?.predicted_label || "暂无六元音结果"}</strong><small>{latestVoice?.predicted_probability != null ? `最高概率 ${(latestVoice.predicted_probability * 100).toFixed(1)}%，仅供筛查参考` : "等待完成采集分析"}</small></article>
          </div>

          <div className="review-content-grid">
            <div className="review-evidence-column">
              <section className="review-panel">
                <div className="panel-heading"><h3>问诊摘要</h3><Tag>{detail.interview_summary.case_points.collected_count ?? 0}/5 要点</Tag></div>
                <dl className="case-point-list">
                  {points.map((point) => <div key={point.label}><dt>{point.label}</dt><dd>{point.value || "未采集"}</dd></div>)}
                  <div><dt>吸烟量化</dt><dd>{historyText(detail.interview_summary.smoking_history?.smoking_status, detail.interview_summary.smoking_history?.smoking_years, detail.interview_summary.smoking_history?.cigarettes_per_day, "支/日")}</dd></div>
                  <div><dt>饮酒量化</dt><dd>{historyText(detail.interview_summary.alcohol_history?.drinking_status, detail.interview_summary.alcohol_history?.drinking_years, detail.interview_summary.alcohol_history?.standard_drinks_per_day, "标准杯/日")}</dd></div>
                </dl>
                <details className="review-disclosure"><summary>查看完整问诊记录（{detail.conversation.messages.length} 条）</summary><div className="transcript">{detail.conversation.messages.map((item) => <div className={item.role} key={item.id}><b>{item.role === "user" ? "患者" : "助手"}</b><p>{item.content}</p><time>{formatTime(item.created_at)}</time></div>)}</div></details>
              </section>

              <section className="review-panel">
                <div className="panel-heading"><h3>录音回放</h3><Tag color={detail.audio_assets.length ? "cyan" : "default"}>{detail.audio_assets.length} 条</Tag></div>
                {detail.audio_assets.length === 0 ? <p className="review-empty-text">暂无已保存录音。</p> : <div className="audio-review-list">{detail.audio_assets.map((asset) => <article key={asset.id}><div><strong>{asset.vowel_key ? vowelNames[asset.vowel_key] || asset.vowel_key : "嗓音录音"}</strong><small>{asset.original_filename} · {(asset.size_bytes / 1024).toFixed(0)} KB</small></div>{asset.playback_url ? <audio controls preload="none" src={`${apiBaseUrl}${asset.playback_url}`}>浏览器不支持音频播放。</audio> : <Tag>服务重启后可回放</Tag>}</article>)}</div>}
              </section>

              <section className="review-panel">
                <div className="panel-heading"><h3>模型筛查结果</h3><span>模型结果不得替代临床判断</span></div>
                {!latestVoice ? <p className="review-empty-text">暂无模型分析。</p> : <>
                  <div className="model-result-head"><strong>{latestVoice.predicted_label}</strong><span>{latestVoice.predicted_probability != null ? `${(latestVoice.predicted_probability * 100).toFixed(1)}%` : "—"}</span></div>
                  <div className="probability-list">{latestVoice.probabilities?.map((item) => <div key={item.label}><span>{item.label}</span><i><em style={{ width: `${Math.max(0, Math.min(100, item.probability * 100))}%` }} /></i><b>{(item.probability * 100).toFixed(1)}%</b></div>)}</div>
                  {latestVoice.warnings?.map((warning) => <p className="model-warning" key={warning}>{warning}</p>)}
                </>}
              </section>

              <section className="review-panel">
                <div className="panel-heading"><h3>检查资料</h3><Tag>{detail.examinations.length} 项</Tag></div>
                {detail.examinations.length === 0 ? <p className="review-empty-text">尚未录入喉镜或其他检查资料。</p> : <div className="examination-list">{detail.examinations.map((item) => <article key={item.id}><div><strong>{item.examination_type}</strong><time>{item.examination_date || formatTime(item.created_at)}</time></div><p>{item.findings}</p><small>记录：{item.clinician}</small></article>)}</div>}
              </section>
            </div>

            <aside className="review-decision-column">
              <form className="review-decision-form" onSubmit={submitReview}>
                <div className="panel-heading"><h3>医生复核意见</h3><Tag color="blue">追加留痕</Tag></div>
                <label>复核状态<select required value={review.review_status} onChange={(event) => setReview({ ...review, review_status: event.target.value as Exclude<ReviewStatus, "pending"> })}><option value="in_review">复核中</option><option value="completed">已完成</option><option value="needs_information">需补充资料</option></select></label>
                <label>复核医生<input required readOnly value={auth.user.display_name} /></label>
                <label>复核意见<textarea required rows={6} maxLength={3000} placeholder="记录对录音、问诊资料、模型结果和检查资料的综合判断" value={review.opinion} onChange={(event) => setReview({ ...review, opinion: event.target.value })} /></label>
                <label>下一步建议<textarea rows={4} maxLength={2000} placeholder="例如：补充电子喉镜、门诊复查或按期随访" value={review.recommendation} onChange={(event) => setReview({ ...review, recommendation: event.target.value })} /></label>
                <Button type="primary" htmlType="submit" loading={saving} block>保存复核记录</Button>
                <small>保存后生成新版本，既往意见不会被覆盖。</small>
              </form>

              <section className="review-panel review-history">
                <div className="panel-heading"><h3>复核轨迹</h3><span>{detail.reviews.length} 个版本</span></div>
                {detail.reviews.length === 0 ? <p className="review-empty-text">尚无复核记录。</p> : [...detail.reviews].reverse().map((item) => <article key={item.id}><div><Tag color={reviewColor(item.review_status)}>{reviewLabel(item.review_status)}</Tag><b>v{item.version}</b><time>{formatTime(item.created_at)}</time></div><strong>{item.reviewer}</strong><p>{item.opinion}</p>{item.recommendation && <small>建议：{item.recommendation}</small>}</article>)}
              </section>
            </aside>
          </div>

          <details className="research-supplement">
            <summary>科研资料补录</summary>
            <div className="research-forms">
              <form onSubmit={(event) => submitSupplement(event, `/api/research/cases/${selected}/labels`, label, () => setLabel({ ...label, label_value: "", notes: "" }))}><h4>临床标签</h4><input placeholder="标签值/诊断" required value={label.label_value} onChange={(event) => setLabel({ ...label, label_value: event.target.value })} /><select value={label.label_status} onChange={(event) => setLabel({ ...label, label_status: event.target.value })}><option value="preliminary">初步</option><option value="confirmed">确认</option><option value="rejected">排除</option></select><input readOnly value={auth.user.display_name} /><Button htmlType="submit" size="small">保存标签</Button></form>
              <form onSubmit={(event) => submitSupplement(event, `/api/research/cases/${selected}/examinations`, exam, () => setExam({ ...exam, findings: "" }))}><h4>检查结果</h4><input type="date" value={exam.examination_date} onChange={(event) => setExam({ ...exam, examination_date: event.target.value })} /><textarea placeholder="检查发现" required value={exam.findings} onChange={(event) => setExam({ ...exam, findings: event.target.value })} /><input readOnly value={auth.user.display_name} /><Button htmlType="submit" size="small">保存检查</Button></form>
              <form onSubmit={(event) => submitSupplement(event, `/api/research/cases/${selected}/follow-ups`, follow, () => setFollow({ ...follow, follow_up_date: "", outcome_notes: "" }))}><h4>随访结局</h4><input type="date" required value={follow.follow_up_date} onChange={(event) => setFollow({ ...follow, follow_up_date: event.target.value })} /><textarea placeholder="随访记录" required value={follow.outcome_notes} onChange={(event) => setFollow({ ...follow, outcome_notes: event.target.value })} /><input readOnly value={auth.user.display_name} /><Button htmlType="submit" size="small">保存随访</Button></form>
            </div>
          </details>

          {auth.user.role === "admin" && <details className="research-supplement">
            <summary>模型与实验登记</summary>
            <div className="research-model-grid">
              <form onSubmit={(event) => { event.preventDefault(); void api("/api/research/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...model, metrics: {} }) }).then(() => load(reviewFilter, selected)).catch((error: Error) => setMessage(error.message)); }}><h3>模型版本</h3><input placeholder="模型名称" required value={model.model_name} onChange={(event) => setModel({ ...model, model_name: event.target.value })} /><input placeholder="版本" required value={model.version} onChange={(event) => setModel({ ...model, version: event.target.value })} /><input placeholder="模型类型" required value={model.model_type} onChange={(event) => setModel({ ...model, model_type: event.target.value })} /><input placeholder="权重/代码引用" required value={model.artifact_reference} onChange={(event) => setModel({ ...model, artifact_reference: event.target.value })} /><Button htmlType="submit" size="small">登记模型</Button></form>
              <form onSubmit={(event) => { event.preventDefault(); void api("/api/research/experiments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...experiment, random_seed: Number(experiment.random_seed), metrics: {}, notes: "" }) }).then(() => setMessage("实验记录已保存")).catch((error: Error) => setMessage(error.message)); }}><h3>实验运行</h3><select required value={experiment.model_version_id} onChange={(event) => setExperiment({ ...experiment, model_version_id: event.target.value })}><option value="">选择模型</option>{models.map((item) => <option key={item.id} value={item.id}>{item.model_name} · {item.version}</option>)}</select><input placeholder="数据集版本" required value={experiment.dataset_version} onChange={(event) => setExperiment({ ...experiment, dataset_version: event.target.value })} /><input placeholder="划分策略" required value={experiment.split_strategy} onChange={(event) => setExperiment({ ...experiment, split_strategy: event.target.value })} /><Button htmlType="submit" size="small">登记实验</Button></form>
            </div>
          </details>}
        </>}
      </section>
    </div>
  </Modal>;
}
