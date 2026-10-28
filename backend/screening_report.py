"""Build and render traceable patient/professional screening reports."""
from __future__ import annotations

import hashlib
import html
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo


Audience = Literal["patient", "professional"]
REPORT_SCHEMA_VERSION = "voicescope-screening-report/v1"
REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
VOWEL_NAMES = {"a": "/a/ 啊", "o": "/o/ 喔", "e": "/e/ 鹅", "i": "/i/ 衣", "u": "/u/ 乌", "v": "/ü/ 迂"}
REVIEW_LABELS = {
    "pending": "待医生复核",
    "in_review": "医生复核中",
    "completed": "医生已复核",
    "needs_information": "需补充资料",
}
QUALITY_LABELS = {"pass": "质量通过", "warning": "可用，但存在提示", "re_record": "需要重录", "unknown": "未记录质量结果"}


class ScreeningReportNotFound(LookupError):
    """The requested voice-analysis event does not exist in this case."""


class ScreeningReportConflict(RuntimeError):
    """History and research storage disagree for the requested analysis."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[-1] if items else None


def _quality_summary(audio_quality: Any) -> dict[str, Any]:
    if not isinstance(audio_quality, dict) or not audio_quality:
        return {"status": "unknown", "label": QUALITY_LABELS["unknown"], "accepted": None, "issues": [], "vowels": []}
    vowels = []
    statuses: list[str] = []
    all_issues: list[str] = []
    for key in ("a", "o", "e", "i", "u", "v"):
        raw = audio_quality.get(key)
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or ("pass" if raw.get("accepted") else "re_record"))
        statuses.append(status)
        issues = [
            {
                "code": str(issue.get("code", "")),
                "severity": str(issue.get("severity", "warning")),
                "message": str(issue.get("message", "")),
                "guidance": str(issue.get("guidance", "")),
            }
            for issue in raw.get("issues", [])
            if isinstance(issue, dict)
        ]
        all_issues.extend(issue["message"] for issue in issues if issue["message"])
        vowels.append({
            "key": key,
            "name": VOWEL_NAMES[key],
            "status": status,
            "status_label": QUALITY_LABELS.get(status, status),
            "accepted": bool(raw.get("accepted")),
            "metrics": raw.get("metrics") if isinstance(raw.get("metrics"), dict) else None,
            "issues": issues,
            "scope_note": str(raw.get("scope_note", "")),
        })
    if not vowels:
        return {"status": "unknown", "label": QUALITY_LABELS["unknown"], "accepted": None, "issues": [], "vowels": []}
    status = "re_record" if "re_record" in statuses or any(not item["accepted"] for item in vowels) else "warning" if "warning" in statuses else "pass"
    return {"status": status, "label": QUALITY_LABELS[status], "accepted": status != "re_record", "issues": all_issues, "vowels": vowels}


def _history_text(value: Any, status_key: str, years_key: str, amount_key: str, unit: str) -> str:
    if not isinstance(value, dict) or not value.get(status_key):
        return "未登记"
    details = []
    if value.get(years_key) is not None:
        details.append(f"{value[years_key]} 年")
    if value.get(amount_key) is not None:
        details.append(f"{value[amount_key]} {unit}")
    return f"{value[status_key]}" + (f"（{'，'.join(details)}）" if details else "")


def build_screening_report(
    store: Any,
    research_store: Any,
    conversation_id: str,
    audience: Audience,
    analysis_request_id: str | None = None,
) -> dict[str, Any]:
    """Return a report snapshot locked to one immutable voice-analysis event."""
    facts = store.facts(conversation_id)
    metadata = store.metadata(conversation_id)
    events = store.events(conversation_id)
    voice_events = [event for event in events if event["kind"] == "voice_analysis"]
    if analysis_request_id:
        selected_event = next((event for event in voice_events if event["payload"].get("request_id") == analysis_request_id), None)
    else:
        selected_event = _latest(voice_events)
    if selected_event is None:
        raise ScreeningReportNotFound(analysis_request_id or "latest")

    result = selected_event["payload"]
    request_id = str(result.get("request_id", ""))
    detail = research_store.case_detail(conversation_id)
    stored_analysis = next(
        (
            item for item in detail["analyses"]
            if item["analysis_type"] == "voice_analysis" and item["result"].get("request_id") == request_id
        ),
        None,
    )
    if stored_analysis is not None:
        stored_result = stored_analysis["result"]
        if (
            stored_result.get("predicted_label") != result.get("predicted_label")
            or stored_result.get("predicted_probability") != result.get("predicted_probability")
        ):
            raise ScreeningReportConflict("历史事件与分析记录不一致，已停止出具报告")

    corrections = [
        event["payload"] for event in events
        if event["kind"] == "voice_diagnosis_correction" and event["payload"].get("model_request_id") == request_id
    ]
    review = _latest(detail["reviews"])
    review_status = str(review.get("review_status")) if review else "pending"
    urgent = bool(store.case_points(conversation_id).get("urgent"))
    if urgent:
        next_step = "问诊记录包含危险信号，请优先按急症提示尽快线下评估，不要等待模型结果。"
    elif review and review.get("recommendation"):
        next_step = str(review["recommendation"])
    elif review_status == "needs_information":
        next_step = "请按医生复核要求补充资料，再完成复核。"
    else:
        next_step = "建议结合症状由耳鼻喉科或嗓音专科医生复核，必要时完善喉镜等检查。"

    model = result.get("model") if isinstance(result.get("model"), dict) else {}
    model_version = str(model.get("version") or model.get("model_version") or "未登记")
    patient = facts.get("patient_info", {}) if isinstance(facts, dict) else {}
    quality = _quality_summary(result.get("audio_quality"))
    report_revision = int(review.get("version", 0)) if review else 0
    content_revision = "|".join([
        metadata["updated_at"],
        str(report_revision),
        *(str(item.get("correction_id", "")) for item in corrections),
        *(str(item.get("id", "")) for item in detail["examinations"]),
    ])
    report_key = f"{conversation_id}|{request_id}|{content_revision}|{audience}|{REPORT_SCHEMA_VERSION}"
    snapshot: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": hashlib.sha256(report_key.encode()).hexdigest()[:24],
        "audience": audience,
        "generated_at": _iso_now(),
        "timezone": "Asia/Shanghai",
        "case": {
            "conversation_id": conversation_id,
            "case_number": conversation_id[:12].upper(),
            "status": metadata["status"],
            "created_at": metadata["created_at"],
            "updated_at": metadata["updated_at"],
            "patient": {
                "name": patient.get("name") or "未登记",
                "age": patient.get("age"),
                "gender": patient.get("gender") or "未登记",
                "location": patient.get("location") or "未登记",
                "chief_complaint": patient.get("chief_complaint") or "未登记",
            },
            "urgent": urgent,
        },
        "analysis": {
            "request_id": request_id,
            "history_event_id": selected_event["id"],
            "analysis_record_id": stored_analysis.get("id") if stored_analysis else None,
            "analyzed_at": selected_event["created_at"],
            "result_label": result.get("predicted_label") or "未输出",
            "result_probability": result.get("predicted_probability"),
            "warnings": [str(item) for item in result.get("warnings", [])],
            "disclaimer": str(result.get("disclaimer") or "该结果仅用于健康筛查与临床决策支持，不能替代医生诊断或喉镜检查。"),
            "model": {
                "version": model_version,
                "architecture": model.get("architecture") or "未登记",
            },
            "storage_consistency": "verified" if stored_analysis else "history_only_legacy",
        },
        "capture_quality": quality,
        "review": {
            "status": review_status,
            "status_label": REVIEW_LABELS.get(review_status, review_status),
            "version": report_revision,
            "reviewed_at": review.get("created_at") if review else None,
            "recommendation": str(review.get("recommendation") or "") if review else "",
        },
        "next_step": next_step,
        "result_boundary": "本报告展示的是研究模型筛查输出，不是疾病确诊；不能替代医生面诊、喉镜、病理或其他专科检查。",
    }
    if audience == "professional":
        points = store.case_points(conversation_id).get("points", {})
        snapshot["analysis"]["probabilities"] = result.get("probabilities", [])
        snapshot["analysis"]["vowel_attention"] = result.get("vowel_attention", {})
        snapshot["interview"] = {
            "case_points": [
                {"key": key, "label": value.get("label", key), "value": value.get("value") or "未采集", "status": value.get("status")}
                for key, value in points.items() if isinstance(value, dict)
            ],
            "smoking_history": _history_text(facts.get("smoking_history"), "smoking_status", "smoking_years", "cigarettes_per_day", "支/日"),
            "alcohol_history": _history_text(facts.get("alcohol_history"), "drinking_status", "drinking_years", "standard_drinks_per_day", "标准杯/日"),
        }
        acoustic_events = [event for event in events if event["kind"] == "acoustic_analysis" and event["created_at"] <= selected_event["created_at"]]
        snapshot["acoustic_analysis"] = _latest(acoustic_events)["payload"] if acoustic_events else None
        snapshot["corrections"] = corrections
        snapshot["examinations"] = detail["examinations"]
        snapshot["review"].update({
            "reviewer": review.get("reviewer") if review else None,
            "opinion": review.get("opinion") if review else None,
            "history_count": len(detail["reviews"]),
        })
    return snapshot


def _display_time(value: Any) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(REPORT_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def _text(value: Any) -> str:
    return html.escape("—" if value is None or value == "" else str(value), quote=True)


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _list(items: list[str], empty: str = "无") -> str:
    return "".join(f"<li>{_text(item)}</li>" for item in items) if items else f"<li>{_text(empty)}</li>"


def render_screening_report_html(report: dict[str, Any], download_url: str) -> str:
    """Render a self-contained, escaped A4 report with print controls."""
    case = report["case"]
    patient = case["patient"]
    analysis = report["analysis"]
    quality = report["capture_quality"]
    review = report["review"]
    professional = report["audience"] == "professional"
    title = "声航·嗓音筛查专业报告" if professional else "声航·嗓音筛查报告"
    probability_rows = ""
    if professional:
        probability_rows = "".join(
            f"<tr><td>{_text(item.get('label'))}</td><td>{_percent(item.get('probability'))}</td></tr>"
            for item in analysis.get("probabilities", []) if isinstance(item, dict)
        ) or "<tr><td colspan=\"2\">无概率记录</td></tr>"
    quality_rows = "".join(
        f"<tr><td>{_text(item['name'])}</td><td>{_text(item['status_label'])}</td>"
        f"<td>{_text((item.get('metrics') or {}).get('duration_seconds'))}</td>"
        f"<td>{_text((item.get('metrics') or {}).get('active_voice_seconds'))}</td>"
        f"<td>{_text('；'.join(issue['message'] for issue in item['issues']) or '无')}</td></tr>"
        for item in quality["vowels"]
    ) or "<tr><td colspan=\"5\">该次分析未保留逐元音质量数据</td></tr>"

    professional_sections = ""
    if professional:
        interview = report.get("interview", {})
        point_rows = "".join(
            f"<tr><th>{_text(item['label'])}</th><td>{_text(item['value'])}</td></tr>"
            for item in interview.get("case_points", [])
        ) or "<tr><td>无结构化要点</td></tr>"
        acoustic = report.get("acoustic_analysis") or {}
        acoustic_content = (
            f"MPT：{_text(acoustic.get('mpt_seconds'))} 秒；F0：{_text(acoustic.get('f0_hz'))} Hz；"
            f"Jitter：{_text(acoustic.get('jitter_percent'))}%；Shimmer：{_text(acoustic.get('shimmer_percent'))}%；"
            f"平均强度：{_text(acoustic.get('mean_intensity_dbfs'))} dBFS"
            if acoustic else "该次六元音分析前无基础声学测量记录。"
        )
        examination_items = [
            f"{item.get('examination_type', '检查')}（{item.get('examination_date') or _display_time(item.get('created_at'))}）：{item.get('findings', '')}"
            for item in report.get("examinations", [])
        ]
        correction_items = [
            f"第 {item.get('correction_number', '—')} 次：{item.get('original_predicted_label', '—')} → {item.get('corrected_label', '—')}；{item.get('note') or '未填写依据'}"
            for item in report.get("corrections", [])
        ]
        professional_sections = f"""
        <section><h2>问诊与生活史</h2><table class="kv">{point_rows}<tr><th>量化吸烟史</th><td>{_text(interview.get('smoking_history'))}</td></tr><tr><th>量化饮酒史</th><td>{_text(interview.get('alcohol_history'))}</td></tr></table></section>
        <section><h2>完整概率分布</h2><table><thead><tr><th>模型标签</th><th>条件概率</th></tr></thead><tbody>{probability_rows}</tbody></table></section>
        <section><h2>基础声学测量</h2><p>{acoustic_content}</p></section>
        <section><h2>检查资料</h2><ul>{_list(examination_items, '尚未录入检查资料')}</ul></section>
        <section><h2>人工纠正</h2><ul>{_list(correction_items, '无针对该次分析的人工纠正')}</ul></section>
        <section><h2>医生复核详情</h2><table class="kv"><tr><th>复核人</th><td>{_text(review.get('reviewer'))}</td></tr><tr><th>复核意见</th><td>{_text(review.get('opinion'))}</td></tr><tr><th>下一步建议</th><td>{_text(review.get('recommendation') or report['next_step'])}</td></tr></table></section>
        """

    provenance_card = (
        f"<div class=\"card\"><span>数据一致性</span><strong>{_text(analysis['storage_consistency'])}</strong>"
        f"<br>历史事件 #{_text(analysis['history_event_id'])}</div>"
        if professional
        else "<div class=\"card\"><span>报告状态</span><strong>已锁定本次分析</strong><br>再次分析会生成新的请求号</div>"
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_text(title)}｜{_text(case['case_number'])}</title>
<style>
@page {{ size: A4; margin: 14mm; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; color: #17322f; background: #eef5f3; font: 14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
.toolbar {{ position: sticky; top: 0; display: flex; justify-content: flex-end; gap: 10px; padding: 12px max(16px,calc((100vw - 210mm)/2)); background: #0d675f; }} .toolbar a,.toolbar button {{ border: 0; border-radius: 7px; padding: 8px 14px; color: #0d675f; background: white; font: inherit; cursor: pointer; text-decoration: none; }}
main {{ width: min(210mm,100%); min-height: 297mm; margin: 22px auto; padding: 18mm; background: white; box-shadow: 0 10px 30px #17322f20; }} header {{ display: flex; justify-content: space-between; gap: 18px; padding-bottom: 18px; border-bottom: 3px solid #168a80; }} h1 {{ margin: 0; font-size: 25px; }} .subtitle {{ color: #607672; }} .report-id {{ text-align: right; font-size: 12px; }} .flag {{ display: inline-block; margin-top: 7px; border-radius: 99px; padding: 3px 9px; color: #fff; background: #168a80; font-size: 12px; }} .flag.warn {{ background: #b56d00; }}
section {{ break-inside: avoid; margin-top: 20px; }} h2 {{ margin: 0 0 9px; color: #0d675f; font-size: 16px; }} .summary {{ display: grid; grid-template-columns: repeat(2,1fr); gap: 9px; }} .card {{ padding: 11px 13px; border: 1px solid #d8e6e3; border-radius: 8px; background: #f8fbfa; }} .card span {{ display: block; color: #6a7e7a; font-size: 12px; }} .card strong {{ font-size: 16px; }} table {{ width: 100%; border-collapse: collapse; }} th,td {{ padding: 7px 9px; border: 1px solid #dbe7e5; text-align: left; vertical-align: top; }} th {{ background: #f2f8f6; }} table.kv th {{ width: 25%; }} .boundary {{ padding: 12px 14px; border-left: 4px solid #b56d00; background: #fff7e8; }} footer {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid #dbe7e5; color: #6a7e7a; font-size: 11px; }}
@media(max-width:700px) {{ main {{ margin: 0; padding: 22px 16px; box-shadow: none; }} .summary {{ grid-template-columns: 1fr; }} header {{ flex-direction: column; }} .report-id {{ text-align: left; }} }}
@media print {{ body {{ background: white; }} .toolbar {{ display: none; }} main {{ width: auto; min-height: 0; margin: 0; padding: 0; box-shadow: none; }} }}
</style></head><body>
<div class="toolbar"><button type="button" onclick="window.print()">打印 / 另存为 PDF</button><a href="{_text(download_url)}">下载 HTML</a></div>
<main><header><div><div class="subtitle">VOICESCOPE SCREENING REPORT</div><h1>{_text(title)}</h1><span class="flag{' warn' if case['urgent'] else ''}">{'危险信号优先' if case['urgent'] else _text(review['status_label'])}</span></div><div class="report-id">报告号 {_text(report['report_id'])}<br>病例号 {_text(case['case_number'])}<br>出具时间 {_text(_display_time(report['generated_at']))}</div></header>
<section><h2>基本信息</h2><div class="summary"><div class="card"><span>患者</span><strong>{_text(patient['name'])}</strong><br>{_text(patient['age'])} 岁 · {_text(patient['gender'])}</div><div class="card"><span>主要症状</span><strong>{_text(patient['chief_complaint'])}</strong><br>{_text(patient['location'])}</div><div class="card"><span>分析时间</span><strong>{_text(_display_time(analysis['analyzed_at']))}</strong><br>请求号 {_text(analysis['request_id'])}</div><div class="card"><span>模型版本</span><strong>{_text(analysis['model']['version'])}</strong><br>{_text(analysis['model']['architecture'])}</div></div></section>
<section><h2>采集质量</h2><p><strong>{_text(quality['label'])}</strong>；质量门只评估录音技术可用性，不会因病理性嗓音表现本身拒收。</p><table><thead><tr><th>元音</th><th>状态</th><th>时长（秒）</th><th>有效发声（秒）</th><th>问题</th></tr></thead><tbody>{quality_rows}</tbody></table></section>
<section><h2>筛查结果</h2><div class="summary"><div class="card"><span>研究模型输出</span><strong>{_text(analysis['result_label'])}</strong></div><div class="card"><span>最高条件概率</span><strong>{_percent(analysis['result_probability'])}</strong></div><div class="card"><span>医生复核</span><strong>{_text(review['status_label'])}</strong><br>复核版本 v{_text(review['version'])}</div>{provenance_card}</div><p class="boundary">{_text(report['result_boundary'])}</p></section>
<section><h2>建议下一步</h2><p>{_text(report['next_step'])}</p><ul>{_list(analysis['warnings'], '本次分析无附加技术警告')}</ul></section>
{professional_sections}
<footer>{_text(analysis['disclaimer'])}<br>报告数据合约：{_text(report['schema_version'])}；时区：{_text(report['timezone'])}。本页可打印或由浏览器另存为 PDF。</footer></main></body></html>"""


def report_download_url(base_path: str, analysis_request_id: str) -> str:
    return f"{base_path}?analysis_request_id={quote(analysis_request_id)}&format=html&download=true"
