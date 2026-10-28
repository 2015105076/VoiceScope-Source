"use client";

import {
  AudioOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseOutlined,
  CustomerServiceOutlined,
  FileImageOutlined,
  HistoryOutlined,
  InfoCircleOutlined,
  MenuOutlined,
  MessageOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PlusOutlined,
  RobotOutlined,
  SafetyCertificateFilled,
  SendOutlined,
  SoundOutlined,
  UserOutlined,
  WarningFilled,
} from "@ant-design/icons";
import { Avatar, Badge, Button, Modal, Progress, Tag, Tooltip } from "antd";
import { ChangeEvent, KeyboardEvent, useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { authFetch, useAuth } from "@/components/auth-context";
import ResearchManagement from "@/components/research-management";
import SecurityManagement from "@/components/security-management";

type MessageRole = "assistant" | "user" | "event";

type ChatMessage = {
  id: number;
  role: MessageRole;
  content: string;
  time: string;
};

type PatientFacts = {
  duration?: string;
  pattern?: string;
  smoking?: string;
  smokingYears?: string;
  cigarettesPerDay?: string;
  drinking?: string;
  drinkingYears?: string;
  standardDrinksPerDay?: string;
  redFlag?: string;
};

type ConversationSummary = {
  conversation_id: string;
  title: string;
  updated_at: string;
  message_count: number;
  status?: "active" | "completed";
};

type PatientProfile = {
  name: string;
  age: string;
  gender: string;
  location: string;
  chiefComplaint: string;
};

type VowelKey = "a" | "o" | "e" | "i" | "u" | "v";

type AudioQualityIssue = {
  code: string;
  severity: "error" | "warning";
  message: string;
  guidance: string;
};

type AudioQualityReport = {
  filename: string;
  status: "pass" | "warning" | "re_record";
  accepted: boolean;
  metrics: {
    duration_seconds: number;
    active_voice_seconds: number;
    clipped_fraction: number;
    noise_assessment: "measured" | "not_assessed";
    estimated_snr_db: number | null;
  } | null;
  issues: AudioQualityIssue[];
  scope_note: string;
};

type AudioQualityState = Record<VowelKey, AudioQualityReport | null>;

type VoiceAnalysisResult = {
  request_id: string;
  predicted_label: string;
  predicted_probability: number;
  probabilities: Array<{ label: string; probability: number }>;
  vowel_attention: Record<string, number>;
  audio_quality: Record<VowelKey, AudioQualityReport>;
  warnings: string[];
  disclaimer: string;
};

type VoiceDiagnosisCorrection = {
  correction_id: string;
  conversation_id: string;
  model_request_id: string;
  original_predicted_label: string;
  original_predicted_probability: number;
  corrected_label: string;
  note: string;
  correction_number: number;
  created_at: string;
};

type AcousticAnalysisResult = {
  request_id: string;
  recording_duration_seconds: number;
  mpt_seconds: number;
  f0_hz: number | null;
  jitter_percent: number | null;
  shimmer_percent: number | null;
  mean_intensity_dbfs: number;
  signal_type: { grade: "I" | "II" | "III"; description: string };
  capture_quality: AudioQualityReport;
  references: Record<string, string>;
  warnings: string[];
  disclaimer: string;
};

type ConversationEvent = {
  id: number;
  kind: string;
  source: string;
  created_at: string;
  payload: VoiceAnalysisResult | AcousticAnalysisResult | VoiceDiagnosisCorrection;
};

function detectionMessage(event: ConversationEvent): ChatMessage | null {
  if (event.kind === "acoustic_analysis") {
    const result = event.payload as AcousticAnalysisResult;
    return { id: -event.id, role: "event", content: `算法测量：MPT ${result.mpt_seconds.toFixed(1)} 秒，未进行疾病分类。`, time: formatHistoryTime(event.created_at) };
  }
  if (event.kind === "voice_analysis") {
    const result = event.payload as VoiceAnalysisResult;
    return { id: -event.id, role: "event", content: `研究模型输出：${result.predicted_label}（${(result.predicted_probability * 100).toFixed(1)}%），不代表临床诊断。`, time: formatHistoryTime(event.created_at) };
  }
  if (event.kind === "voice_diagnosis_correction") {
    const correction = event.payload as VoiceDiagnosisCorrection;
    return { id: -event.id, role: "event", content: `人工纠正：${correction.original_predicted_label} → ${correction.corrected_label}，原模型结果已保留。`, time: formatHistoryTime(event.created_at) };
  }
  return null;
}

type VhiScores = { functional: string; physical: string; emotional: string };
type GrbasScores = { g: string; r: string; b: string; a: string; s: string };

type RecordingSession = {
  key: VowelKey;
  stream: MediaStream;
  audioContext: AudioContext;
  source: MediaStreamAudioSourceNode;
  processor: ScriptProcessorNode;
  silentGain: GainNode;
  chunks: Float32Array[];
  sampleRate: number;
  startedAt: number;
};

const vowelInputs: Array<{ key: VowelKey; label: string; guide: string }> = [
  { key: "a", label: "/a/", guide: "啊" },
  { key: "o", label: "/o/", guide: "喔" },
  { key: "e", label: "/e/", guide: "鹅" },
  { key: "i", label: "/i/", guide: "衣" },
  { key: "u", label: "/u/", guide: "乌" },
  { key: "v", label: "/ü/", guide: "迂" },
];

const emptyVoiceFiles: Record<VowelKey, File | null> = {
  a: null,
  o: null,
  e: null,
  i: null,
  u: null,
  v: null,
};

const emptyVoiceQuality: AudioQualityState = {
  a: null,
  o: null,
  e: null,
  i: null,
  u: null,
  v: null,
};
const emptyQualityChecking: Record<VowelKey, boolean> = { a: false, o: false, e: false, i: false, u: false, v: false };

const emptyVhiScores: VhiScores = { functional: "", physical: "", emotional: "" };
const emptyGrbasScores: GrbasScores = { g: "", r: "", b: "", a: "", s: "" };
const emptyPatientProfile: PatientProfile = { name: "", age: "", gender: "", location: "", chiefComplaint: "" };
const subscribeToClientReady = () => () => {};
const getClientReadySnapshot = () => true;
const getServerClientReadySnapshot = () => false;

const initialMessages: ChatMessage[] = [];

type CaseField = "symptom" | "duration" | "pattern" | "smoking" | "drinking";
type CasePoint = { label: string; value: string; status: "missing" | "needs_detail" | "collected"; evidence: Array<{ source_id: string; quote: string }> };
type CaseState = {
  points: Record<CaseField, CasePoint>;
  collected_count: number;
  next_field: CaseField | null;
  question: string;
  options: string[];
  ready_for_voice: boolean;
  urgent: boolean;
};
const caseFields: CaseField[] = ["symptom", "duration", "pattern", "smoking", "drinking"];
const caseLabels = ["主要症状", "持续时间", "症状模式", "吸烟史", "饮酒史"];

const apiBaseUrl = (process.env.NEXT_PUBLIC_VOICESCOPE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

function getCurrentTime() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

function formatHistoryTime(timestamp: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(timestamp));
}

function formatApiError(error: unknown, fallback: string) {
  if (error instanceof Error && /Failed to fetch|NetworkError|Load failed/i.test(error.message)) {
    return "无法连接问诊服务，请确认后端已启动（127.0.0.1:8000）后重试。";
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function encodePcmAsWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, text: string) => {
    [...text].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => {
    const normalized = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + index * 2, normalized < 0 ? normalized * 0x8000 : normalized * 0x7fff, true);
  });
  return new Blob([buffer], { type: "audio/wav" });
}

