import assert from "node:assert/strict";
import test from "node:test";
import { inspectAudio, makeBackup, maxAudioBytes, parseBackup, reportHtml } from "../lib/browser-store.ts";

const syntheticCase = () => ({
  id: crypto.randomUUID(), alias: "合成测试", age: "30", gender: "女", location: "测试地区",
  complaint: "合成主诉", createdAt: "2026-10-28T00:00:00.000Z", updatedAt: "2026-10-28T00:00:00.000Z",
  notes: "合成备注", answers: { 持续时间: "不足 2 周" }, messages: [], audio: {},
});

test("backup round trip preserves records and imports under new IDs", async () => {
  const item = syntheticCase();
  const backup = await makeBackup([item]);
  const result = await parseBackup(new File([backup], "synthetic.json"));
  assert.equal(result.length, 1);
  assert.notEqual(result[0].id, item.id);
  assert.equal(result[0].notes, item.notes);
  assert.deepEqual(result[0].answers, item.answers);
});

test("invalid backup and invalid message roles fail before writing records", async () => {
  await assert.rejects(parseBackup(new File(['{"cases":[]}'], "invalid.json")));
  const item = syntheticCase();
  item.messages.push({ role: "external", text: "invalid", at: item.createdAt });
  await assert.rejects(parseBackup(new File([JSON.stringify({ format: "voicescope-local", version: 1, cases: [item] })], "invalid.json")));
});

test("audio backup preserves original bytes and rechecks technical metrics on import", async (t) => {
  const originalReader = globalThis.FileReader;
  const originalContext = globalThis.AudioContext;
  globalThis.FileReader = class {
    readAsDataURL(blob) { void blob.arrayBuffer().then((buffer) => { this.result = `data:${blob.type};base64,${Buffer.from(buffer).toString("base64")}`; this.onload(); }); }
  };
  globalThis.AudioContext = class {
    async decodeAudioData() { return { duration: 2, numberOfChannels: 1, getChannelData: () => new Float32Array(100).fill(0.1) }; }
    async close() {}
  };
  t.after(() => { globalThis.FileReader = originalReader; globalThis.AudioContext = originalContext; });
  const item = syntheticCase();
  const bytes = new Uint8Array([1, 2, 3, 4, 254, 255]);
  item.audio.a = { blob: new Blob([bytes], { type: "audio/wav" }), filename: "synthetic.wav", at: item.createdAt, metrics: { seconds: 999, rmsDb: 0, clipped: 0, warnings: [] } };
  const backup = await makeBackup([item]);
  const [restored] = await parseBackup(new File([backup], "synthetic.json"));
  assert.deepEqual(new Uint8Array(await restored.audio.a.blob.arrayBuffer()), bytes);
  assert.equal(restored.audio.a.metrics.seconds, 2);
});

test("exported HTML escapes all patient fields and cannot run supplied markup", async () => {
  const item = syntheticCase();
  const attack = '<script>alert("synthetic")</script><img src="https://example.invalid/collect">';
  item.alias = attack; item.notes = attack; item.complaint = attack;
  item.answers = { [attack]: attack };
  item.messages = [{ role: "user", text: attack, at: item.createdAt }];
  const html = await reportHtml(item).text();
  assert(!html.includes("<script>")); assert(!html.includes("<img"));
  assert(html.includes("&lt;script&gt;"));
  assert(html.includes("default-src 'none'"));
  assert(html.includes("不是诊断报告"));
});

test("empty and oversized audio is refused before opening an audio device", async () => {
  await assert.rejects(inspectAudio(new Blob([])), /非空音频/);
  await assert.rejects(inspectAudio(new Blob([new Uint8Array(maxAudioBytes + 1)])), /15 MB/);
});

test("technical audio checks assess channels without stereo cancellation", async (t) => {
  const original = globalThis.AudioContext;
  let closed = false;
  globalThis.AudioContext = class {
    async decodeAudioData() { return { duration: 2, numberOfChannels: 2, getChannelData: (channel) => new Float32Array(1000).fill(channel ? -0.2 : 0.2) }; }
    async close() { closed = true; }
  };
  t.after(() => { globalThis.AudioContext = original; });
  const result = await inspectAudio(new Blob(["synthetic"]));
  assert.equal(result.seconds, 2); assert.equal(result.clipped, 0);
  assert(Math.abs(result.rmsDb + 13.9794) < 0.01);
  assert.deepEqual(result.warnings, []); assert(closed);
});

test("silence, short recordings and clipping produce technical warnings", async (t) => {
  const original = globalThis.AudioContext;
  let samples = new Float32Array(1000);
  globalThis.AudioContext = class {
    async decodeAudioData() { return { duration: 0.2, numberOfChannels: 1, getChannelData: () => samples }; }
    async close() {}
  };
  t.after(() => { globalThis.AudioContext = original; });
  const silent = await inspectAudio(new Blob(["synthetic"]));
  assert(silent.warnings.some((value) => value.includes("静音")));
  assert(silent.warnings.some((value) => value.includes("较短")));
  samples = new Float32Array(1000).fill(1);
  const clipped = await inspectAudio(new Blob(["synthetic"]));
  assert(clipped.warnings.some((value) => value.includes("削波")));
});