export default function ChatWorkspace() {
  const auth = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [caseState, setCaseState] = useState<CaseState | null>(null);
  const [mobileGuideOpen, setMobileGuideOpen] = useState(false);
  const [researchOpen, setResearchOpen] = useState(auth.user.role !== "patient");
  const [securityOpen, setSecurityOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [typing, setTyping] = useState(false);
  const [recordingKey, setRecordingKey] = useState<VowelKey | null>(null);
  const [recordingStarting, setRecordingStarting] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [largeTextEnabled, setLargeTextEnabled] = useState(false);
  const [textScale, setTextScale] = useState(1.2);
  const [facts, setFacts] = useState<PatientFacts>({});
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationStatus, setConversationStatus] = useState<"active" | "completed">("active");
  const [patientProfile, setPatientProfile] = useState<PatientProfile>(emptyPatientProfile);
  const [patientProfileDraft, setPatientProfileDraft] = useState<PatientProfile>(emptyPatientProfile);
  const [patientInfoOpen, setPatientInfoOpen] = useState(auth.user.role === "patient");
  const clientReady = useSyncExternalStore(subscribeToClientReady, getClientReadySnapshot, getServerClientReadySnapshot);
  const [patientInfoSaving, setPatientInfoSaving] = useState(false);
  const [patientInfoError, setPatientInfoError] = useState("");
  const [recentConversations, setRecentConversations] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [searchDateFrom, setSearchDateFrom] = useState("");
  const [searchDateTo, setSearchDateTo] = useState("");
  const [searchName, setSearchName] = useState("");
  const [searchGender, setSearchGender] = useState("");
  const [searchMessage, setSearchMessage] = useState("");
  const [searchOpen, setSearchOpen] = useState(true);
  const [sessionPage, setSessionPage] = useState(1);
  const [smokingSaving, setSmokingSaving] = useState(false);
  const [smokingSaveMessage, setSmokingSaveMessage] = useState("");
  const [alcoholSaving, setAlcoholSaving] = useState(false);
  const [alcoholSaveMessage, setAlcoholSaveMessage] = useState("");
  const [voicePanelOpen, setVoicePanelOpen] = useState(false);
  const [voiceFiles, setVoiceFiles] = useState<Record<VowelKey, File | null>>(emptyVoiceFiles);
  const [voiceQuality, setVoiceQuality] = useState<AudioQualityState>(emptyVoiceQuality);
  const [qualityChecking, setQualityChecking] = useState<Record<VowelKey, boolean>>(emptyQualityChecking);
  const [voiceAnalyzing, setVoiceAnalyzing] = useState(false);
  const [acousticAnalyzing, setAcousticAnalyzing] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [voiceResult, setVoiceResult] = useState<VoiceAnalysisResult | null>(null);
  const [voiceCorrection, setVoiceCorrection] = useState<VoiceDiagnosisCorrection | null>(null);
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionLabel, setCorrectionLabel] = useState("");
  const [correctionNote, setCorrectionNote] = useState("");
  const [correctionSaving, setCorrectionSaving] = useState(false);
  const [correctionError, setCorrectionError] = useState("");
  const [acousticResult, setAcousticResult] = useState<AcousticAnalysisResult | null>(null);
  const [vhiScores, setVhiScores] = useState<VhiScores>(emptyVhiScores);
  const [rsiScore, setRsiScore] = useState("");
  const [vowelGrbas, setVowelGrbas] = useState<GrbasScores>(emptyGrbasScores);
  const [speechGrbas, setSpeechGrbas] = useState<GrbasScores>(emptyGrbasScores);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeRecorderRef = useRef<RecordingSession | null>(null);
  const qualityRequestSequence = useRef<Record<VowelKey, number>>({ a: 0, o: 0, e: 0, i: 0, u: 0, v: 0 });

  useEffect(() => {
    const savedEnabled = window.localStorage.getItem("voicescope-large-text") === "true";
    const savedScale = Number(window.localStorage.getItem("voicescope-text-scale"));
    setLargeTextEnabled(savedEnabled);
    if (Number.isFinite(savedScale)) setTextScale(Math.min(1.5, Math.max(1.1, savedScale)));
  }, []);

  useEffect(() => {
    window.localStorage.setItem("voicescope-large-text", String(largeTextEnabled));
    window.localStorage.setItem("voicescope-text-scale", String(textScale));
  }, [largeTextEnabled, textScale]);

  const operationInFlight = useRef(false);
  const messageSequence = useRef(Date.now());
  const nextMessageId = () => ++messageSequence.current;
  const beginOperation = useCallback(() => {
    if (operationInFlight.current) return false;
    operationInFlight.current = true;
    return true;
  }, []);
  const isWorking = typing || historyLoading || patientInfoSaving || smokingSaving || alcoholSaving || voiceAnalyzing || acousticAnalyzing || correctionSaving;
  const consultationCompleted = conversationStatus === "completed";
  const allVoiceQualityAccepted = vowelInputs.every(({ key }) => Boolean(voiceFiles[key]) && voiceQuality[key]?.accepted === true && !qualityChecking[key]);
  const patientReportUrl = conversationId && voiceResult
    ? `${apiBaseUrl}/api/conversations/${conversationId}/screening-report?analysis_request_id=${encodeURIComponent(voiceResult.request_id)}&format=html`
    : "";

  const progress = (caseState?.collected_count ?? 0) * 20;
  const redFlag = caseState?.urgent ? "请优先线下评估" : undefined;
  const options = caseState?.options ?? [];
  const sessionPageSize = 5;
  const sessionPageCount = Math.max(1, Math.ceil(recentConversations.length / sessionPageSize));
  const visibleConversations = recentConversations.slice((sessionPage - 1) * sessionPageSize, sessionPage * sessionPageSize);
  const vhiTotal = Object.values(vhiScores).every((value) => value !== "")
    ? Object.values(vhiScores).reduce((total, value) => total + Number(value), 0)
    : null;
  const formatGrbas = (scores: GrbasScores) => (
    Object.values(scores).every((value) => value !== "")
      ? `G${scores.g}; R${scores.r}; B${scores.b}; A${scores.a}; S${scores.s}`
      : "待专业评定"
  );
  const smokingHistoryComplete = Boolean(facts.smoking) && (
    facts.smoking === "从不吸烟" || (Boolean(facts.smokingYears) && Boolean(facts.cigarettesPerDay))
  );
  const alcoholHistoryComplete = Boolean(facts.drinking) && (
    facts.drinking === "从不饮酒" || (Boolean(facts.drinkingYears) && Boolean(facts.standardDrinksPerDay))
  );
  const completedFacts = caseFields.map((field, index) => {
    const point = caseState?.points[field];
    return { label: caseLabels[index], value: point?.value || "待补充", done: point?.status === "collected",
             evidence: point?.evidence.map((item) => item.quote).join("；") || "" };
  });

  function openVoiceCapture(upload = false) {
    if (!caseState?.ready_for_voice || isWorking) return;
    setVoicePanelOpen(true);
    if (upload) window.setTimeout(() => document.getElementById("voice-file-a")?.click(), 0);
  }

  const refreshRecentConversations = useCallback(async () => {
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations`);
      if (!response.ok) return;
      const data: { conversations: ConversationSummary[] } = await response.json();
      setRecentConversations(data.conversations);
      setSessionPage(1);
    } catch {
      // 设备离线时不清空已加载的本地页面状态。
    }
  }, []);

  const searchConversations = useCallback(async () => {
    const params = new URLSearchParams();
    if (searchDateFrom) params.set("date_from", searchDateFrom);
    if (searchDateTo) params.set("date_to", searchDateTo);
    if (searchName.trim()) params.set("name", searchName.trim());
    if (searchGender) params.set("gender", searchGender);
    setHistoryLoading(true);
    setSearchMessage("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/search?${params.toString()}`);
      const data = await response.json() as { conversations?: ConversationSummary[]; detail?: string };
      if (!response.ok) throw new Error(data.detail || "检索失败");
      setRecentConversations(data.conversations ?? []);
      setSessionPage(1);
    } catch (error) {
      setSearchMessage(error instanceof Error ? error.message : "检索失败");
    } finally {
      setHistoryLoading(false);
    }
  }, [searchDateFrom, searchDateTo, searchGender, searchName]);

  async function deleteConversation(targetConversationId: string, title: string) {
    if (isWorking || !window.confirm(`确定删除“${title}”及其全部对话和音频记录吗？`)) return;
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${targetConversationId}`, { method: "DELETE" });
      const data = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(data.detail || "删除失败");
      if (targetConversationId === conversationId) {
        window.localStorage.removeItem("voicescope-conversation-id");
        setConversationId(null); setConversationStatus("active"); setCaseState(null); setMessages(initialMessages); setPatientProfile(emptyPatientProfile); setPatientProfileDraft(emptyPatientProfile); setPatientInfoOpen(true);
      }
      await refreshRecentConversations();
    } catch (error) {
      setSearchMessage(error instanceof Error ? error.message : "删除失败");
    }
  }


  const openConversation = useCallback(async (targetConversationId: string) => {
    if (!beginOperation()) return;
    setHistoryLoading(true);
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${targetConversationId}`);
      if (!response.ok) throw new Error("历史会话不可用");
      const data: {
        messages: Array<{ id: number; role: "assistant" | "user"; content: string; created_at: string }>;
        events?: ConversationEvent[];
        case_points?: CaseState;
        status?: "active" | "completed";
        facts?: {
          patient_info?: { name?: string; age?: number; gender?: string; location?: string; chief_complaint?: string };
          smoking_history?: { smoking_status?: string; smoking_years?: number | null; cigarettes_per_day?: number | null };
          alcohol_history?: { drinking_status?: string; drinking_years?: number | null; standard_drinks_per_day?: number | null };
        };
      } = await response.json();
      const storedPatientInfo = data.facts?.patient_info;
      const smokingHistory = data.facts?.smoking_history;
      const alcoholHistory = data.facts?.alcohol_history;
      const restoredPatientProfile: PatientProfile = {
        name: storedPatientInfo?.name ?? "",
        age: storedPatientInfo?.age?.toString() ?? "",
        gender: storedPatientInfo?.gender ?? "",
        location: storedPatientInfo?.location ?? "",
        chiefComplaint: storedPatientInfo?.chief_complaint ?? "",
      };
      setPatientProfile(restoredPatientProfile);
      setPatientProfileDraft(restoredPatientProfile);
      setPatientInfoOpen(false);
      setConversationId(targetConversationId);
      setConversationStatus(data.status ?? "active");
      const timeline = [
        ...data.messages.map((message) => ({ created_at: message.created_at, message: {
          id: message.id, role: message.role, content: message.content, time: formatHistoryTime(message.created_at),
        } as ChatMessage })),
        ...(data.events ?? []).flatMap((event) => {
          const message = detectionMessage(event);
          return message ? [{ created_at: event.created_at, message }] : [];
        }),
      ];
      timeline.sort((a, b) => a.created_at.localeCompare(b.created_at));
      setMessages(timeline.map((item) => item.message));
      setDraft("");
      setFacts({
        smoking: smokingHistory?.smoking_status,
        smokingYears: smokingHistory?.smoking_years?.toString() ?? "",
        cigarettesPerDay: smokingHistory?.cigarettes_per_day?.toString() ?? "",
        drinking: alcoholHistory?.drinking_status,
        drinkingYears: alcoholHistory?.drinking_years?.toString() ?? "",
        standardDrinksPerDay: alcoholHistory?.standard_drinks_per_day?.toString() ?? "",
      });
      setCaseState(data.case_points ?? null);
      setMobileGuideOpen(false);
      setVoicePanelOpen(false);
      setVoiceFiles(emptyVoiceFiles);
      setVoiceQuality(emptyVoiceQuality);
      vowelInputs.forEach(({ key }) => { qualityRequestSequence.current[key] += 1; });
      setQualityChecking(emptyQualityChecking);
      const savedEvents = data.events ?? [];
      const savedVoiceResult = (savedEvents.filter((event) => event.kind === "voice_analysis").at(-1)?.payload as VoiceAnalysisResult | undefined) ?? null;
      setVoiceResult(savedVoiceResult);
      setVoiceCorrection(savedVoiceResult
        ? ((savedEvents.filter((event) => event.kind === "voice_diagnosis_correction" && (event.payload as VoiceDiagnosisCorrection).model_request_id === savedVoiceResult.request_id).at(-1)?.payload as VoiceDiagnosisCorrection | undefined) ?? null)
        : null);
      setCorrectionOpen(false);
      setCorrectionLabel("");
      setCorrectionNote("");
      setCorrectionError("");
      setAcousticResult((savedEvents.filter((event) => event.kind === "acoustic_analysis").at(-1)?.payload as AcousticAnalysisResult | undefined) ?? null);
      setVhiScores(emptyVhiScores);
      setRsiScore("");
      setVowelGrbas(emptyGrbasScores);
      setSpeechGrbas(emptyGrbasScores);
      setVoiceError("");
      setSmokingSaveMessage("");
      setAlcoholSaveMessage("");
      window.localStorage.setItem("voicescope-conversation-id", targetConversationId);
      setMobileNavOpen(false);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "无法打开历史会话");
    } finally {
      setHistoryLoading(false);
      operationInFlight.current = false;
    }
  }, [beginOperation]);

  useEffect(() => {
    let active = true;
    async function initialiseHistory() {
      try {
        const recentResponse = await authFetch(`${apiBaseUrl}/api/conversations`);
        if (recentResponse.ok && active) {
          const recentData: { conversations: ConversationSummary[] } = await recentResponse.json();
          setRecentConversations(recentData.conversations);
        }
        const savedConversationId = window.localStorage.getItem("voicescope-conversation-id");
        if (savedConversationId && active) await openConversation(savedConversationId);
      } catch {
        // 离线时保留当前页面的本地引导，不清空已展示的问诊内容。
      }
    }
    void initialiseHistory();
    return () => { active = false; };
  }, [openConversation]);

  useEffect(() => {
    if (!recordingKey) return;
    const updateDuration = () => {
      const session = activeRecorderRef.current;
      if (session) setRecordingSeconds(Math.max(0, Math.floor(session.audioContext.currentTime - session.startedAt)));
    };
    const interval = window.setInterval(updateDuration, 250);
    return () => window.clearInterval(interval);
  }, [recordingKey]);

  useEffect(() => () => {
    const session = activeRecorderRef.current;
    if (!session) return;
    session.processor.onaudioprocess = null;
    session.source.disconnect();
    session.processor.disconnect();
    session.silentGain.disconnect();
    session.stream.getTracks().forEach((track) => track.stop());
    void session.audioContext.close();
    activeRecorderRef.current = null;
  }, []);

  async function sendMessage(value = draft) {
    const cleanValue = value.trim();
    if (!cleanValue || typing) return;
    if (!conversationId) {
      setPatientInfoOpen(true);
      return;
    }
    if (!beginOperation()) return;

    setMessages((current) => [
      ...current,
      { id: nextMessageId(), role: "user", content: cleanValue, time: getCurrentTime() },
    ]);
    setDraft("");
    setTyping(true);
    const assistantMessageId = nextMessageId();
    setMessages((current) => [
      ...current,
      { id: assistantMessageId, role: "assistant", content: "", time: getCurrentTime() },
    ]);

    try {
      const response = await authFetch(`${apiBaseUrl}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: cleanValue, conversation_id: conversationId }),
      });
      if (!response.ok || !response.body) {
        const error = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(error.detail || "问诊服务不可用");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const { value: chunk, done } = await reader.read();
        pending += decoder.decode(chunk ?? new Uint8Array(), { stream: !done });
        const events = pending.split("\n\n");
        pending = events.pop() ?? "";
        for (const eventText of events) {
          const dataLine = eventText.split("\n").find((line) => line.startsWith("data: "));
          if (!dataLine) continue;
          const event = JSON.parse(dataLine.slice(6)) as { type: string; text?: string; conversation_id?: string; case_points?: CaseState };
          if (event.type === "case_points" && event.case_points) {
            setCaseState(event.case_points);
            if (!event.case_points.ready_for_voice) setVoicePanelOpen(false);
          }
          if (event.type === "delta" && event.text) {
            setMessages((current) => current.map((message) => (
              message.id === assistantMessageId ? { ...message, content: `${message.content}${event.text}` } : message
            )));
          }
          if ((event.type === "done" || event.type === "session") && event.conversation_id) {
            setConversationId(event.conversation_id);
            window.localStorage.setItem("voicescope-conversation-id", event.conversation_id);
            void refreshRecentConversations();
          }
        }
        if (done) break;
      }

    } catch (error) {
      setMessages((current) => [
        ...current.filter((message) => message.id !== assistantMessageId),
        { id: assistantMessageId, role: "assistant", content: error instanceof Error ? error.message : "无法连接问诊服务，请稍后重试。", time: getCurrentTime() },
      ]);
    } finally {
      setTyping(false);
      operationInFlight.current = false;
    }
  }

  async function saveSmokingHistory() {
    if (!conversationId) {
      setSmokingSaveMessage("请先发送一条问诊消息，再保存吸烟史用于后续随访。");
      return;
    }
    if (!facts.smoking) {
      setSmokingSaveMessage("请先选择吸烟状态。");
      return;
    }
    if (facts.smoking !== "从不吸烟" && (!facts.smokingYears || !facts.cigarettesPerDay)) {
      setSmokingSaveMessage("请填写吸烟年限和平均每日支数。");
      return;
    }
    if (!beginOperation()) return;
    setSmokingSaving(true);
    setSmokingSaveMessage("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${conversationId}/smoking-history`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          smoking_status: facts.smoking,
          smoking_years: facts.smokingYears ? Number(facts.smokingYears) : null,
          cigarettes_per_day: facts.cigarettesPerDay ? Number(facts.cigarettesPerDay) : null,
        }),
      });
      const data = await response.json() as { detail?: string; case_points?: CaseState };
      if (!response.ok) throw new Error(data.detail || "吸烟史保存失败");
      if (data.case_points) setCaseState(data.case_points);
      setSmokingSaveMessage("吸烟史已保存，可在历史会话中继续随访。");
      void refreshRecentConversations();
    } catch (error) {
      setSmokingSaveMessage(error instanceof Error ? error.message : "吸烟史保存失败");
    } finally {
      operationInFlight.current = false;
      setSmokingSaving(false);
    }
  }

  async function saveAlcoholHistory() {
    if (!conversationId) {
      setAlcoholSaveMessage("请先发送一条问诊消息，再保存饮酒史用于后续随访。");
      return;
    }
    if (!facts.drinking) {
      setAlcoholSaveMessage("请先选择饮酒状态。");
      return;
    }
    if (facts.drinking !== "从不饮酒" && (!facts.drinkingYears || !facts.standardDrinksPerDay)) {
      setAlcoholSaveMessage("请填写饮酒年限和平均每日饮酒量。");
      return;
    }
    if (!beginOperation()) return;
    setAlcoholSaving(true);
    setAlcoholSaveMessage("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${conversationId}/alcohol-history`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          drinking_status: facts.drinking,
          drinking_years: facts.drinkingYears ? Number(facts.drinkingYears) : null,
          standard_drinks_per_day: facts.standardDrinksPerDay ? Number(facts.standardDrinksPerDay) : null,
        }),
      });
      const data = await response.json() as { detail?: string; case_points?: CaseState };
      if (!response.ok) throw new Error(data.detail || "饮酒史保存失败");
      if (data.case_points) setCaseState(data.case_points);
      setAlcoholSaveMessage("饮酒史已保存，可在历史会话中继续随访。");
      void refreshRecentConversations();
    } catch (error) {
      setAlcoholSaveMessage(error instanceof Error ? error.message : "饮酒史保存失败");
    } finally {
      operationInFlight.current = false;
      setAlcoholSaving(false);
    }
  }

  function openPatientIntake() {
    if (operationInFlight.current || recordingKey || recordingStarting) return;
    setPatientProfileDraft(emptyPatientProfile);
    setPatientInfoError("");
    setPatientInfoOpen(true);
    setMobileNavOpen(false);
  }

  async function createPatientConversation() {
    const profile: PatientProfile = {
      name: patientProfileDraft.name.trim(),
      age: patientProfileDraft.age.trim(),
      gender: patientProfileDraft.gender,
      location: patientProfileDraft.location.trim(),
      chiefComplaint: patientProfileDraft.chiefComplaint.trim(),
    };
    const age = Number(profile.age);
    if (!profile.name || !profile.location || !profile.gender || !profile.chiefComplaint || !Number.isInteger(age) || age < 0 || age > 120) {
      setPatientInfoError("请完整填写姓名、年龄、性别、所在地和主要症状；年龄须为 0–120 的整数。");
      return;
    }
    if (!beginOperation()) return;
    setPatientInfoSaving(true);
    setPatientInfoError("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: profile.name,
          age,
          gender: profile.gender,
          location: profile.location,
          chief_complaint: profile.chiefComplaint,
        }),
      });
      const data = await response.json() as { conversation_id?: string; detail?: string; status?: "active" | "completed"; case_points?: CaseState; messages?: Array<{ id: number; role: "assistant" | "user"; content: string; created_at: string }> };
      if (!response.ok || !data.conversation_id) throw new Error(data.detail || "无法创建问诊会话");
      setPatientProfile(profile);
      setPatientProfileDraft(profile);
      setConversationId(data.conversation_id);
      setConversationStatus(data.status ?? "active");
      setMessages((data.messages ?? []).map((message) => ({ ...message, time: formatHistoryTime(message.created_at) })));
      setCaseState(data.case_points ?? null);
      setMobileGuideOpen(false);
      setFacts({});
      setDraft("");
      setTyping(false);
      setVoicePanelOpen(false);
      setVoiceFiles(emptyVoiceFiles);
      setVoiceQuality(emptyVoiceQuality);
      vowelInputs.forEach(({ key }) => { qualityRequestSequence.current[key] += 1; });
      setQualityChecking(emptyQualityChecking);
      setVoiceAnalyzing(false);
      setAcousticAnalyzing(false);
      setVoiceError("");
      setVoiceResult(null);
      setVoiceCorrection(null);
      setCorrectionOpen(false);
      setCorrectionLabel("");
      setCorrectionNote("");
      setCorrectionError("");
      setAcousticResult(null);
      setVhiScores(emptyVhiScores);
      setRsiScore("");
      setVowelGrbas(emptyGrbasScores);
      setSpeechGrbas(emptyGrbasScores);
      setSmokingSaving(false);
      setSmokingSaveMessage("");
      setAlcoholSaving(false);
      setAlcoholSaveMessage("");
      window.localStorage.setItem("voicescope-conversation-id", data.conversation_id);
      setPatientInfoOpen(false);
      void refreshRecentConversations();
    } catch (error) {
      setPatientInfoError(formatApiError(error, "无法创建问诊会话，请稍后重试。"));
    } finally {
      operationInFlight.current = false;
      setPatientInfoSaving(false);
    }
  }

  async function completeConsultation() {
    if (!conversationId || consultationCompleted || !voiceResult || !window.confirm("确认结束本次咨询吗？结束后不能继续发送问诊或新增嗓音测试。")) return;
    setVoiceError("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${conversationId}/complete`, { method: "POST" });
      const data = await response.json() as { status?: "completed"; detail?: string };
      if (!response.ok || data.status !== "completed") throw new Error(data.detail || "结束咨询失败");
      setConversationStatus("completed");
      setMessages((current) => [...current, { id: nextMessageId(), role: "event", content: "本次咨询已结束，状态已更新为已完成诊疗。", time: getCurrentTime() }]);
      void refreshRecentConversations();
    } catch (error) { setVoiceError(error instanceof Error ? error.message : "结束咨询失败"); }
  }

  function openDiagnosisCorrection() {
    if (!voiceResult) return;
    const alternatives = voiceResult.probabilities.filter((item) => item.label !== voiceResult.predicted_label);
    setCorrectionLabel(voiceCorrection?.corrected_label ?? alternatives[0]?.label ?? "");
    setCorrectionNote(voiceCorrection?.note ?? "");
    setCorrectionError("");
    setCorrectionOpen(true);
  }

  async function saveDiagnosisCorrection() {
    if (!conversationId || !voiceResult || !correctionLabel || correctionSaving) return;
    if (!beginOperation()) return;
    setCorrectionSaving(true);
    setCorrectionError("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/conversations/${conversationId}/voice-diagnosis-corrections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_request_id: voiceResult.request_id, corrected_label: correctionLabel, note: correctionNote }),
      });
      const data = await response.json() as VoiceDiagnosisCorrection | { detail?: string };
      if (!response.ok) throw new Error("detail" in data ? data.detail || "纠正结果保存失败" : "纠正结果保存失败");
      const correction = data as VoiceDiagnosisCorrection;
      setVoiceCorrection(correction);
      setCorrectionOpen(false);
      setMessages((current) => [...current, {
        id: nextMessageId(), role: "event",
        content: `人工纠正已保存：${correction.original_predicted_label} → ${correction.corrected_label}，原模型结果仍保留。`,
        time: getCurrentTime(),
      }]);
      void refreshRecentConversations();
    } catch (error) {
      setCorrectionError(error instanceof Error ? error.message : "纠正结果保存失败");
    } finally {
      operationInFlight.current = false;
      setCorrectionSaving(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setMessages((current) => [
      ...current,
      {
        id: nextMessageId(),
        role: "event",
        content: `已添加资料：${file.name}（演示模式下不会上传或分析）`,
        time: getCurrentTime(),
      },
    ]);
    event.target.value = "";
  }

  function applyQualityFailure(detail: unknown): string {
    if (detail && typeof detail === "object") {
      const structured = detail as { message?: string; quality_control?: Partial<AudioQualityState> };
      if (structured.quality_control) {
        setVoiceQuality((current) => ({ ...current, ...structured.quality_control }));
      }
      if (structured.message) return structured.message;
    }
    return typeof detail === "string" ? detail : "录音质量检查失败，请按对应元音提示重试。";
  }

  async function checkVoiceQuality(key: VowelKey, file: File) {
    const requestId = ++qualityRequestSequence.current[key];
    setQualityChecking((current) => ({ ...current, [key]: true }));
    setVoiceQuality((current) => ({ ...current, [key]: null }));
    const form = new FormData();
    form.append("vowel_key", key);
    form.append("audio", file);
    try {
      const response = await authFetch(`${apiBaseUrl}/api/voice/quality-check`, { method: "POST", body: form });
      const data = await response.json() as { quality?: AudioQualityReport; detail?: unknown };
      if (!response.ok || !data.quality) throw new Error(applyQualityFailure(data.detail));
      if (qualityRequestSequence.current[key] !== requestId) return;
      setVoiceQuality((current) => ({ ...current, [key]: data.quality ?? null }));
      if (!data.quality.accepted) {
        const label = vowelInputs.find((item) => item.key === key)?.label ?? key;
        setVoiceError(`${label} 未通过采集质量检查，请按卡片提示重录。`);
      }
    } catch (error) {
      if (qualityRequestSequence.current[key] === requestId) {
        setVoiceError(error instanceof Error ? error.message : "无法完成录音质量检查，请稍后重试。");
      }
    } finally {
      if (qualityRequestSequence.current[key] === requestId) {
        setQualityChecking((current) => ({ ...current, [key]: false }));
      }
    }
  }

  function setCapturedVoiceFile(key: VowelKey, file: File) {
    setVoiceFiles((current) => ({ ...current, [key]: file }));
    setVoiceQuality((current) => ({ ...current, [key]: null }));
    setVoiceResult(null);
    if (key === "a") setAcousticResult(null);
    setVoiceError("");
    void checkVoiceQuality(key, file);
  }

  function handleVoiceFile(key: VowelKey, event: ChangeEvent<HTMLInputElement>) {
    if (operationInFlight.current) return;
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) setCapturedVoiceFile(key, file);
  }

  function releaseRecorder(session: RecordingSession) {
    session.processor.onaudioprocess = null;
    session.source.disconnect();
    session.processor.disconnect();
    session.silentGain.disconnect();
    session.stream.getTracks().forEach((track) => track.stop());
    void session.audioContext.close();
  }

  async function startVoiceRecording(key: VowelKey) {
    if (operationInFlight.current) return;
    if (recordingKey || recordingStarting) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setVoiceError("麦克风采集需要在 HTTPS 或 localhost 打开，并使用支持 getUserMedia 的浏览器。");
      return;
    }
    setRecordingStarting(true);
    setVoiceError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      const AudioContextConstructor = window.AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextConstructor) {
        stream.getTracks().forEach((track) => track.stop());
        throw new Error("当前浏览器不支持音频采集上下文");
      }
      const audioContext = new AudioContextConstructor();
      await audioContext.resume();
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      const silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      const chunks: Float32Array[] = [];
      processor.onaudioprocess = (event) => {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(audioContext.destination);
      activeRecorderRef.current = {
        key,
        stream,
        audioContext,
        source,
        processor,
        silentGain,
        chunks,
        sampleRate: audioContext.sampleRate,
        startedAt: audioContext.currentTime,
      };
      setRecordingSeconds(0);
      setRecordingKey(key);
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        setVoiceError("未获得麦克风权限。请在浏览器地址栏或系统设置中允许本页面使用麦克风后重试。");
      } else {
        setVoiceError(error instanceof Error ? `无法启动麦克风：${error.message}` : "无法启动麦克风采集，请检查设备设置。");
      }
    } finally {
      setRecordingStarting(false);
    }
  }

  async function stopVoiceRecording() {
    const session = activeRecorderRef.current;
    if (!session) return;
    activeRecorderRef.current = null;
    setRecordingKey(null);
    setRecordingSeconds(0);
    releaseRecorder(session);
    const sampleCount = session.chunks.reduce((total, chunk) => total + chunk.length, 0);
    if (sampleCount < session.sampleRate / 2) {
      setVoiceError("录音不足 0.5 秒，请按住元音持续发声后重新录制。");
      return;
    }
    const samples = new Float32Array(sampleCount);
    let offset = 0;
    session.chunks.forEach((chunk) => {
      samples.set(chunk, offset);
      offset += chunk.length;
    });
    const file = new File(
      [encodePcmAsWav(samples, session.sampleRate)],
      `voicescope_${session.key}_${Date.now()}.wav`,
      { type: "audio/wav" },
    );
    setCapturedVoiceFile(session.key, file);
  }

  async function analyzeAcoustics() {
    const vowelA = voiceFiles.a;
    if (!vowelA || acousticAnalyzing) {
      setVoiceError("请先录制或选择一段持续发 /a/ 的音频，再进行基础声学分析。");
      return;
    }
    if (qualityChecking.a || !voiceQuality.a) {
      setVoiceError("正在检查 /a/ 的录音质量，请稍候；若长时间没有结果，请重新选择文件。");
      return;
    }
    if (!voiceQuality.a.accepted) {
      setVoiceError("/a/ 未通过采集质量检查，请按卡片提示重录后再分析。");
      return;
    }
    if (!conversationId) {
      setVoiceError("请先登记患者并创建会话。");
      return;
    }
    if (!beginOperation()) return;
    const form = new FormData();
    form.append("conversation_id", conversationId);
    form.append("a", vowelA);
    setAcousticAnalyzing(true);
    setVoiceError("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/voice/acoustic-analyze`, { method: "POST", body: form });
      const data = await response.json() as AcousticAnalysisResult | { detail?: unknown };
      if (!response.ok) throw new Error("detail" in data ? applyQualityFailure(data.detail) : "基础声学分析服务不可用");
      const result = data as AcousticAnalysisResult;
      setAcousticResult(result);
      void refreshRecentConversations();
      setMessages((current) => [
        ...current,
        { id: nextMessageId(), role: "event", content: `基础声学指标已完成：MPT ${result.mpt_seconds.toFixed(1)} 秒，未进行疾病分类。`, time: getCurrentTime() },
      ]);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "基础声学分析失败，请稍后重试");
    } finally {
      operationInFlight.current = false;
      setAcousticAnalyzing(false);
    }
  }

  async function analyzeVoice() {
    const missing = vowelInputs.filter(({ key }) => !voiceFiles[key]).map(({ label }) => label);
    if (missing.length > 0 || voiceAnalyzing) {
      setVoiceError(`请先补齐 ${missing.join("、")} 元音音频`);
      return;
    }
    const checking = vowelInputs.filter(({ key }) => qualityChecking[key]).map(({ label }) => label);
    if (checking.length > 0) {
      setVoiceError(`正在检查 ${checking.join("、")} 的录音质量，请稍候。`);
      return;
    }
    const unchecked = vowelInputs.filter(({ key }) => !voiceQuality[key]).map(({ label }) => label);
    if (unchecked.length > 0) {
      setVoiceError(`${unchecked.join("、")} 尚未完成质量检查，请重新选择对应文件。`);
      return;
    }
    const failed = vowelInputs.filter(({ key }) => voiceQuality[key]?.accepted === false).map(({ label }) => label);
    if (failed.length > 0) {
      setVoiceError(`${failed.join("、")} 需要重录，请先按卡片提示修正。`);
      return;
    }
    if (!conversationId) {
      setVoiceError("请先登记患者并创建会话。");
      return;
    }
    if (!beginOperation()) return;
    const form = new FormData();
    form.append("conversation_id", conversationId);
    vowelInputs.forEach(({ key }) => form.append(key, voiceFiles[key] as File));
    setVoiceAnalyzing(true);
    setVoiceError("");
    try {
      const response = await authFetch(`${apiBaseUrl}/api/voice/analyze`, { method: "POST", body: form });
      const data = await response.json() as VoiceAnalysisResult | { detail?: unknown };
      if (!response.ok) throw new Error("detail" in data ? applyQualityFailure(data.detail) : "嗓音分析服务不可用");
      const result = data as VoiceAnalysisResult;
      setVoiceResult(result);
      setVoiceCorrection(null);
      setCorrectionOpen(false);
      setCorrectionLabel("");
      setCorrectionNote("");
      setCorrectionError("");
      void refreshRecentConversations();
      setMessages((current) => [
        ...current,
        {
          id: nextMessageId(),
          role: "event",
          content: `六元音分析完成：研究模型最高概率标签为 ${result.predicted_label}（${(result.predicted_probability * 100).toFixed(1)}%），请结合专科检查解读。`,
          time: getCurrentTime(),
        },
      ]);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "嗓音分析失败，请稍后重试");
    } finally {
      operationInFlight.current = false;
      setVoiceAnalyzing(false);
    }
  }

  return (
    <>
      <ResearchManagement apiBaseUrl={apiBaseUrl} open={researchOpen} onClose={() => setResearchOpen(false)} />
      {auth.user.role === "admin" && <SecurityManagement open={securityOpen} onClose={() => setSecurityOpen(false)} />}
      <Modal
        title="纠正六元音筛查结果"
        open={correctionOpen}
        onCancel={() => { if (!correctionSaving) setCorrectionOpen(false); }}
        onOk={() => void saveDiagnosisCorrection()}
        okText="保存纠正"
        cancelText="取消"
        confirmLoading={correctionSaving}
        okButtonProps={{ disabled: !correctionLabel || correctionLabel === voiceResult?.predicted_label }}
      >
        <div className="diagnosis-correction-form">
          <p>模型原诊断 <strong>{voiceResult?.predicted_label}</strong> 将永久保留；下方结果作为独立的人工反馈记录。</p>
          <label>纠正后诊断
            <select value={correctionLabel} onChange={(event) => { setCorrectionLabel(event.target.value); setCorrectionError(""); }}>
              <option value="">请选择</option>
              {voiceResult?.probabilities.filter((item) => item.label !== voiceResult.predicted_label).map((item) => (
                <option key={item.label} value={item.label}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>纠正依据（可选）
            <textarea rows={3} maxLength={500} value={correctionNote} placeholder="例如：结合喉镜检查或专科医师判断" onChange={(event) => setCorrectionNote(event.target.value)} />
          </label>
          <small>该记录可作为后续模型优化的反馈数据，不会立即修改当前模型权重。</small>
          {correctionError && <p className="patient-intake-error"><WarningFilled /> {correctionError}</p>}
        </div>
      </Modal>
      <Modal title="手机录音与上传" open={mobileGuideOpen} onCancel={() => setMobileGuideOpen(false)} footer={[
        <Button key="close" onClick={() => setMobileGuideOpen(false)}>知道了</Button>,
        <Button key="upload" type="primary" onClick={() => { setMobileGuideOpen(false); openVoiceCapture(true); }}>选择录音文件</Button>,
      ]}>
        <ol>
          <li>用手机支持导出 WAV 的录音工具，在安静环境中录制。先舒适地持续发“啊”，有不适时停止。</li>
          <li>导出 WAV、FLAC、AIFF 或 OGG 文件。若手机默认保存为 M4A，请先导出或转换为 WAV，不能仅修改扩展名。</li>
          <li>将录音传到当前电脑，点击“上传嗓音文件”。若已在手机打开可用的 HTTPS 站点，也可选择“在当前设备录音”。</li>
        </ol>
        <p>当前本机地址不能直接在另一台手机访问。录音分析后会保存到当前会话；请不要切换到其他患者。</p>
      </Modal>
      {clientReady && auth.user.role === "patient" && <Modal
        open={patientInfoOpen}
        title={conversationId ? "登记新患者信息" : "开始问诊前，请登记患者信息"}
        footer={null}
        closable={Boolean(conversationId)}
        mask={{ closable: Boolean(conversationId) }}
        keyboard={Boolean(conversationId)}
        onCancel={() => { if (conversationId) setPatientInfoOpen(false); }}
      >
        <div className="patient-intake-form">
          <p>基本信息仅保存于本机本次患者会话；所在地请填写省/市级，不填写详细住址。</p>
          <label>患者姓名<input value={patientProfileDraft.name} maxLength={32} placeholder="例如：张三" onChange={(event) => { setPatientProfileDraft((current) => ({ ...current, name: event.target.value })); setPatientInfoError(""); }} /></label>
          <div className="patient-intake-grid">
            <label>年龄<input type="number" min="0" max="120" step="1" value={patientProfileDraft.age} placeholder="岁" onChange={(event) => { setPatientProfileDraft((current) => ({ ...current, age: event.target.value })); setPatientInfoError(""); }} /></label>
            <label>性别<select value={patientProfileDraft.gender} onChange={(event) => { setPatientProfileDraft((current) => ({ ...current, gender: event.target.value })); setPatientInfoError(""); }}><option value="">请选择</option><option value="男">男</option><option value="女">女</option><option value="其他/不便透露">其他/不便透露</option></select></label>
          </div>
          <label>所在地<input value={patientProfileDraft.location} maxLength={64} placeholder="例如：浙江省杭州市" onChange={(event) => { setPatientProfileDraft((current) => ({ ...current, location: event.target.value })); setPatientInfoError(""); }} /></label>
          <label>主要症状 / 主诉<input value={patientProfileDraft.chiefComplaint} maxLength={120} placeholder="例如：声音嘶哑、发声疲劳" onChange={(event) => { setPatientProfileDraft((current) => ({ ...current, chiefComplaint: event.target.value })); setPatientInfoError(""); }} /></label>
          {patientInfoError && <p className="patient-intake-error"><WarningFilled /> {patientInfoError}</p>}
          <Button type="primary" block loading={patientInfoSaving} onClick={() => void createPatientConversation()}>创建患者问诊</Button>
        </div>
      </Modal>}
      <main className="app-shell" data-large-text={largeTextEnabled ? "true" : "false"} style={{ "--text-scale": textScale } as CSSProperties}>
      <aside className={`session-sidebar ${mobileNavOpen ? "is-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            <span className="brand-wave wave-one" />
            <span className="brand-wave wave-two" />
            <span className="brand-wave wave-three" />
          </div>
          <div>
            <div className="brand-name">VoiceScope</div>
            <div className="brand-subtitle">Clinical Agent</div>
          </div>
          <Button
            className="mobile-close"
            type="text"
            icon={<CloseOutlined />}
            aria-label="关闭会话菜单"
            onClick={() => setMobileNavOpen(false)}
          />
        </div>

        {auth.user.role === "patient" && <Button disabled={isWorking || Boolean(recordingKey) || recordingStarting} className="new-session-button" type="primary" icon={<PlusOutlined />} block onClick={openPatientIntake}>
          新建智能问诊
        </Button>}

        <nav className="session-nav" aria-label="问诊会话">
          <div className="nav-label session-search-heading">
            <span>会话检索</span>
            <Button type="text" size="small" onClick={() => setSearchOpen((open) => !open)} aria-expanded={searchOpen} aria-controls="conversation-search-panel">{searchOpen ? "收起" : "展开"}</Button>
          </div>
          {searchOpen && <div id="conversation-search-panel" className="conversation-search" aria-label="按日期姓名性别检索会话">
            <input type="date" aria-label="开始日期" value={searchDateFrom} onChange={(event) => setSearchDateFrom(event.target.value)} />
            <input type="date" aria-label="结束日期" value={searchDateTo} onChange={(event) => setSearchDateTo(event.target.value)} />
            <input aria-label="患者姓名" placeholder="姓名" maxLength={64} value={searchName} onChange={(event) => setSearchName(event.target.value)} />
            <select aria-label="患者性别" value={searchGender} onChange={(event) => setSearchGender(event.target.value)}><option value="">全部性别</option><option value="男">男</option><option value="女">女</option><option value="其他/不便透露">其他/不便透露</option></select>
            <Button size="small" loading={historyLoading} onClick={() => void searchConversations()}>检索</Button>
            {searchMessage && <span className="search-message">{searchMessage}</span>}
          </div>}
          <div className="nav-label recent-session-heading"><span>最近会话</span><small>{recentConversations.length} 条</small></div>
          {recentConversations.length === 0 && <p className="session-empty">{historyLoading ? "正在加载…" : "暂无匹配会话"}</p>}
          {visibleConversations.map((conversation) => (
            <div className="session-item-wrap" key={conversation.conversation_id}>
              <button
                type="button"
                className={`session-item ${conversation.conversation_id === conversationId ? "active" : ""}`}
                disabled={isWorking || Boolean(recordingKey) || recordingStarting}
                onClick={() => void openConversation(conversation.conversation_id)}
              >
                <MessageOutlined />
                <span className="session-copy">
                  <strong>{conversation.title}</strong>
                  <small>{formatHistoryTime(conversation.updated_at)} · {conversation.message_count} 条</small>
                </span>
                {conversation.conversation_id === conversationId && <span className="active-dot" />}
              </button>
              {auth.user.role !== "doctor" && <Button type="text" danger size="small" disabled={isWorking || Boolean(recordingKey) || recordingStarting} icon={<CloseOutlined />} onClick={() => void deleteConversation(conversation.conversation_id, conversation.title)} aria-label={`删除${conversation.title}`} />}
            </div>
          ))}
          {recentConversations.length > sessionPageSize && <div className="session-pagination" aria-label="历史会话分页">
            <Button size="small" disabled={sessionPage <= 1} onClick={() => setSessionPage((page) => Math.max(1, page - 1))}>上一页</Button>
            <span>第 {sessionPage} / {sessionPageCount} 页</span>
            <Button size="small" disabled={sessionPage >= sessionPageCount} onClick={() => setSessionPage((page) => Math.min(sessionPageCount, page + 1))}>下一页</Button>
          </div>}
        </nav>

        <div className="sidebar-spacer" />

        <div className="privacy-card">
          <SafetyCertificateFilled />
          <div>
            <strong>隐私保护</strong>
            <p>问诊记录与已保存要点可用于后续随访；新建问诊不会删除历史记录</p>
          </div>
        </div>

        {auth.user.role === "patient" && <div className="consent-card">
          <span>数据使用授权：{auth.consent?.accepted ? "已同意" : "已撤回"}</span>
          <Button size="small" danger={Boolean(auth.consent?.accepted)} onClick={() => void auth.setConsent(!auth.consent?.accepted)}>{auth.consent?.accepted ? "撤回后续处理授权" : "重新同意"}</Button>
        </div>}

        <button className="profile-row" type="button" onClick={() => void auth.logout()} title="退出登录">
          <Avatar size={38} icon={<UserOutlined />} />
          <span>
            <strong>{auth.user.display_name}</strong>
            <small>{{ patient: "患者", doctor: "医生", admin: "管理员" }[auth.user.role]} · 点击退出</small>
          </span>
          <MoreOutlined />
        </button>
      </aside>

      {mobileNavOpen && <button className="sidebar-scrim" aria-label="关闭菜单" onClick={() => setMobileNavOpen(false)} />}

      <section className="chat-column">
        <header className="chat-header">
          <Button
            className="mobile-menu"
            type="text"
            icon={<MenuOutlined />}
            aria-label="打开会话菜单"
            onClick={() => setMobileNavOpen(true)}
          />
          <div className="agent-avatar">
            <RobotOutlined />
            <span className="online-indicator" />
          </div>
          <div className="header-title">
            <div className="title-line">
              <h1>智能嗓音问诊</h1>
              <Tag color={consultationCompleted ? "green" : voiceResult ? "cyan" : "blue"}>{consultationCompleted ? "已完成诊疗" : voiceResult ? "嗓音测试完成" : "研究演示"}</Tag>
            </div>
            <p><Badge status="success" /> Clinical Interview Agent 在线 · {patientProfile.chiefComplaint ? `当前问诊：${patientProfile.chiefComplaint}` : "请先登记患者"}</p>
          </div>
          <div className="header-actions">
            <div className="text-size-control" aria-label="文字大小设置">
              <Button type={largeTextEnabled ? "primary" : "text"} size="small" aria-pressed={largeTextEnabled} onClick={() => setLargeTextEnabled((enabled) => !enabled)}>大字模式</Button>
              {largeTextEnabled && <label className="text-scale-slider">字号
                <input type="range" min="1.1" max="1.5" step="0.05" value={textScale} aria-label="调整字体大小" onChange={(event) => setTextScale(Number(event.target.value))} />
                <span>{Math.round(textScale * 100)}%</span>
              </label>}
            </div>
            {auth.user.role !== "patient" && <Tooltip title="医生复核：待办队列、录音回放、检查资料与意见留痕">
              <Button type="text" icon={<FileImageOutlined />} aria-label="打开医生复核工作台" onClick={() => setResearchOpen(true)}>医生复核</Button>
            </Tooltip>}
            {auth.user.role === "admin" && <Tooltip title="账户、病例授权与访问审计">
              <Button type="text" icon={<SafetyCertificateFilled />} onClick={() => setSecurityOpen(true)}>权限管理</Button>
            </Tooltip>}
            <Tooltip title="当前页面为产品原型，不提供真实医学诊断">
              <Button type="text" shape="circle" icon={<InfoCircleOutlined />} aria-label="查看说明" />
            </Tooltip>
            <Tooltip title="历史记录">
              <Button type="text" shape="circle" icon={<HistoryOutlined />} aria-label="历史记录" />
            </Tooltip>
          </div>
        </header>

        <div className="chat-scroll" aria-live="polite">
          <div className="date-divider"><span>当前会话记录</span></div>

          <div className="intro-banner">
            <SafetyCertificateFilled />
            <div>
              <strong>本次问诊约需 3–5 分钟</strong>
              <p>助手会逐项补全病史，并在发现高危信号时提示优先就医。</p>
            </div>
          </div>

          <div className="message-list">
            {messages.map((message) => {
              if (message.role === "event") {
                return (
                  <div className="event-message" key={message.id}>
                    <PaperClipOutlined /> {message.content}
                  </div>
                );
              }

              const isAssistant = message.role === "assistant";
              return (
                <article className={`message-row ${isAssistant ? "assistant" : "user"}`} key={message.id}>
                  {isAssistant && <Avatar className="message-avatar" icon={<RobotOutlined />} />}
                  <div className="message-body">
                    {isAssistant && <span className="message-author">VoiceScope 助手</span>}
                    <div className="message-bubble">{message.content}</div>
                    <time>{message.time}</time>
                  </div>
                </article>
              );
            })}

            {typing && (
              <article className="message-row assistant">
                <Avatar className="message-avatar" icon={<RobotOutlined />} />
                <div className="message-body">
                  <span className="message-author">VoiceScope 助手</span>
                  <div className="message-bubble typing-bubble" aria-label="助手正在输入">
                    <i /><i /><i />
                  </div>
                </div>
              </article>
            )}
          </div>

          {!typing && options.length > 0 && (
            <div className="quick-replies" aria-label="快捷回答">
              <span>快捷回答</span>
              <div>
                {options.map((option) => (
                  <Button key={option} onClick={() => sendMessage(option)}>
                    {option}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {caseState?.ready_for_voice && (
            <div className="next-step-card">
              <div className="next-step-icon"><SoundOutlined /></div>
              <div>
                <strong>病例要点已完成 · 下一步采集嗓音</strong>
                <p style={{ maxWidth: "900px" }}>
                  可上传已有录音，或用手机录制后上传。先录制持续发“啊”，有不适时请停止。
                  </p>
                <p></p>
                <div className="quick-replies">
                  <Button type="primary" disabled={isWorking} onClick={() => openVoiceCapture(true)}>上传嗓音文件</Button>
                  <Button disabled={isWorking} onClick={() => setMobileGuideOpen(true)}>手机录音指引</Button>
                  <Button disabled={isWorking} onClick={() => openVoiceCapture()}>在当前设备录音</Button>
                </div>
              </div>
            </div>
          )}

          {voicePanelOpen && caseState?.ready_for_voice && (
            <section className="voice-analysis-card" aria-label="嗓音采集与基础声学分析">
              <div className="voice-card-heading">
                <div>
                  <span className="eyebrow">先指标，后筛查</span>
                  <h2>嗓音采集与基础声学分析</h2>
                  <p>选择或录制后会自动检查背景噪声、削波和有效发声时长。质控只判断录音技术可用性，不以嘶哑、气息声、基频或声音规则度判定不合格。</p>
                </div>
                <Button type="text" icon={<CloseOutlined />} onClick={() => setVoicePanelOpen(false)} aria-label="关闭六元音采集" />
              </div>

              <div className="vowel-upload-grid">
                {vowelInputs.map(({ key, label, guide }) => {
                  const quality = voiceQuality[key];
                  return (
                    <div className={`vowel-upload ${voiceFiles[key] ? "has-file" : ""} ${recordingKey === key ? "is-recording" : ""} ${quality ? `quality-${quality.status}` : ""}`} key={key}>
                      <strong>{label}</strong>
                      <span>{voiceFiles[key]?.name ?? (key === "a" ? "尽力持续发“啊”10–30 秒" : `后续筛查：持续发“${guide}”1–10 秒`)}</span>
                      {qualityChecking[key] ? <ClockCircleOutlined /> : quality?.status === "re_record" ? <WarningFilled /> : voiceFiles[key] ? <CheckCircleFilled /> : <PlusOutlined />}
                      <div className="vowel-upload-actions">
                        <label htmlFor={`voice-file-${key}`}>{voiceFiles[key] ? "更换文件" : "选择文件"}</label>
                        <input id={`voice-file-${key}`} type="file" accept="audio/wav,audio/x-wav,audio/flac,audio/ogg,.wav,.flac,.aiff,.ogg" onChange={(event) => handleVoiceFile(key, event)} />
                        <Button
                          size="small"
                          danger={recordingKey === key || quality?.status === "re_record"}
                          icon={<AudioOutlined />}
                          loading={(recordingStarting && !recordingKey) || qualityChecking[key]}
                          disabled={Boolean(recordingKey && recordingKey !== key)}
                          onClick={() => (recordingKey === key ? void stopVoiceRecording() : void startVoiceRecording(key))}
                        >
                          {recordingKey === key ? "结束录音" : voiceFiles[key] ? "重新录制" : "麦克风录制"}
                        </Button>
                      </div>
                      {quality && (
                        <div className="vowel-quality-detail" role={quality.accepted ? "status" : "alert"}>
                          <b>{quality.status === "pass" ? "质量通过" : quality.status === "warning" ? "可用，但建议优化" : "需要重录"}</b>
                          {quality.metrics && <small>有效发声 {quality.metrics.active_voice_seconds.toFixed(2)} 秒 · 削波 {(quality.metrics.clipped_fraction * 100).toFixed(1)}%{quality.metrics.estimated_snr_db !== null ? ` · 信噪比 ${quality.metrics.estimated_snr_db.toFixed(1)} dB` : ""}</small>}
                          {quality.issues.map((issue) => (
                            <p key={issue.code}>
                              <span>{issue.message}</span>
                              <em>{issue.guidance}</em>
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {recordingKey && (
                <div className="voice-recording-status">
                  <span className="recording-pulse" /> 正在采集 {vowelInputs.find((item) => item.key === recordingKey)?.label} · {recordingSeconds}s
                  <Button size="small" danger onClick={() => void stopVoiceRecording()}>完成并保存 WAV</Button>
                </div>
              )}

              {voiceError && <div className="voice-error"><WarningFilled /> {voiceError}</div>}

              <div className="voice-card-actions">
                <span><SafetyCertificateFilled /> 质量通过后可分析；系统使用持续 /a/ 计算基础指标，分析录音将保存到当前病例记录</span>
                <Button type="primary" loading={acousticAnalyzing} disabled={isWorking || Boolean(recordingKey) || !voiceFiles.a || qualityChecking.a || voiceQuality.a?.accepted !== true || !conversationId} onClick={analyzeAcoustics}>
                  {acousticAnalyzing ? "正在计算声学指标" : "开始分析指标"}
                </Button>
              </div>

              {acousticResult && (
                <div className="acoustic-result">
                  <div className="acoustic-result-heading">
                    <div><span>基础声学结果（未进行疾病分类）</span><strong>持续 /a/ · {acousticResult.recording_duration_seconds.toFixed(1)} 秒</strong></div>
                    <Tag color={acousticResult.signal_type.grade === "I" ? "green" : "orange"}>信号类型 {acousticResult.signal_type.grade}</Tag>
                  </div>
                  <div className="acoustic-metric-grid">
                    <div><span>最长发声时间 MPT</span><strong>{acousticResult.mpt_seconds.toFixed(2)} 秒</strong><small>参考：10 秒</small></div>
                    <div><span>基频 F0</span><strong>{acousticResult.f0_hz?.toFixed(2) ?? "未提取"}{acousticResult.f0_hz !== null ? " Hz" : ""}</strong><small>参考：85–155 Hz</small></div>
                    <div><span>基频微扰 Jitter</span><strong>{acousticResult.jitter_percent?.toFixed(3) ?? "未提取"}{acousticResult.jitter_percent !== null ? " %" : ""}</strong><small>参考：0.5%</small></div>
                    <div><span>振幅微扰 Shimmer</span><strong>{acousticResult.shimmer_percent?.toFixed(3) ?? "未提取"}{acousticResult.shimmer_percent !== null ? " %" : ""}</strong><small>参考：3%</small></div>
                    <div><span>相对音强</span><strong>{acousticResult.mean_intensity_dbfs.toFixed(2)} dBFS</strong><small>82 dB SPL 需校准设备</small></div>
                    <div><span>信号类型</span><strong>Ⅰ/Ⅱ/Ⅲ：{acousticResult.signal_type.grade}</strong><small>{acousticResult.signal_type.description}</small></div>
                  </div>
                  {acousticResult.warnings.map((warning) => <p className="voice-warning" key={warning}><WarningFilled /> {warning}</p>)}
                  <p className="voice-disclaimer"><InfoCircleOutlined /> {acousticResult.disclaimer}</p>
                </div>
              )}

              <div className="clinical-rating-card">
                <div>
                  <strong>临床量表与听感分级</strong>
                  <p>以下项目不能由录音自动推断，请由受试者或具备经验的评估者填写；当前仅在浏览器内显示，不上传。</p>
                </div>
                <div className="clinical-score-row">
                  <span>VHI（功能 / 生理 / 情感）</span>
                  {(["functional", "physical", "emotional"] as const).map((key) => (
                    <input key={key} type="number" min="0" max="40" placeholder={key === "functional" ? "功能" : key === "physical" ? "生理" : "情感"} value={vhiScores[key]} onChange={(event) => setVhiScores((current) => ({ ...current, [key]: event.target.value }))} />
                  ))}
                  <b>总分：{vhiTotal ?? "待填"}</b>
                </div>
                <div className="clinical-score-row">
                  <span>RSI 总分</span>
                  <input type="number" min="0" max="45" placeholder="0–45" value={rsiScore} onChange={(event) => setRsiScore(event.target.value)} />
                  <b>参考：13 分</b>
                </div>
                {(["元音", "言语"] as const).map((label) => {
                  const scores = label === "元音" ? vowelGrbas : speechGrbas;
                  const setScores = label === "元音" ? setVowelGrbas : setSpeechGrbas;
                  return (
                    <div className="clinical-score-row grbas-row" key={label}>
                      <span>GRBAS（{label}）</span>
                      {(["g", "r", "b", "a", "s"] as const).map((key) => (
                        <label key={key}>{key.toUpperCase()}<input type="number" min="0" max="3" placeholder="0–3" value={scores[key]} onChange={(event) => setScores((current) => ({ ...current, [key]: event.target.value }))} /></label>
                      ))}
                      <b>{formatGrbas(scores)}</b>
                    </div>
                  );
                })}
              </div>

              <div className="voice-card-actions secondary-action">
                <span>完成基础指标后，如研究方案需要，才可提交六元音至已训练的疾病筛查模型。</span>
                <Button loading={voiceAnalyzing} disabled={isWorking || consultationCompleted || !acousticResult || !allVoiceQualityAccepted || !conversationId} onClick={analyzeVoice}>
                  {voiceAnalyzing ? "正在加载模型并分析" : "下一步：六元音研究筛查"}
                </Button>
              </div>

              {voiceResult && (
                <div className="voice-result">
                  <div className="voice-result-summary">
                    <span>模型原诊断（保留）</span>
                    <strong>{voiceResult.predicted_label}</strong>
                    <b>{(voiceResult.predicted_probability * 100).toFixed(1)}%</b>
                  </div>
                  {auth.user.role !== "patient" && <div className="diagnosis-correction-card" data-corrected={voiceCorrection ? "true" : "false"}>
                    <div>
                      <span>人工纠正结果</span>
                      <strong>{voiceCorrection?.corrected_label ?? "尚未纠正"}</strong>
                      <small>{voiceCorrection ? `第 ${voiceCorrection.correction_number} 次纠正 · 原结果 ${voiceCorrection.original_predicted_label} 已保留` : "如模型判断有误，可提交人工反馈"}</small>
                    </div>
                    <Button size="small" loading={correctionSaving} onClick={openDiagnosisCorrection}>{voiceCorrection ? "再次纠正" : "纠正诊断"}</Button>
                    {voiceCorrection?.note && <p>纠正依据：{voiceCorrection.note}</p>}
                  </div>}
                  <div className="probability-list">
                    {voiceResult.probabilities.map((item) => (
                      <div className="probability-row" key={item.label}>
                        <span>{item.label}</span>
                        <Progress percent={Number((item.probability * 100).toFixed(1))} size={["100%", 6]} showInfo={false} />
                        <strong>{(item.probability * 100).toFixed(1)}%</strong>
                      </div>
                    ))}
                  </div>
                  <div className="attention-list">
                    <span>六元音模型关注度</span>
                    <div>
                      {Object.entries(voiceResult.vowel_attention).map(([vowel, weight]) => (
                        <Tag key={vowel}>{vowel} {(weight * 100).toFixed(1)}%</Tag>
                      ))}
                    </div>
                  </div>
                  {voiceResult.warnings.map((warning) => <p className="voice-warning" key={warning}><WarningFilled /> {warning}</p>)}
                  <p className="voice-disclaimer"><InfoCircleOutlined /> {voiceResult.disclaimer}</p>
                  <div className="screening-report-actions">
                    <div><strong>筛查报告</strong><span>报告锁定本次分析，可打印或另存为 PDF。</span></div>
                    <Button href={patientReportUrl} target="_blank" rel="noreferrer">查看患者版报告</Button>
                  </div>
                  <div className="consultation-complete-action"><Button type="primary" disabled={consultationCompleted} onClick={() => void completeConsultation()}>{consultationCompleted ? "已完成诊疗" : "结束咨询"}</Button><span>{consultationCompleted ? "本次咨询已归档，可在医生复核工作台处理并留存意见。" : "确认嗓音测试结果后，可选择结束本次咨询。"}</span></div>
                </div>
              )}
            </section>
          )}
        </div>

        <footer className="composer-area">
          <div className="composer">
            <textarea
              value={draft}
              disabled={consultationCompleted}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={consultationCompleted ? "本次咨询已完成，请新建问诊开始新的会话。" : "补充您的症状，或选择上方快捷回答…"}
              rows={1}
              aria-label="输入问诊内容"
            />
            <div className="composer-actions">
              <input ref={fileInputRef} className="sr-only" type="file" accept="audio/*,image/*,.pdf" onChange={handleFile} />
              <Tooltip title="添加嗓音、喉镜图片或检查报告">
                <Button type="text" shape="circle" icon={<PaperClipOutlined />} onClick={() => fileInputRef.current?.click()} aria-label="添加资料" />
              </Tooltip>
              <Tooltip title="使用麦克风录制六元音">
                <Button
                  type="text"
                  shape="circle"
                  icon={<AudioOutlined />}
                  disabled={!caseState?.ready_for_voice || isWorking}
                  onClick={() => openVoiceCapture()}
                  aria-label="打开六元音麦克风采集"
                />
              </Tooltip>
              <Button
                className="send-button"
                type="primary"
                shape="circle"
                icon={<SendOutlined />}
                onClick={() => sendMessage()}
                disabled={!draft.trim() || isWorking || consultationCompleted || !conversationId}
                aria-label="发送消息"
              />
            </div>
          </div>
          <p className="medical-disclaimer">
            <InfoCircleOutlined /> AI 结果仅用于健康筛查与临床决策支持，不能替代医生诊断；如有呼吸困难等急症，请立即就医。
          </p>
        </footer>
      </section>

      <aside className="insight-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">PATIENT STATE</span>
            <h2>问诊状态</h2>
          </div>
          <Tag color={redFlag ? "red" : "green"}>{redFlag ? "需关注" : "采集中"}</Tag>
        </div>

        <section className="patient-profile-card" aria-label="当前对话患者基本信息">
          <span>当前对话患者</span>
          <strong className="patient-name-title">{patientProfile.name || "待登记患者"}</strong>
          <div className="patient-profile-details">
            <div><b>年龄</b><small>{patientProfile.age ? `${patientProfile.age} 岁` : "待登记"}</small></div>
            <div><b>性别</b><small>{patientProfile.gender || "待登记"}</small></div>
            <div><b>所在地</b><small>{patientProfile.location || "待登记"}</small></div>
          </div>
        </section>

        <section className="progress-card">
          <div className="progress-title">
            <span>信息完整度</span>
            <strong>{progress}%</strong>
          </div>
          <Progress percent={progress} showInfo={false} size={["100%", 7]} />
          <p><ClockCircleOutlined /> 已获取 {caseState?.collected_count ?? 0}/5 项病例要点</p>
        </section>

        <section className="state-section">
          <div className="section-title">
            <h3>病例要点</h3>
            <span>{caseState?.collected_count ?? 0}/5</span>
          </div>
          <div className="fact-list">
            {completedFacts.map((fact) => (
              <div className={`fact-row ${fact.done ? "is-complete" : ""}`} key={fact.label}>
                {fact.done ? <CheckCircleFilled /> : <span className="empty-check" />}
                <span>{fact.label}</span>
                <strong>{fact.value}</strong>
              </div>
            ))}
          </div>
        </section>

        <section className="state-section smoking-history-section">
          <div className="section-title">
            <h3>吸烟史（患者填写）</h3>
            <span>{smokingHistoryComplete ? "已填写" : "待补充"}</span>
          </div>
          <label>吸烟状态
            <select value={facts.smoking ?? ""} onChange={(event) => { const smoking = event.target.value; setFacts((current) => ({ ...current, smoking, ...(smoking === "从不吸烟" ? { smokingYears: "", cigarettesPerDay: "" } : {}) })); setSmokingSaveMessage(""); }}>
              <option value="">请选择</option>
              <option value="从不吸烟">从不吸烟</option>
              <option value="已经戒烟">已经戒烟</option>
              <option value="偶尔吸烟">偶尔吸烟</option>
              <option value="长期吸烟">长期吸烟</option>
            </select>
          </label>
          <div className="smoking-inputs">
            <label>累计吸烟年限<input type="number" min="0" max="120" step="0.5" placeholder="年" disabled={facts.smoking === "从不吸烟"} value={facts.smokingYears ?? ""} onChange={(event) => { setFacts((current) => ({ ...current, smokingYears: event.target.value })); setSmokingSaveMessage(""); }} /></label>
            <label>平均每日量<input type="number" min="0" max="200" step="0.5" placeholder="支/日" disabled={facts.smoking === "从不吸烟"} value={facts.cigarettesPerDay ?? ""} onChange={(event) => { setFacts((current) => ({ ...current, cigarettesPerDay: event.target.value })); setSmokingSaveMessage(""); }} /></label>
          </div>
          <Button size="small" loading={smokingSaving} onClick={() => void saveSmokingHistory()}>保存吸烟史用于随访</Button>
          {smokingSaveMessage && <p className="smoking-save-message">{smokingSaveMessage}</p>}
        </section>

        <section className="state-section smoking-history-section alcohol-history-section">
          <div className="section-title">
            <h3>饮酒史（患者填写）</h3>
            <span>{alcoholHistoryComplete ? "已填写" : "待补充"}</span>
          </div>
          <label>饮酒状态
            <select value={facts.drinking ?? ""} onChange={(event) => { const drinking = event.target.value; setFacts((current) => ({ ...current, drinking, ...(drinking === "从不饮酒" ? { drinkingYears: "", standardDrinksPerDay: "" } : {}) })); setAlcoholSaveMessage(""); }}>
              <option value="">请选择</option>
              <option value="从不饮酒">从不饮酒</option>
              <option value="已经戒酒">已经戒酒</option>
              <option value="偶尔饮酒">偶尔饮酒</option>
              <option value="经常饮酒">经常饮酒</option>
            </select>
          </label>
          <div className="smoking-inputs">
            <label>累计饮酒年限<input type="number" min="0" max="120" step="0.5" placeholder="年" disabled={facts.drinking === "从不饮酒"} value={facts.drinkingYears ?? ""} onChange={(event) => { setFacts((current) => ({ ...current, drinkingYears: event.target.value })); setAlcoholSaveMessage(""); }} /></label>
            <label>平均每日量<input type="number" min="0" max="100" step="0.5" placeholder="标准杯/日" disabled={facts.drinking === "从不饮酒"} value={facts.standardDrinksPerDay ?? ""} onChange={(event) => { setFacts((current) => ({ ...current, standardDrinksPerDay: event.target.value })); setAlcoholSaveMessage(""); }} /></label>
          </div>
          <p className="history-unit-note">按标准杯填写（约 10 g 纯酒精）；不确定时可咨询评估者。</p>
          <Button size="small" loading={alcoholSaving} onClick={() => void saveAlcoholHistory()}>保存饮酒史用于随访</Button>
          {alcoholSaveMessage && <p className="smoking-save-message">{alcoholSaveMessage}</p>}
        </section>

        <section className={`safety-state ${redFlag ? "alert" : ""}`}>
          {redFlag ? <WarningFilled /> : <SafetyCertificateFilled />}
          <div>
            <strong>{redFlag ? `已识别：${redFlag}` : "高危信号排查"}</strong>
            <p>{redFlag ? "建议优先进行线下专科评估" : "等待核实吞咽、呼吸及咯血情况"}</p>
          </div>
        </section>

        <section className="state-section">
          <div className="section-title">
            <h3>多模态资料</h3>
            <span>{voiceResult ? "1/3" : "0/3"}</span>
          </div>
          <button className="modality-item" type="button" onClick={() => setVoicePanelOpen(true)}>
            <span className="modality-icon voice"><CustomerServiceOutlined /></span>
            <span><strong>六元音嗓音</strong><small>{voiceResult ? (voiceCorrection ? `原 ${voiceResult.predicted_label} · 纠正 ${voiceCorrection.corrected_label}` : `已分析 · ${voiceResult.predicted_label}`) : "待采集"}</small></span>
            {voiceResult ? <CheckCircleFilled /> : <PlusOutlined />}
          </button>
          <button className="modality-item" type="button" onClick={() => fileInputRef.current?.click()}>
            <span className="modality-icon wlb"><FileImageOutlined /></span>
            <span><strong>WLB 喉镜</strong><small>可选 · 待上传</small></span>
            <PlusOutlined />
          </button>
          <button className="modality-item" type="button" onClick={() => fileInputRef.current?.click()}>
            <span className="modality-icon nbi"><FileImageOutlined /></span>
            <span><strong>NBI 喉镜</strong><small>可选 · 待上传</small></span>
            <PlusOutlined />
          </button>
        </section>

        <div className="audit-note">
          <SafetyCertificateFilled />
          <span><strong>会话可审计</strong><small>Agent 行为与状态变更将被记录</small></span>
        </div>
      </aside>
      </main>
    </>
  );
}
