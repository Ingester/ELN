"""
ELN App — FastAPI Server
All REST endpoints. Runs on Windows as the data host (port 8000).
"""

from __future__ import annotations
import os
import json
import shutil
import time
from datetime import datetime, timezone
from typing import Optional, Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db.database as db_ops
from db.models import ProtocolDefinition
from utils.report_generator import generate_report
from utils.i18n import localize_html

app = FastAPI(title="ELN API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _photos_dir() -> str:
    return db_ops.get_photos_dir()


def _html_response(content: str, **kwargs) -> HTMLResponse:
    """Return localized HTML for the native web pages."""
    return HTMLResponse(localize_html(content), **kwargs)


# Mount static photo files
def mount_photos(application: FastAPI) -> None:
    photos_dir = _photos_dir()
    application.mount("/photos", StaticFiles(directory=photos_dir), name="photos")


# ─────────────────────────────────────────────
# Pydantic request/response schemas
# ─────────────────────────────────────────────

class ExperimentCreate(BaseModel):
    name: str
    protocol_json: str          # full ProtocolDefinition JSON string
    protocol_id: Optional[int] = None
    notes: str = ""


class ExperimentUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class StepUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    fields_json: Optional[str] = None
    values_json: Optional[str] = None
    description_overrides_json: Optional[str] = None
    photo_paths: Optional[str] = None
    photo_pending: Optional[bool] = None
    timer_override_seconds: Optional[int] = None
    timer_finished_at: Optional[str] = None
    overtime_seconds: Optional[int] = None


class TimerUpdate(BaseModel):
    total_seconds: Optional[int] = None
    remaining_seconds: Optional[int] = None
    overtime_seconds: Optional[int] = None
    status: Optional[str] = None
    timer_finished_at: Optional[str] = None
    started_at: Optional[str] = None


class TimerSync(BaseModel):
    total_seconds: int
    remaining_seconds: int
    overtime_seconds: int = 0
    status: str
    action: str = "sync"
    elapsed_seconds: Optional[int] = None


class ProtocolCreate(BaseModel):
    protocol_json: str          # full ProtocolDefinition JSON string


class BoxCreate(BaseModel):
    box_name: str
    box_size: int = 10
    notes: str = ""


class BoxUpdate(BaseModel):
    box_name: Optional[str] = None
    box_size: Optional[int] = None
    notes: Optional[str] = None


class SlotUpdate(BaseModel):
    sample_name: str
    notes: str = ""
    experiment_id: Optional[int] = None
    step_id: Optional[int] = None


class StorageRegister(BaseModel):
    item_id: int
    box_id: int
    row_label: str
    col_label: str
    notes: str = ""


class StorageCreate(BaseModel):
    item_label: str
    tube_type: str = ""
    notes_template: str = ""
    default_box: str = ""


# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ─────────────────────────────────────────────
# Native web experiment runner
# ─────────────────────────────────────────────

@app.get("/run", response_class=HTMLResponse)
@app.get("/mobile", response_class=HTMLResponse)
def experiment_runner(experiment_id: Optional[int] = Query(None)):
    return _html_response("""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>ELN 实验执行</title>
  <style>
    :root { color-scheme: light; --orange:#fb8c00; --green:#43a047; --line:#e8e8e8; --text:#222; --muted:#777; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--text); background:#f7f7f7; }
    header { position:sticky; top:0; z-index:3; background:#fff; border-bottom:1px solid var(--line); padding:12px 14px; display:flex; gap:10px; align-items:center; }
    h1 { margin:0; font-size:18px; flex:1; }
    main { max-width: 980px; margin:0 auto; padding:12px; }
    button, label.button, a.button, select, input, textarea { font: inherit; }
    button, label.button, a.button { border:0; border-radius:8px; background:var(--orange); color:#fff; padding:10px 14px; display:inline-block; text-decoration:none; cursor:pointer; }
    button.secondary, label.secondary { background:#eee; color:#333; }
    button.green { background:var(--green); }
    button:disabled { background:#bbb; }
    .status { font-size:12px; color:var(--muted); }
    .card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px; margin:12px 0; box-shadow:0 1px 3px #0000000f; }
    .stepper { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:10px 0; }
    .stepper button { min-width:70px; }
    .progress { flex:1; height:6px; border-radius:999px; background:#e7e7e7; overflow:hidden; }
    .progress > div { height:100%; background:var(--orange); transition:width .2s ease; }
    .step-title { font-size:18px; font-weight:700; margin:4px 0 8px; }
    .desc { line-height:1.55; color:#333; }
    .desc p { margin:0 0 10px; }
    .desc h1, .desc h2, .desc h3 { margin:14px 0 8px; line-height:1.25; }
    .desc h1 { font-size:22px; }
    .desc h2 { font-size:19px; }
    .desc h3 { font-size:16px; }
    .desc ul, .desc ol { margin:8px 0 10px 24px; padding:0; }
    .desc table { border-collapse:collapse; width:100%; margin:10px 0; font-size:14px; }
    .desc th, .desc td { border:1px solid var(--line); padding:7px 9px; vertical-align:top; }
    .desc th { background:#fafafa; font-weight:700; }
    .desc code { background:#f3f3f3; border-radius:4px; padding:1px 4px; }
    .desc pre { background:#f6f6f6; border:1px solid var(--line); border-radius:8px; padding:10px; overflow:auto; }
    .desc blockquote { border-left:4px solid #ddd; margin:8px 0; padding:3px 12px; color:#555; }
    .field { margin-top:12px; }
    .field label { display:block; color:#555; font-size:13px; margin-bottom:5px; }
    .field input, .field select, .field textarea { width:100%; border:1px solid #ddd; border-radius:8px; padding:11px; background:#fff; }
    .field textarea { min-height:180px; resize:vertical; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:14px; }
    .done { color:var(--green); font-weight:700; }
    .warn { color:#d98200; }
    .photo-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:8px; }
    input[type=file] { position:absolute; left:-9999px; width:1px; height:1px; opacity:0; }
    .photos a { color:var(--orange); margin-right:10px; }
    .small { color:var(--muted); font-size:12px; }
    .timer { border:1px solid #ffd8a8; background:#fff8ef; border-radius:10px; padding:12px; margin-top:12px; }
    .timer-display { font-size:32px; font-weight:800; color:var(--orange); letter-spacing:0; }
    .timer-edit { display:flex; gap:8px; align-items:end; }
    .timer-edit input { width:120px; }
    .timer.over { background:#fff0f0; border-color:#ffc7c7; }
    .timer.over .timer-display { color:#d32f2f; }
    .section-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:14px; }
    .section-head h2 { margin:0; font-size:15px; color:#555; font-weight:600; }
    .edit-link { background:transparent; color:var(--orange); padding:3px 0; border-radius:0; }
    .wrapup { border:1px solid #cfe8d4; background:#f2fbf4; border-radius:10px; padding:12px; margin-top:14px; }
    .modal-backdrop { position:fixed; inset:0; z-index:20; display:none; align-items:center; justify-content:center; background:#00000033; padding:18px; }
    .modal-backdrop.open { display:flex; }
    .modal { width:min(720px, 100%); max-height:90vh; overflow:auto; background:#fff; border-radius:12px; padding:16px; box-shadow:0 12px 36px #0000002e; }
    .modal h2 { margin:0 0 12px; font-size:18px; }
    .modal textarea, .modal input { width:100%; border:1px solid #ddd; border-radius:8px; padding:10px; }
  </style>
</head>
<body>
  <header>
    <h1>ELN 实验执行</h1>
    <span id="net" class="status">连接中</span>
    <button class="secondary" onclick="syncCurrentAndNow()">同步</button>
  </header>
  <main>
    <section class="card">
      <div class="small">原生 Web 执行页。记录会先保存在本机浏览器缓存，联网时同步回电脑数据库。</div>
      <div class="actions">
        <a class="button secondary" id="backToFlet" href="/">返回首页</a>
        <select id="experimentSelect" onchange="selectExperiment(this.value)"></select>
        <button onclick="loadExperiments()">刷新实验</button>
      </div>
      <div id="queueInfo" class="status"></div>
    </section>
    <section id="steps"></section>
  </main>
  <div id="modalBackdrop" class="modal-backdrop">
    <div class="modal">
      <h2 id="modalTitle">编辑</h2>
      <div id="modalBody"></div>
      <div class="actions">
        <button id="modalSave">保存</button>
        <button class="secondary" onclick="closeModal()">取消</button>
      </div>
      <div id="modalStatus" class="status"></div>
    </div>
  </div>

<script>
const LS = {
  experiments: "eln.mobile.experiments",
  selected: "eln.mobile.selectedExperiment",
  stepIndexPrefix: "eln.mobile.stepIndex.",
  stepsPrefix: "eln.mobile.steps.",
  draftsPrefix: "eln.mobile.drafts.",
  descPrefix: "eln.mobile.desc.",
  timerPrefix: "eln.mobile.timer.",
  timers: "eln.mobile.timers",
  queue: "eln.mobile.queue"
};

let selectedExperiment = new URLSearchParams(window.location.search).get("experiment_id") || localStorage.getItem(LS.selected) || "";
let steps = [];
let experiments = [];
const timerSync = {};
const timerLastSync = {};
let modalSaveHandler = null;

function getQueue(){ try { return JSON.parse(localStorage.getItem(LS.queue) || "[]"); } catch { return []; } }
function setQueue(q){ localStorage.setItem(LS.queue, JSON.stringify(q)); renderQueueInfo(); }
function getTimers(){ try { return JSON.parse(localStorage.getItem(LS.timers) || "{}"); } catch { return {}; } }
function setTimers(t){ localStorage.setItem(LS.timers, JSON.stringify(t)); }
function enqueue(job){
  const q = getQueue();
  q.push({...job, id: Date.now() + "-" + Math.random().toString(16).slice(2)});
  setQueue(q);
}
function stepKey(expId){ return LS.stepsPrefix + expId; }
function stepIndexKey(expId){ return LS.stepIndexPrefix + expId; }
function draftKey(stepId){ return LS.draftsPrefix + stepId; }
function descKey(stepId){ return LS.descPrefix + stepId; }
function timerKey(stepId){ return LS.timerPrefix + stepId; }
function net(text, ok=true){ const el=document.getElementById("net"); el.textContent=text; el.style.color=ok ? "#43a047" : "#d98200"; }
function renderQueueInfo(){ document.getElementById("queueInfo").textContent = "待同步：" + getQueue().length + " 项"; }

async function api(path, opts={}){
  const res = await fetch(path, {headers: {"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

async function loadExperiments(){
  try {
    const active = await api("/api/experiments?status=active");
    const wrap = await api("/api/experiments?status=needs_wrapup");
    const exps = [...active, ...wrap];
    experiments = exps;
    localStorage.setItem(LS.experiments, JSON.stringify(exps));
    if(!selectedExperiment && exps[0]) selectedExperiment = String(exps[0].id);
    localStorage.setItem(LS.selected, selectedExperiment);
    renderExperiments(exps);
    if(selectedExperiment) await loadSteps(selectedExperiment);
    net("已连接", true);
  } catch(e) {
    net("离线缓存", false);
    experiments = JSON.parse(localStorage.getItem(LS.experiments) || "[]");
    renderExperiments(experiments);
    if(selectedExperiment) renderSteps(JSON.parse(localStorage.getItem(stepKey(selectedExperiment)) || "[]"));
  }
}

function currentExperiment(){
  return experiments.find(e => String(e.id) === String(selectedExperiment)) || null;
}

function renderExperiments(exps){
  const sel = document.getElementById("experimentSelect");
  sel.innerHTML = "";
  for(const e of exps){
    const opt = document.createElement("option");
    opt.value = e.id; opt.textContent = e.name + " · " + e.completed_steps + "/" + e.total_steps;
    if(String(e.id) === String(selectedExperiment)) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function selectExperiment(id){
  selectedExperiment = id;
  localStorage.setItem(LS.selected, id);
  await loadSteps(id);
}

async function loadSteps(expId){
  try {
    steps = await api(`/api/experiments/${expId}/steps`);
    localStorage.setItem(stepKey(expId), JSON.stringify(steps));
    net("已连接", true);
  } catch(e) {
    steps = JSON.parse(localStorage.getItem(stepKey(expId)) || "[]");
    net("离线缓存", false);
  }
  renderSteps(steps);
}

function mergedValues(step){
  let vals = {...(step.values || {})};
  try { vals = {...vals, ...JSON.parse(localStorage.getItem(draftKey(step.id)) || "{}")}; } catch {}
  return vals;
}

function mergedOverrides(step){
  let vals = {...(step.description_overrides || {})};
  try { vals = {...vals, ...JSON.parse(localStorage.getItem(descKey(step.id)) || "{}")}; } catch {}
  return vals;
}

function mergedTimerSeconds(step){
  const raw = localStorage.getItem(timerKey(step.id));
  if(raw !== null && raw !== "") return Math.max(0, parseInt(raw, 10) || 0);
  return step.effective_timer_seconds || 0;
}

function timerMinutes(seconds){
  const n = (seconds || 0) / 60;
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

function currentStepIndex(){
  const raw = localStorage.getItem(stepIndexKey(selectedExperiment));
  const idx = Math.max(0, parseInt(raw || "0", 10) || 0);
  return Math.min(idx, Math.max(0, steps.length - 1));
}

function setCurrentStepIndex(idx){
  if(!selectedExperiment) return;
  const safe = Math.min(Math.max(0, idx), Math.max(0, steps.length - 1));
  localStorage.setItem(stepIndexKey(selectedExperiment), String(safe));
}

function goStep(delta){
  setCurrentStepIndex(currentStepIndex() + delta);
  renderSteps(steps);
}

function goToFirstOpenStep(){
  const open = steps.findIndex(s => !s.completed_at);
  if(open >= 0) setCurrentStepIndex(open);
  renderSteps(steps);
}

function openModal(title, html, onSave){
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modalStatus").textContent = "";
  modalSaveHandler = onSave;
  document.getElementById("modalBackdrop").classList.add("open");
}

function closeModal(){
  document.getElementById("modalBackdrop").classList.remove("open");
  modalSaveHandler = null;
}

document.getElementById("modalSave").addEventListener("click", async () => {
  if(!modalSaveHandler) return;
  try {
    await modalSaveHandler();
    closeModal();
  } catch(e) {
    document.getElementById("modalStatus").textContent = "保存失败：" + e.message;
  }
});

function setLocalStep(stepId, patch){
  steps = steps.map(s => s.id === stepId ? {...s, ...patch} : s);
  if(selectedExperiment) localStorage.setItem(stepKey(selectedExperiment), JSON.stringify(steps));
}

async function editExperimentName(){
  const exp = currentExperiment();
  if(!exp) return;
  openModal("修改实验名", `<div class="field"><label>实验名</label><input id="editExperimentName" value="${esc(exp.name)}" /></div>`, async () => {
    const name = document.getElementById("editExperimentName").value.trim();
    if(!name) throw new Error("实验名不能为空");
    await api(`/api/experiments/${selectedExperiment}`, {method:"PATCH", body:JSON.stringify({name})});
    experiments = experiments.map(e => String(e.id) === String(selectedExperiment) ? {...e, name} : e);
    localStorage.setItem(LS.experiments, JSON.stringify(experiments));
    renderExperiments(experiments);
  });
}

function editStepText(stepId, key, title){
  const step = steps.find(s => s.id === stepId);
  if(!step) return;
  const value = step[key] || "";
  const control = key === "description"
    ? `<textarea id="editStepText">${esc(value)}</textarea>`
    : `<input id="editStepText" value="${esc(value)}" />`;
  openModal(title, `<div class="field">${control}</div>`, async () => {
    const next = document.getElementById("editStepText").value;
    await api(`/api/steps/${stepId}`, {method:"PATCH", body:JSON.stringify({[key]: next})});
    setLocalStep(stepId, {[key]: next});
    renderSteps(steps);
  });
}

function fieldsToText(fields){
  return (fields || []).map(f => {
    const options = (f.options || []).join(",");
    return [f.key || "", f.label || "", f.type || "text", f.default || "", f.required ? "true" : "false", options].join(" | ");
  }).join("\\n");
}

function parseFieldsText(text){
  return text.split(/\\r?\\n/).map(line => line.trim()).filter(Boolean).map((line, i) => {
    const parts = line.split("|").map(p => p.trim());
    const key = parts[0] || `field_${i + 1}`;
    const label = parts[1] || key;
    const type = ["text", "number", "dropdown"].includes(parts[2]) ? parts[2] : "text";
    const options = (parts[5] || "").split(",").map(x => x.trim()).filter(Boolean);
    return {key, label, type, default: parts[3] || "", required: /^true|是|yes|1$/i.test(parts[4] || ""), options};
  });
}

function editFields(stepId){
  const step = steps.find(s => s.id === stepId);
  if(!step) return;
  const help = "每行一个字段：key | 显示名称 | 类型(text/number/dropdown) | 默认值 | 是否必填(true/false) | 下拉选项1,选项2";
  openModal("编辑记录字段", `
    <p class="small">${help}</p>
    <div class="field"><textarea id="editFieldsText">${esc(fieldsToText(step.fields || []))}</textarea></div>
  `, async () => {
    const fields = parseFieldsText(document.getElementById("editFieldsText").value);
    await api(`/api/steps/${stepId}`, {method:"PATCH", body:JSON.stringify({fields_json: JSON.stringify(fields)})});
    setLocalStep(stepId, {fields});
    renderSteps(steps);
  });
}

function renderSteps(items){
  const root = document.getElementById("steps");
  root.innerHTML = "";
  if(!items.length){ root.innerHTML = '<div class="card small">暂无缓存步骤。联网后点“刷新实验”。</div>'; return; }
  steps = items;
  const idx = currentStepIndex();
  const step = items[idx];
  const pct = Math.round(((idx + 1) / items.length) * 100);
  const vals = mergedValues(step);
  const totalSeconds = mergedTimerSeconds(step);
  const isLast = idx >= items.length - 1;
  const card = document.createElement("article");
  card.className = "card";
  const fields = (step.fields || []).map(f => {
    const v = vals[f.key] ?? f.default ?? "";
    if(f.type === "dropdown"){
      const opts = (f.options || []).map(o => `<option value="${esc(o)}" ${o==v?"selected":""}>${esc(o)}</option>`).join("");
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><select data-step="${step.id}" data-key="${esc(f.key)}" onchange="saveDraft(${step.id})">${opts}</select></div>`;
    }
    return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><input data-step="${step.id}" data-key="${esc(f.key)}" value="${esc(v)}" oninput="saveDraft(${step.id})" /></div>`;
  }).join("");
  const photos = (step.photo_paths || []).map((p,i) => `<a href="/photos/${esc(p)}" target="_blank">照片${i+1}</a>`).join("");
  const timerBlock = totalSeconds > 0 ? `
    <div class="timer" id="timer-box-${step.id}">
      <div class="small">本地计时 · 电脑端负责响铃</div>
      <div class="timer-display" id="timer-display-${step.id}">${fmt(totalSeconds)}</div>
      <div class="field timer-edit">
        <label>计时器</label>
        <input type="number" min="0" step="0.1" value="${timerMinutes(totalSeconds)}" onchange="saveTimerOverride(${step.experiment_id}, ${step.id}, this.value)" />
        <span class="small">分钟</span>
      </div>
      <div class="actions">
        <button onclick="startLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})">开始</button>
        <button class="secondary" onclick="pauseLocalTimer(${step.experiment_id}, ${step.id})">暂停</button>
        <button class="secondary" onclick="resetLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})">重置</button>
      </div>
      <div class="status" id="timer-status-${step.id}"></div>
    </div>` : "";
  const photoBlock = step.has_camera ? `
    <div class="field">
      <label>照片</label>
      <div class="photos">${photos || '<span class="small">暂无照片</span>'}</div>
      <form class="photo-row" onsubmit="uploadPhoto(event, ${step.id})">
        <label class="button" for="cam-${step.id}">拍照</label>
        <label class="button secondary" for="gal-${step.id}">相册</label>
        <input id="cam-${step.id}" name="file" type="file" accept="image/*" capture="environment" onchange="markFile(this)" />
        <input id="gal-${step.id}" name="file2" type="file" accept="image/*" onchange="markFile(this)" />
        <button type="submit">上传</button>
        <span class="small" id="file-${step.id}">未选择</span>
      </form>
    </div>` : "";
  const wrapupBlock = isLast ? `
    <div class="wrapup">
      <div class="section-head">
        <h2>实验收尾</h2>
      </div>
      <div class="small">最后一步完成后，可以在电脑端补充储存物品、登记 Box 位置、查看和保存报告。</div>
      <div class="actions">
        <a class="button" href="/run/storage/${selectedExperiment}">储存物品 / 登记位置</a>
        <a class="button secondary" href="/run/report/${selectedExperiment}">查看报告</a>
        <button class="secondary" onclick="finishExperiment()">结束实验</button>
      </div>
    </div>` : "";
  card.innerHTML = `
    <div class="stepper">
      <button class="secondary" onclick="goStep(-1)" ${idx === 0 ? "disabled" : ""}>上一步</button>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <button class="secondary" onclick="goStep(1)" ${idx >= items.length - 1 ? "disabled" : ""}>下一步</button>
    </div>
    <div class="section-head">
      <div class="small">Step ${idx + 1} / ${items.length}${step.completed_at ? ' · <span class="done">已完成</span>' : ''}</div>
      <button class="edit-link" onclick="editExperimentName()">改实验名</button>
    </div>
    <div class="section-head">
      <div class="step-title">${esc(step.title)}</div>
      <button class="edit-link" onclick="editStepText(${step.id}, 'title', '修改步骤标题')">改标题</button>
    </div>
    <div class="section-head">
      <h2>步骤说明</h2>
      <button class="edit-link" onclick="editStepText(${step.id}, 'description', '修改步骤说明')">编辑整段说明</button>
    </div>
    <div class="desc">${renderDescription(step)}</div>
    ${timerBlock}
    <div class="section-head">
      <h2>记录数据</h2>
      <button class="edit-link" onclick="editFields(${step.id})">编辑字段</button>
    </div>
    ${fields}
    ${photoBlock}
    ${wrapupBlock}
    <div class="actions">
      <button onclick="saveAndSync(${step.id})">保存</button>
      <button class="green" onclick="completeStep(${step.id})" ${step.completed_at ? "disabled" : ""}>完成步骤</button>
      <button class="secondary" onclick="goToFirstOpenStep()">未完成步骤</button>
      <span class="status" id="status-${step.id}"></span>
    </div>`;
  root.appendChild(card);
  refreshTimers();
  return;
  for(const step of items){
    const vals = mergedValues(step);
    const totalSeconds = mergedTimerSeconds(step);
    const card = document.createElement("article");
    card.className = "card";
    const fields = (step.fields || []).map(f => {
      const v = vals[f.key] ?? f.default ?? "";
      if(f.type === "dropdown"){
        const opts = (f.options || []).map(o => `<option value="${esc(o)}" ${o==v?"selected":""}>${esc(o)}</option>`).join("");
        return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><select data-step="${step.id}" data-key="${esc(f.key)}" onchange="saveDraft(${step.id})">${opts}</select></div>`;
      }
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><input data-step="${step.id}" data-key="${esc(f.key)}" value="${esc(v)}" oninput="saveDraft(${step.id})" /></div>`;
    }).join("");
    const photos = (step.photo_paths || []).map((p,i) => `<a href="/photos/${esc(p)}" target="_blank">照片${i+1}</a>`).join("");
    const timerBlock = totalSeconds > 0 ? `
      <div class="timer" id="timer-box-${step.id}">
        <div class="small">本地计时 · 电脑端负责响铃</div>
        <div class="timer-display" id="timer-display-${step.id}">${fmt(totalSeconds)}</div>
        <div class="field timer-edit">
          <label>计时器</label>
          <input type="number" min="0" step="0.1" value="${timerMinutes(totalSeconds)}" onchange="saveTimerOverride(${step.experiment_id}, ${step.id}, this.value)" />
          <span class="small">分钟</span>
        </div>
        <div class="actions">
          <button onclick="startLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})">开始</button>
          <button class="secondary" onclick="pauseLocalTimer(${step.experiment_id}, ${step.id})">暂停</button>
          <button class="secondary" onclick="resetLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})">重置</button>
        </div>
        <div class="status" id="timer-status-${step.id}"></div>
      </div>` : "";
    const photoBlock = step.has_camera ? `
      <div class="field">
        <label>照片</label>
        <div class="photos">${photos || '<span class="small">暂无照片</span>'}</div>
        <form class="photo-row" onsubmit="uploadPhoto(event, ${step.id})">
          <label class="button" for="cam-${step.id}">拍照</label>
          <label class="button secondary" for="gal-${step.id}">相册</label>
          <input id="cam-${step.id}" name="file" type="file" accept="image/*" capture="environment" onchange="markFile(this)" />
          <input id="gal-${step.id}" name="file2" type="file" accept="image/*" onchange="markFile(this)" />
          <button type="submit">上传</button>
          <span class="small" id="file-${step.id}">未选择</span>
        </form>
      </div>` : "";
    card.innerHTML = `
      <div class="small">Step ${step.step_index + 1}${step.completed_at ? ' · <span class="done">已完成</span>' : ''}</div>
      <div class="step-title">${esc(step.title)}</div>
      <div class="desc">${renderDescription(step)}</div>
      ${timerBlock}
      ${fields}
      ${photoBlock}
      <div class="actions">
        <button onclick="saveAndSync(${step.id})">保存</button>
        <button class="green" onclick="completeStep(${step.id})" ${step.completed_at ? "disabled" : ""}>完成步骤</button>
        <span class="status" id="status-${step.id}"></span>
      </div>`;
    root.appendChild(card);
  }
  refreshTimers();
}

function collectValues(stepId){
  const vals = {};
  document.querySelectorAll(`[data-step="${stepId}"]`).forEach(el => vals[el.dataset.key] = el.value || "");
  return vals;
}
function saveDraft(stepId){ localStorage.setItem(draftKey(stepId), JSON.stringify(collectValues(stepId))); }
function status(stepId, text){ const el=document.getElementById("status-"+stepId); if(el) el.textContent=text; }

function renderDescription(step){
  return markdownToHtml(step.description || "");
}

function markdownToHtml(markdown){
  const lines = String(markdown || "").replace(/\\r\\n/g, "\\n").split("\\n");
  const out = [];
  let i = 0;
  let inCode = false;
  let codeLines = [];
  let paragraph = [];

  function flushParagraph(){
    if(paragraph.length){
      out.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }
  function flushCode(){
    out.push(`<pre><code>${esc(codeLines.join("\\n"))}</code></pre>`);
    codeLines = [];
  }
  function isTableSep(line){
    return /^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?\\s*$/.test(line);
  }
  function splitTableRow(line){
    let trimmed = line.trim();
    if(trimmed.startsWith("|")) trimmed = trimmed.slice(1);
    if(trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
    return trimmed.split("|").map(cell => cell.trim());
  }

  while(i < lines.length){
    const raw = lines[i];
    const line = raw.trimEnd();
    if(line.trim().startsWith("```")){
      if(inCode){ flushCode(); inCode = false; } else { flushParagraph(); inCode = true; codeLines = []; }
      i++;
      continue;
    }
    if(inCode){ codeLines.push(raw); i++; continue; }

    if(!line.trim()){ flushParagraph(); i++; continue; }

    if(i + 1 < lines.length && line.includes("|") && isTableSep(lines[i + 1])){
      flushParagraph();
      const headers = splitTableRow(line);
      i += 2;
      const rows = [];
      while(i < lines.length && lines[i].trim() && lines[i].includes("|")){
        rows.push(splitTableRow(lines[i]));
        i++;
      }
      out.push(`<table><thead><tr>${headers.map(h => `<th>${renderInline(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, idx) => `<td>${renderInline(row[idx] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }

    const heading = /^(#{1,3})\\s+(.+)$/.exec(line);
    if(heading){
      flushParagraph();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if(/^>\\s+/.test(line)){
      flushParagraph();
      const quote = [];
      while(i < lines.length && /^>\\s+/.test(lines[i])){
        quote.push(lines[i].replace(/^>\\s+/, ""));
        i++;
      }
      out.push(`<blockquote>${quote.map(q => `<p>${renderInline(q)}</p>`).join("")}</blockquote>`);
      continue;
    }

    if(/^[-*+]\\s+/.test(line)){
      flushParagraph();
      const items = [];
      while(i < lines.length && /^[-*+]\\s+/.test(lines[i].trimEnd())){
        items.push(lines[i].trimEnd().replace(/^[-*+]\\s+/, ""));
        i++;
      }
      out.push(`<ul>${items.map(item => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if(/^\\d+[.)]\\s+/.test(line)){
      flushParagraph();
      const items = [];
      while(i < lines.length && /^\\d+[.)]\\s+/.test(lines[i].trimEnd())){
        items.push(lines[i].trimEnd().replace(/^\\d+[.)]\\s+/, ""));
        i++;
      }
      out.push(`<ol>${items.map(item => `<li>${renderInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    paragraph.push(line.trim());
    i++;
  }
  if(inCode) flushCode();
  flushParagraph();
  return out.join("");
}

function renderInline(text){
  let html = esc(text);
  const codes = [];
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(`<code>${code}</code>`);
    return `\\u0000${codes.length - 1}\\u0000`;
  });
  html = html.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\\*([^*]+)\\*(?!\\*)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
  html = html.replace(/\\u0000(\\d+)\\u0000/g, (_, idx) => codes[Number(idx)] || "");
  return html;
}

function saveTimerOverride(expId, stepId, minutes){
  const value = parseFloat(minutes);
  if(Number.isNaN(value) || value < 0){ status(stepId, "计时器请输入有效分钟数"); return; }
  const seconds = Math.max(0, Math.round(value * 60));
  localStorage.setItem(timerKey(stepId), String(seconds));
  resetLocalTimer(expId, stepId, seconds, "override");
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  status(stepId, "计时器已存本地，等待同步");
  syncNow();
}

function patchPayload(stepId){
  const body = {values_json: JSON.stringify(collectValues(stepId))};
  const timerRaw = localStorage.getItem(timerKey(stepId));
  if(timerRaw !== null && timerRaw !== "") body.timer_override_seconds = Math.max(0, parseInt(timerRaw, 10) || 0);
  return body;
}

function fmt(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2,"0") + ":" + String(s).padStart(2,"0");
}

function timerState(stepId, total){
  const timers = getTimers();
  const t = timers[stepId];
  if(!t) return {status:"idle", total, remaining:total, startedAt:null, pausedRemaining:total};
  if(t.status === "running"){
    const elapsed = Math.floor((Date.now() - t.startedAt) / 1000);
    return {...t, remaining: t.pausedRemaining - elapsed};
  }
  const pausedRemaining = t.pausedRemaining ?? t.remaining ?? total;
  return {...t, remaining: pausedRemaining, pausedRemaining};
}

async function tellComputerTimer(expId, stepId, action){
  try {
    if(action === "start" || action === "reset"){
      await fetch(`/api/steps/${stepId}`, {
        method:"PATCH",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(patchPayload(stepId))
      });
    }
    const res = await fetch(`/api/timers/${expId}/${stepId}/${action}`, {method:"POST"});
    if(!res.ok) throw new Error(await res.text());
  } catch(e) {
    status(stepId, "电脑端计时器未同步，请确认电脑服务在线");
  }
}

function queueComputerTimer(expId, stepId, action){
  timerSync[stepId] = (timerSync[stepId] || Promise.resolve()).then(
    () => tellComputerTimer(expId, stepId, action)
  );
  return timerSync[stepId];
}

async function tellComputerTimerState(expId, stepId, state, patchStep=false){
  try {
    if(patchStep){
      await fetch(`/api/steps/${stepId}`, {
        method:"PATCH",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(patchPayload(stepId))
      });
    }
    const payload = {
      total_seconds: state.total,
      remaining_seconds: Math.max(0, Math.floor(state.remaining ?? state.pausedRemaining ?? state.total)),
      overtime_seconds: state.remaining < 0 ? Math.abs(Math.floor(state.remaining)) : 0,
      status: state.status,
      action: state.action || "sync",
      elapsed_seconds: Math.max(0, Math.floor(state.elapsedSeconds ?? elapsedForState(state)))
    };
    const res = await fetch(`/api/timers/${expId}/${stepId}/sync`, {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    if(!res.ok) throw new Error(await res.text());
    timerLastSync[stepId] = Date.now();
  } catch(e) {
    status(stepId, "电脑端计时器未同步，请确认电脑服务在线");
  }
}

function queueComputerTimerState(expId, stepId, state, patchStep=false){
  timerSync[stepId] = (timerSync[stepId] || Promise.resolve()).then(
    () => tellComputerTimerState(expId, stepId, state, patchStep)
  );
  return timerSync[stepId];
}

function startLocalTimer(expId, stepId, total){
  const timers = getTimers();
  const current = timerState(stepId, total);
  const remaining = current.status === "paused" ? current.remaining : total;
  timers[stepId] = {status:"running", total, pausedRemaining:remaining, remaining, startedAt:Date.now()};
  setTimers(timers);
  const next = timerState(stepId, total);
  next.action = "start";
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next, true);
  refreshTimers();
}

function pauseLocalTimer(expId, stepId){
  const timers = getTimers();
  const current = timerState(stepId, timers[stepId]?.total || 0);
  const remaining = Math.max(0, current.remaining);
  timers[stepId] = {status:"paused", total:current.total, pausedRemaining:remaining, remaining, startedAt:null};
  setTimers(timers);
  const next = timerState(stepId, current.total);
  next.action = "pause";
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next);
  refreshTimers();
}

function resetLocalTimer(expId, stepId, total, action="reset"){
  const timers = getTimers();
  const current = timerState(stepId, timers[stepId]?.total || total);
  timers[stepId] = {status:"idle", total, pausedRemaining:total, remaining:total, startedAt:null};
  setTimers(timers);
  const next = timerState(stepId, total);
  next.action = action;
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next, true);
  refreshTimers();
}

function elapsedForState(state){
  if(!state) return 0;
  const total = Math.max(0, Math.floor(state.total || 0));
  const remaining = Math.floor(state.remaining ?? state.pausedRemaining ?? total);
  if(remaining < 0) return total + Math.abs(remaining);
  return Math.max(0, total - Math.max(0, remaining));
}

function refreshTimers(){
  const timers = getTimers();
  for(const step of steps){
    const totalSeconds = mergedTimerSeconds(step);
    if(!totalSeconds) continue;
    const current = timerState(step.id, totalSeconds);
    const display = document.getElementById("timer-display-"+step.id);
    const box = document.getElementById("timer-box-"+step.id);
    const text = document.getElementById("timer-status-"+step.id);
    if(!display || !box) continue;
    if(current.status === "running" && current.remaining <= 0){
      display.textContent = "+" + fmt(Math.abs(current.remaining));
      box.classList.add("over");
      if(text) text.textContent = "时间到。电脑端会响铃；当前页面同步显示。";
      if(!timers[step.id]?.alerted){
        try { navigator.vibrate && navigator.vibrate([300,120,300,120,600]); } catch {}
        timers[step.id] = {...timers[step.id], alerted:true};
        setTimers(timers);
      }
    } else {
      display.textContent = fmt(current.remaining ?? totalSeconds);
      box.classList.remove("over");
      if(text) text.textContent = current.status === "running" ? "计时中" : (current.status === "paused" ? "已暂停" : "未开始");
    }
    if(current.status === "running" && current.remaining > 0 && Date.now() - (timerLastSync[step.id] || 0) > 3000){
      queueComputerTimerState(step.experiment_id, step.id, current);
    }
  }
}

function saveAndSync(stepId){
  saveDraft(stepId);
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  status(stepId, "已存本地，等待同步");
  syncNow();
}

function requiredErrors(step){
  const vals = collectValues(step.id);
  return (step.fields || []).filter(f => f.required && !String(vals[f.key] || "").trim()).map(f => f.label);
}

function completeStep(stepId){
  const step = steps.find(s => s.id === stepId);
  const errs = requiredErrors(step);
  if(errs.length){ status(stepId, "必填未填：" + errs.join("、")); return; }
  saveDraft(stepId);
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  enqueue({type:"completeStep", stepId});
  status(stepId, "已加入完成队列");
  const idx = currentStepIndex();
  if(idx < steps.length - 1){
    setCurrentStepIndex(idx + 1);
    renderSteps(steps);
  } else {
    enqueue({type:"patchExperiment", expId:Number(selectedExperiment), payload:{status:"needs_wrapup"}});
  }
  syncNow();
}

async function finishExperiment(){
  if(!selectedExperiment) return;
  if(!confirm("确认结束实验？结束后仍然可以从历史记录查看报告。")) return;
  try {
    await api(`/api/experiments/${selectedExperiment}`, {method:"PATCH", body:JSON.stringify({status:"completed"})});
    location.href = `/run/report/${selectedExperiment}`;
  } catch(e) {
    alert("结束实验失败：" + e.message);
  }
}

async function syncNow(){
  let q = getQueue();
  if(!q.length){ renderQueueInfo(); return; }
  const remain = [];
  for(const job of q){
    try {
      if(job.type === "patchStep"){
        const payload = job.payload || {values_json: JSON.stringify(job.values || {})};
        await api(`/api/steps/${job.stepId}`, {method:"PATCH", body: JSON.stringify(payload)});
      } else if(job.type === "completeStep"){
        await api(`/api/steps/${job.stepId}/complete`, {method:"POST", body: "{}"});
      } else if(job.type === "patchExperiment"){
        await api(`/api/experiments/${job.expId}`, {method:"PATCH", body: JSON.stringify(job.payload || {})});
      }
    } catch(e) {
      remain.push(job);
    }
  }
  setQueue(remain);
  if(selectedExperiment) await loadSteps(selectedExperiment);
}

function syncCurrentAndNow(){
  if(steps.length){
    const step = steps[currentStepIndex()];
    if(step){
      saveDraft(step.id);
      enqueue({type:"patchStep", stepId: step.id, payload: patchPayload(step.id)});
      status(step.id, "当前步骤已加入同步");
    }
  }
  syncNow();
}

function markFile(input){
  const form = input.closest("form");
  const stepId = form.getAttribute("onsubmit").match(/, (\\d+)\\)/)?.[1];
  if(stepId && input.files && input.files.length) document.getElementById("file-"+stepId).textContent = input.files[0].name;
}

async function uploadPhoto(event, stepId){
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.file.files[0] || form.file2.files[0];
  if(!file){ document.getElementById("file-"+stepId).textContent = "请先选择照片"; return; }
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`/api/photos/upload?step_id=${stepId}`, {method:"POST", body:fd});
    if(!res.ok) throw new Error(await res.text());
    document.getElementById("file-"+stepId).textContent = "上传完成";
    await loadSteps(selectedExperiment);
  } catch(e) {
    document.getElementById("file-"+stepId).textContent = "上传失败，文件保留在本机，请联网后重试";
  }
}

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }

window.addEventListener("online", syncNow);
setInterval(syncNow, 15000);
setInterval(refreshTimers, 1000);
renderQueueInfo();
document.getElementById("backToFlet").href = `${location.protocol}//${location.hostname}:8550/`;
loadExperiments();
</script>
</body>
</html>
""", headers={"Cache-Control": "no-store, max-age=0"})


# ─────────────────────────────────────────────
# Experiments
# ─────────────────────────────────────────────

@app.get("/api/experiments")
def list_experiments(status: Optional[str] = Query(None)):
    exps = db_ops.list_experiments(status=status)
    result = []
    for e in exps:
        progress = db_ops.get_experiment_progress(e.id)
        result.append({
            "id": e.id,
            "name": e.name,
            "created_at": e.created_at,
            "status": e.status,
            "protocol_id": e.protocol_id,
            "notes": e.notes,
            **progress,
        })
    return result


@app.post("/api/experiments", status_code=201)
def create_experiment(body: ExperimentCreate):
    try:
        protocol = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    exp = db_ops.create_experiment(
        name=body.name,
        protocol=protocol,
        protocol_id=body.protocol_id,
        notes=body.notes,
    )
    return {"id": exp.id, "name": exp.name, "created_at": exp.created_at, "status": exp.status}


@app.get("/api/experiments/{exp_id}")
def get_experiment(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    progress = db_ops.get_experiment_progress(exp_id)
    return {
        "id": exp.id, "name": exp.name, "created_at": exp.created_at,
        "status": exp.status, "protocol_json": exp.protocol_json,
        "protocol_id": exp.protocol_id, "notes": exp.notes,
        **progress,
    }


@app.patch("/api/experiments/{exp_id}")
def update_experiment(exp_id: int, body: ExperimentUpdate):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    updates = body.model_dump(exclude_none=True)
    exp = db_ops.update_experiment(exp_id, **updates)
    return {"id": exp.id, "name": exp.name, "status": exp.status}


@app.delete("/api/experiments/{exp_id}", status_code=204)
def delete_experiment(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    db_ops.delete_experiment(exp_id)


# ─────────────────────────────────────────────
# Steps
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/steps")
def get_steps(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_steps(exp_id)
    return [_step_to_dict(s) for s in steps]


@app.get("/api/steps/{step_id}")
def get_step(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    return _step_to_dict(step)


@app.patch("/api/steps/{step_id}")
def update_step(step_id: int, body: StepUpdate):
    if not db_ops.get_step(step_id):
        raise HTTPException(404, "Step not found")
    updates = body.model_dump(exclude_none=True)
    # Convert bool to int for SQLite
    if "photo_pending" in updates:
        updates["photo_pending"] = int(updates["photo_pending"])
    step = db_ops.update_step(step_id, **updates)
    return _step_to_dict(step)


@app.post("/api/steps/{step_id}/complete")
def complete_step(step_id: int):
    if not db_ops.get_step(step_id):
        raise HTTPException(404, "Step not found")
    step = db_ops.complete_step(step_id)
    return _step_to_dict(step)


@app.get("/api/experiments/{exp_id}/pending_photos")
def get_pending_photos(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_pending_photo_steps(exp_id)
    return [_step_to_dict(s) for s in steps]


def _step_to_dict(step) -> dict:
    return {
        "id": step.id,
        "experiment_id": step.experiment_id,
        "step_index": step.step_index,
        "title": step.title,
        "description": step.description,
        "timer_seconds": step.timer_seconds,
        "timer_override_seconds": step.timer_override_seconds,
        "effective_timer_seconds": step.effective_timer_seconds,
        "timer_finished_at": step.timer_finished_at,
        "overtime_seconds": step.overtime_seconds,
        "has_camera": bool(step.has_camera),
        "camera_required": bool(step.camera_required),
        "fields": step.get_fields(),
        "values": step.get_values(),
        "description_overrides": step.get_description_overrides(),
        "photo_paths": step.get_photo_paths(),
        "photo_pending": bool(step.photo_pending),
        "completed_at": step.completed_at,
    }


# ─────────────────────────────────────────────
# Timers
# ─────────────────────────────────────────────

@app.get("/api/timers/{exp_id}/{step_id}")
def get_timer(exp_id: int, step_id: int):
    timer = db_ops.get_timer(exp_id, step_id)
    if not timer:
        raise HTTPException(404, "Timer not found")
    return _timer_to_dict(timer)


@app.put("/api/timers/{exp_id}/{step_id}")
def upsert_timer(exp_id: int, step_id: int, body: TimerUpdate):
    # Fetch existing or use defaults
    existing = db_ops.get_timer(exp_id, step_id)
    total = body.total_seconds if body.total_seconds is not None else (existing.total_seconds if existing else 0)
    remaining = body.remaining_seconds if body.remaining_seconds is not None else (existing.remaining_seconds if existing else total)
    overtime = body.overtime_seconds if body.overtime_seconds is not None else (existing.overtime_seconds if existing else 0)
    status = body.status if body.status is not None else (existing.status if existing else "idle")
    finished_at = body.timer_finished_at if body.timer_finished_at is not None else (existing.timer_finished_at if existing else None)
    started_at = body.started_at if body.started_at is not None else (existing.started_at if existing else None)

    timer = db_ops.upsert_timer(
        experiment_id=exp_id, step_id=step_id,
        total_seconds=total, remaining_seconds=remaining,
        overtime_seconds=overtime, status=status,
        timer_finished_at=finished_at, started_at=started_at,
    )
    return _timer_to_dict(timer)


@app.patch("/api/timers/{exp_id}/{step_id}")
def patch_timer(exp_id: int, step_id: int, body: TimerUpdate):
    return upsert_timer(exp_id, step_id, body)


def _require_timer_step(exp_id: int, step_id: int):
    step = db_ops.get_step(step_id)
    if not step or step.experiment_id != exp_id:
        raise HTTPException(404, "Step not found for experiment")
    if step.effective_timer_seconds <= 0:
        raise HTTPException(400, "Step has no timer")
    return step


def _ensure_managed_timer(exp_id: int, step_id: int):
    step = _require_timer_step(exp_id, step_id)
    from timer_manager import get_timer_manager

    tm = get_timer_manager()
    tm.start()
    state = tm.get_state(exp_id, step_id)
    if state is None:
        state = tm.create_or_restore(exp_id, step_id, step.effective_timer_seconds)
    elif (
        state.total_seconds != step.effective_timer_seconds
        and state.status not in ("overtime", "confirmed")
    ):
        state = tm.set_total_seconds(exp_id, step_id, step.effective_timer_seconds) or state
    return tm, state


@app.post("/api/timers/{exp_id}/{step_id}/start")
def start_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.start_timer(exp_id, step_id)
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/sync")
def sync_managed_timer(exp_id: int, step_id: int, body: TimerSync):
    _require_timer_step(exp_id, step_id)
    from timer_manager import get_timer_manager

    tm = get_timer_manager()
    tm.start()
    state = tm.sync_timer(
        exp_id,
        step_id,
        total_seconds=body.total_seconds,
        remaining_seconds=body.remaining_seconds,
        overtime_seconds=body.overtime_seconds,
        status=body.status,
        action=body.action,
        elapsed_seconds=body.elapsed_seconds,
    )
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/pause")
def pause_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.pause_timer(exp_id, step_id)
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/reset")
def reset_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.reset_timer(exp_id, step_id)
    try:
        from notifications import stop_alert_sound
        stop_alert_sound()
    except Exception:
        pass
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/confirm")
def confirm_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.confirm_overtime(exp_id, step_id)
    try:
        from notifications import stop_alert_sound
        stop_alert_sound()
    except Exception:
        pass
    return _timer_state_to_dict(state)


@app.get("/api/timers/active")
def list_active_timers():
    timers = db_ops.list_active_timers()
    return [_timer_to_dict(t) for t in timers]


def _timer_to_dict(timer) -> dict:
    return {
        "id": timer.id,
        "experiment_id": timer.experiment_id,
        "step_id": timer.step_id,
        "total_seconds": timer.total_seconds,
        "remaining_seconds": timer.remaining_seconds,
        "overtime_seconds": timer.overtime_seconds,
        "status": timer.status,
        "timer_finished_at": timer.timer_finished_at,
        "started_at": timer.started_at,
        "updated_at": timer.updated_at,
    }


def _timer_state_to_dict(state) -> dict:
    if state is None:
        raise HTTPException(404, "Timer not found")
    return {
        "id": state.timer_id,
        "experiment_id": state.experiment_id,
        "step_id": state.step_id,
        "total_seconds": state.total_seconds,
        "remaining_seconds": state.remaining_seconds,
        "overtime_seconds": state.overtime_seconds,
        "display_seconds": state.display_seconds,
        "status": state.status,
        "timer_finished_at": state.timer_finished_at,
        "started_at": state.started_at,
    }


# ─────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────

@app.post("/api/photos/upload")
async def upload_photo(
    step_id: int = Query(...),
    file: UploadFile = File(...),
):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")

    # Save to photos/{exp_id}/{step_id}_{timestamp}.jpg
    photos_dir = _photos_dir()
    sub_dir = os.path.join(photos_dir, str(step.experiment_id))
    os.makedirs(sub_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg"
    filename = f"step{step_id}_{ts}{ext}"
    filepath = os.path.join(sub_dir, filename)

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if os.path.getsize(filepath) <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(400, "照片文件为空，请重新拍照或选择图片")

    # Relative path for URL construction
    rel_path = f"{step.experiment_id}/{filename}"
    db_ops.add_photo_to_step(step_id, rel_path)

    return {"path": rel_path, "url": f"/photos/{rel_path}"}


@app.get("/web/upload/{step_id}", response_class=HTMLResponse)
def web_upload_form(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    _write_eln_return_target(step.experiment_id, step_id)
    app_url = _eln_step_url(step.experiment_id, step_id)
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上传照片 · Step {step.step_index + 1}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    main {{ max-width: 560px; }}
    h1 {{ font-size: 22px; }}
    form {{ display: grid; gap: 16px; margin-top: 24px; }}
    input, button, a.button, label.button {{ font-size: 16px; }}
    button, a.button, label.button {{ display: inline-block; width: fit-content; border: 0; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; cursor: pointer; text-decoration: none; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
    .actions {{ display: flex; gap: 12px; align-items: center; margin-bottom: 28px; flex-wrap: wrap; }}
    .upload-actions {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    input[type=file] {{ position: absolute; left: -9999px; width: 1px; height: 1px; opacity: 0; }}
    .filename {{ min-height: 24px; color: #444; }}
    .muted {{ color: #666; }}
  </style>
</head>
<body>
  <main>
    <div class="actions">
      <a class="button" href="{app_url}">返回 ELN</a>
      <a class="button secondary" href="javascript:history.back()">返回上一页</a>
    </div>
    <h1>上传照片</h1>
    <p><b>{_html_escape(step.title)}</b></p>
    <p class="muted">在 iPhone 上点“拍照”会打开相机；也可以从相册选择。上传成功后，回到 ELN 页面点击“刷新照片”。</p>
    <form method="post" enctype="multipart/form-data">
      <div class="upload-actions">
        <label class="button" for="cameraFile">拍照</label>
        <label class="button secondary" for="galleryFile">从相册选择</label>
      </div>
      <input id="cameraFile" name="file" type="file" accept="image/*" capture="environment" />
      <input id="galleryFile" name="file" type="file" accept="image/*" />
      <div id="filename" class="filename">尚未选择照片</div>
      <button type="submit">上传</button>
    </form>
  </main>
  <script>
    const cameraFile = document.getElementById("cameraFile");
    const galleryFile = document.getElementById("galleryFile");
    const filename = document.getElementById("filename");
    function showName(input) {{
      if (input.files && input.files.length) {{
        filename.textContent = "已选择：" + input.files[0].name;
        if (input === cameraFile) galleryFile.value = "";
        if (input === galleryFile) cameraFile.value = "";
      }}
    }}
    cameraFile.addEventListener("change", () => showName(cameraFile));
    galleryFile.addEventListener("change", () => showName(galleryFile));
  </script>
</body>
</html>
""")


@app.get("/web/open/{exp_id}", response_class=HTMLResponse)
def web_open_experiment(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    target = f"{_web_base_url()}/stepper/{exp_id}?t={int(time.time())}"
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>打开实验</title>
  <script>
    window.location.replace("{target}");
  </script>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    a {{ color: #fb8c00; }}
  </style>
</head>
<body>
  <p>正在打开实验：{_html_escape(exp.name)}</p>
  <p><a href="{target}">如果没有自动打开，请点击这里</a></p>
</body>
</html>
""")


@app.get("/web/edit-step/{step_id}", response_class=HTMLResponse)
def web_edit_step_form(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    values = step.get_values()
    fields = step.get_fields()
    app_url = _eln_step_url(step.experiment_id, step_id)
    field_html = []
    for field in fields:
        key = _html_escape(field.key)
        label = _html_escape(field.label + (" *" if field.required else ""))
        value = _html_escape(values.get(field.key, field.default) or "")
        if field.type == "dropdown":
            options = []
            current = values.get(field.key, field.default) or ""
            for opt in field.options:
                selected = " selected" if opt == current else ""
                options.append(f'<option value="{_html_escape(opt)}"{selected}>{_html_escape(opt)}</option>')
            control = f'<select name="{key}">{"".join(options)}</select>'
        else:
            control = f'<input name="{key}" type="text" value="{value}" autocomplete="off" />'
        field_html.append(f"""
        <label>
          <span>{label}</span>
          {control}
        </label>
        """)

    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>编辑记录数据 · Step {step.step_index + 1}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #222; }}
    main {{ max-width: 680px; }}
    h1 {{ font-size: 22px; margin-bottom: 6px; }}
    .muted {{ color: #666; font-size: 13px; }}
    form {{ display: grid; gap: 16px; margin-top: 22px; }}
    label {{ display: grid; gap: 6px; }}
    label span {{ color: #555; font-size: 14px; }}
    input, select {{ font: inherit; border: 1px solid #ddd; border-radius: 8px; padding: 10px 12px; max-width: 420px; }}
    button, a.button {{ display: inline-block; width: fit-content; border: 0; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; cursor: pointer; text-decoration: none; font: inherit; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
    .actions {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 8px; }}
  </style>
</head>
<body>
  <main>
    <div class="actions">
      <a class="button secondary" href="{app_url}">返回 ELN</a>
    </div>
    <h1>编辑记录数据</h1>
    <p><b>{_html_escape(step.title)}</b></p>
    <p class="muted">这里使用浏览器原生输入框，避免 Flet Web 输入时白屏。保存后会回到当前步骤。</p>
    <form method="post">
      {"".join(field_html)}
      <div class="actions">
        <button type="submit">保存并返回 ELN</button>
        <a class="button secondary" href="{app_url}">取消</a>
      </div>
    </form>
  </main>
</body>
</html>
""")


@app.post("/web/edit-step/{step_id}", response_class=HTMLResponse)
async def web_edit_step_save(step_id: int, request: Request):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    form = await request.form()
    values = {
        field.key: str(form.get(field.key, ""))
        for field in step.get_fields()
    }
    db_ops.update_step(step_id, values_json=json.dumps(values, ensure_ascii=False))
    app_url = _eln_step_url(step.experiment_id, step_id)
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>记录已保存</title>
  <script>setTimeout(function() {{ window.location.replace("{app_url}"); }}, 500);</script>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    a {{ color: #fb8c00; }}
  </style>
</head>
<body>
  <p>记录已保存，正在返回 ELN。</p>
  <p><a href="{app_url}">如果没有自动返回，请点击这里</a></p>
</body>
</html>
""")


@app.post("/web/upload/{step_id}", response_class=HTMLResponse)
async def web_upload_photo(step_id: int, file: UploadFile = File(...)):
    result = await upload_photo(step_id=step_id, file=file)
    step = db_ops.get_step(step_id)
    if step:
        _write_eln_return_target(step.experiment_id, step_id)
    app_url = _eln_step_url(step.experiment_id, step_id) if step else _web_base_url()
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上传成功</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    img {{ max-width: min(520px, 100%); max-height: 420px; border: 1px solid #ddd; border-radius: 8px; }}
    a {{ color: #fb8c00; }}
    a.button {{ display: inline-block; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; text-decoration: none; margin-right: 10px; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
  </style>
</head>
<body>
  <h1>上传成功</h1>
  <p>照片已经保存到 ELN。回到实验页面点击“刷新照片”。</p>
  <p>
    <a class="button" href="{app_url}">返回 ELN</a>
    <a class="button secondary" href="/web/upload/{step_id}">继续上传另一张</a>
  </p>
  <img src="{result['url']}" alt="uploaded photo" />
</body>
</html>
""")


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _eln_step_url(experiment_id: int, step_id: int) -> str:
    return f"{_web_base_url()}/stepper/{experiment_id}/{step_id}"


def _web_base_url() -> str:
    configured = os.environ.get("ELN_WEB_PUBLIC_URL", "").rstrip("/")
    if configured:
        return configured
    try:
        from server.startup import get_local_ip
        return f"http://{get_local_ip()}:8550"
    except Exception:
        return "http://127.0.0.1:8550"


def _write_eln_return_target(experiment_id: int, step_id: int) -> None:
    try:
        import time
        path = os.path.join(os.path.expanduser("~"), "ELN_Data", "web_return.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "route": "stepper",
                    "experiment_id": experiment_id,
                    "step_id": step_id,
                    "created_at": time.time(),
                },
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _redirect_html(url: str, message: str = "正在返回") -> HTMLResponse:
    safe_url = _html_escape(url)
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_html_escape(message)}</title>
  <script>window.location.replace("{safe_url}");</script>
</head>
<body>
  <p>{_html_escape(message)}。</p>
  <p><a href="{safe_url}">如果没有自动跳转，请点击这里</a></p>
</body>
</html>
""")


@app.get("/run/storage/{exp_id}", response_class=HTMLResponse)
def run_storage_page(exp_id: int, msg: str = Query(""), error: str = Query("")):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    items = db_ops.get_storage_items(exp_id)
    boxes = db_ops.list_boxes()
    boxes_data = [_box_to_dict(b) for b in boxes]
    slots_data = {str(b.id): [_slot_to_dict(s) for s in db_ops.get_box_slots(b.id)] for b in boxes}

    item_cards = []
    for item in items:
        pos = item.position or "未登记"
        item_cards.append(f"""
        <div class="item">
          <div><b>{_html_escape(item.item_label)}</b></div>
          <div class="muted">管型：{_html_escape(item.tube_type or "未填写")} · 位置：{_html_escape(pos)}</div>
          <div class="muted">备注：{_html_escape(item.notes or item.notes_template or "无")}</div>
          <button type="button" onclick='prepareRegister({item.id}, {json.dumps(item.item_label, ensure_ascii=False)})'>登记 / 修改位置</button>
        </div>
        """)

    if not item_cards:
        item_cards.append('<p class="muted">还没有储存物品。可以在下面添加，每行一个。</p>')

    notice = ""
    if msg:
        notice = f'<div class="notice ok">{_html_escape(msg)}</div>'
    elif error:
        notice = f'<div class="notice error">{_html_escape(error)}</div>'

    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>储存登记 · {_html_escape(exp.name)}</title>
  <style>
    :root {{ --orange:#fb8c00; --green:#43a047; --line:#e8e8e8; --muted:#777; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f7f7f7; color:#222; }}
    header {{ position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); padding:12px 18px; display:flex; gap:12px; align-items:center; z-index:2; }}
    h1 {{ font-size:20px; margin:0; flex:1; }}
    main {{ max-width:1120px; margin:0 auto; padding:14px; display:grid; gap:14px; }}
    section, .item {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px; }}
    h2 {{ margin:0 0 12px; font-size:17px; }}
    button, a.button, input, select, textarea {{ font:inherit; }}
    button, a.button {{ border:0; border-radius:8px; background:var(--orange); color:white; padding:10px 14px; text-decoration:none; cursor:pointer; display:inline-block; }}
    button.secondary, a.secondary {{ background:#eee; color:#333; }}
    button.green {{ background:var(--green); }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:12px; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .notice {{ border-radius:10px; padding:12px 14px; font-weight:600; }}
    .notice.ok {{ background:#e9f7ec; color:#2e7d32; border:1px solid #b7dfbf; }}
    .notice.error {{ background:#fff0f0; color:#c62828; border:1px solid #ffc7c7; }}
    textarea, input, select {{ width:100%; border:1px solid #ddd; border-radius:8px; padding:10px; background:white; }}
    textarea {{ min-height:110px; resize:vertical; }}
    .grid-layout {{ display:grid; grid-template-columns:minmax(260px, 360px) 1fr; gap:14px; align-items:start; }}
    .item {{ margin-bottom:10px; }}
    .register-panel {{ display:none; }}
    .register-panel.open {{ display:block; }}
    .box-grid {{ display:grid; gap:4px; margin-top:12px; width:max-content; max-width:100%; overflow:auto; }}
    .slot {{ width:44px; height:36px; border:1px solid #ddd; border-radius:6px; background:#fafafa; color:#333; padding:0; font-size:12px; }}
    .slot.occupied {{ background:#fff2df; border-color:#ffc078; }}
    .slot.selected {{ background:var(--green); color:white; border-color:var(--green); }}
    @media (max-width:760px) {{ .grid-layout {{ grid-template-columns:1fr; }} .slot {{ width:38px; }} }}
  </style>
</head>
<body>
  <header>
    <a class="button secondary" href="/run?experiment_id={exp_id}">返回实验</a>
    <h1>储存登记 · {_html_escape(exp.name)}</h1>
    <a class="button secondary" href="/run/report/{exp_id}">查看报告</a>
  </header>
  <main>
    {notice}
    <section>
      <h2>添加要储存的物品</h2>
      <p class="muted">每行一个物品。推荐格式：样品名 | 管型 | 备注。也可以只写样品名。</p>
      <form method="post" action="/run/storage/{exp_id}/add">
        <textarea name="items" required placeholder="PCR 产物 Colony #1 | 1.5mL EP管 | 需要冻存"></textarea>
        <div class="actions"><button type="submit">添加物品</button></div>
      </form>
    </section>

    <section>
      <h2>Box</h2>
      <form method="post" action="/run/storage/{exp_id}/box/add" class="actions">
        <input name="box_name" placeholder="新 Box 名称" style="max-width:260px" />
        <select name="box_size" style="max-width:140px"><option value="10">10 × 10</option><option value="9">9 × 9</option></select>
        <button type="submit">新建 Box</button>
      </form>
    </section>

    <div class="grid-layout">
      <section>
        <h2>储存物品</h2>
        {"".join(item_cards)}
      </section>

      <section id="registerPanel" class="register-panel">
        <h2 id="registerTitle">登记位置</h2>
        <form method="post" action="/run/storage/{exp_id}/register">
          <input type="hidden" name="item_id" id="itemId" />
          <input type="hidden" name="position" id="position" />
          <label>选择 Box</label>
          <select name="box_id" id="boxSelect" onchange="renderGrid()"></select>
          <div id="boxGrid" class="box-grid"></div>
          <div class="muted" id="positionHint">请选择一个格子</div>
          <label>备注</label>
          <input name="notes" placeholder="可选" />
          <div class="actions">
            <button type="submit">保存位置</button>
            <button class="secondary" type="button" onclick="closeRegister()">取消</button>
          </div>
        </form>
      </section>
    </div>

    <section>
      <h2>结束与报告</h2>
      <p class="muted">补完照片和登记位置后，可以结束实验并进入报告页。以后也可以从历史记录查看。</p>
      <form method="post" action="/run/storage/{exp_id}/finish" onsubmit="return confirm('确认结束实验？')">
        <div class="actions">
          <button class="green" type="submit">结束实验并查看报告</button>
          <a class="button secondary" href="/run/report/{exp_id}">只查看报告</a>
        </div>
      </form>
    </section>
  </main>
  <script>
    const boxes = {json.dumps(boxes_data, ensure_ascii=False)};
    const slotsByBox = {json.dumps(slots_data, ensure_ascii=False)};
    let selectedPosition = "";
    function esc(v) {{ return String(v ?? "").replace(/[&<>"']/g, s => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[s])); }}
    function prepareRegister(itemId, label) {{
      document.getElementById("registerPanel").classList.add("open");
      document.getElementById("itemId").value = itemId;
      document.getElementById("registerTitle").textContent = "登记位置 · " + label;
      const sel = document.getElementById("boxSelect");
      sel.innerHTML = boxes.map(b => `<option value="${{b.id}}">${{esc(b.box_name)}} (${{b.box_size}}×${{b.box_size}})</option>`).join("");
      selectedPosition = "";
      document.getElementById("position").value = "";
      renderGrid();
    }}
    function closeRegister() {{
      document.getElementById("registerPanel").classList.remove("open");
    }}
    function renderGrid() {{
      const boxId = document.getElementById("boxSelect").value;
      const box = boxes.find(b => String(b.id) === String(boxId));
      const grid = document.getElementById("boxGrid");
      if(!box) {{ grid.innerHTML = '<p class="muted">请先新建 Box</p>'; return; }}
      const slots = slotsByBox[String(boxId)] || [];
      const byPos = Object.fromEntries(slots.map(s => [s.position, s]));
      grid.style.gridTemplateColumns = `repeat(${{box.box_size}}, 44px)`;
      grid.innerHTML = "";
      for(let r=0; r<box.box_size; r++) {{
        const row = String.fromCharCode(65 + r);
        for(let c=1; c<=box.box_size; c++) {{
          const pos = row + c;
          const slot = byPos[pos];
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "slot" + (slot ? " occupied" : "") + (pos === selectedPosition ? " selected" : "");
          btn.textContent = pos;
          btn.title = slot ? (slot.sample_name || "已占用") : "空位";
          btn.onclick = () => {{
            if(slot && !confirm(pos + " 已有内容：" + (slot.sample_name || "") + "。确认覆盖这个位置？")) return;
            selectedPosition = pos;
            document.getElementById("position").value = pos;
            document.getElementById("positionHint").textContent = "已选择：" + pos;
            renderGrid();
          }};
          grid.appendChild(btn);
        }}
      }}
    }}
  </script>
</body>
</html>
""")


@app.post("/run/storage/{exp_id}/add", response_class=HTMLResponse)
async def run_storage_add(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    raw = str(form.get("items", "")).strip()
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if parts else ""
        if not label:
            continue
        tube = parts[1] if len(parts) > 1 else ""
        notes = parts[2] if len(parts) > 2 else ""
        db_ops.create_storage_item(exp_id, item_label=label, tube_type=tube, notes_template=notes)
        added += 1
    if added <= 0:
        return RedirectResponse(
            f"/run/storage/{exp_id}?error={quote('没有输入可添加的储存物品')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/run/storage/{exp_id}?msg={quote(f'已添加 {added} 个储存物品')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/box/add", response_class=HTMLResponse)
async def run_storage_box_add(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    name = str(form.get("box_name", "")).strip()
    size = int(str(form.get("box_size", "10")) or "10")
    if name:
        db_ops.create_box(name, box_size=9 if size == 9 else 10)
        return RedirectResponse(
            f"/run/storage/{exp_id}?msg={quote('Box 已新建')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/run/storage/{exp_id}?error={quote('请输入 Box 名称')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/register", response_class=HTMLResponse)
async def run_storage_register(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    item_id = int(str(form.get("item_id", "0")) or "0")
    box_id = int(str(form.get("box_id", "0")) or "0")
    position = str(form.get("position", "")).strip().upper()
    if not item_id or not box_id or len(position) < 2:
        return RedirectResponse(
            f"/run/storage/{exp_id}?error={quote('登记信息不完整，请选择 Box 和位置')}",
            status_code=303,
        )
    notes = str(form.get("notes", "")).strip()
    db_ops.register_storage_item(
        item_id=item_id,
        box_id=box_id,
        row_label=position[0],
        col_label=position[1:],
        notes=notes,
        exp_id=exp_id,
    )
    return RedirectResponse(
        f"/run/storage/{exp_id}?msg={quote('位置已登记')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/finish", response_class=HTMLResponse)
async def run_storage_finish(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    db_ops.update_experiment(exp_id, status="completed")
    return _redirect_html(f"/run/report/{exp_id}", "实验已结束")


@app.get("/run/report/{exp_id}", response_class=HTMLResponse)
def run_report_page(exp_id: int, saved: str = Query("")):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    markdown = db_ops.get_report(exp_id)
    photo_html = []
    for step in db_ops.get_steps(exp_id):
        for i, path in enumerate(step.get_photo_paths(), 1):
            photo_html.append(f"""
            <figure>
              <img src="/photos/{_html_escape(path)}" alt="Step {step.step_index + 1} photo {i}" />
              <figcaption>Step {step.step_index + 1} · {_html_escape(step.title)} · 照片 {i}</figcaption>
            </figure>
            """)
    saved_block = f'<p class="saved">已保存：{_html_escape(saved)}</p>' if saved else ""
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>实验报告 · {_html_escape(exp.name)}</title>
  <style>
    body {{ margin:0; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f7f7f7; color:#222; }}
    header {{ position:sticky; top:0; background:#fff; border-bottom:1px solid #e8e8e8; padding:12px 18px; display:flex; gap:12px; align-items:center; z-index:2; }}
    h1 {{ font-size:20px; margin:0; flex:1; }}
    main {{ max-width:980px; margin:0 auto; padding:14px; }}
    section {{ background:#fff; border:1px solid #e8e8e8; border-radius:10px; padding:14px; margin-bottom:14px; }}
    button, a.button {{ border:0; border-radius:8px; background:#fb8c00; color:white; padding:10px 14px; text-decoration:none; cursor:pointer; font:inherit; }}
    a.secondary {{ background:#eee; color:#333; }}
    pre {{ white-space:pre-wrap; word-break:break-word; line-height:1.5; }}
    img {{ max-width:min(100%, 640px); border:1px solid #ddd; border-radius:8px; }}
    figure {{ margin:14px 0; }}
    figcaption {{ color:#666; font-size:13px; margin-top:5px; }}
    .saved {{ color:#2e7d32; }}
  </style>
</head>
<body>
  <header>
    <a class="button secondary" href="/run?experiment_id={exp_id}">返回实验</a>
    <h1>实验报告 · {_html_escape(exp.name)}</h1>
    <form method="post" action="/run/report/{exp_id}/save"><button type="submit">保存报告</button></form>
  </header>
  <main>
    {saved_block}
    <section>
      <h2>照片预览</h2>
      {"".join(photo_html) if photo_html else '<p>暂无照片。</p>'}
    </section>
    <section>
      <h2>Markdown 报告</h2>
      <pre>{_html_escape(markdown)}</pre>
    </section>
  </main>
</body>
</html>
""")


@app.post("/run/report/{exp_id}/save", response_class=HTMLResponse)
async def run_report_save(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    result = db_ops.save_report(exp_id)
    return _redirect_html(f"/run/report/{exp_id}?saved={quote(result['path'])}", "报告已保存")


# ─────────────────────────────────────────────
# Protocols
# ─────────────────────────────────────────────

@app.get("/api/protocols")
def list_protocols():
    protocols = db_ops.list_protocols()
    return [_protocol_to_dict(p) for p in protocols]


@app.post("/api/protocols", status_code=201)
def create_protocol(body: ProtocolCreate):
    try:
        definition = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    p = db_ops.create_protocol(definition)
    return _protocol_to_dict(p)


@app.get("/api/protocols/{protocol_id}")
def get_protocol(protocol_id: int):
    p = db_ops.get_protocol(protocol_id)
    if not p:
        raise HTTPException(404, "Protocol not found")
    return _protocol_to_dict(p)


@app.put("/api/protocols/{protocol_id}")
def update_protocol(protocol_id: int, body: ProtocolCreate):
    if not db_ops.get_protocol(protocol_id):
        raise HTTPException(404, "Protocol not found")
    try:
        definition = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    p = db_ops.update_protocol(protocol_id, definition)
    return _protocol_to_dict(p)


@app.delete("/api/protocols/{protocol_id}", status_code=204)
def delete_protocol(protocol_id: int):
    if not db_ops.get_protocol(protocol_id):
        raise HTTPException(404, "Protocol not found")
    db_ops.delete_protocol(protocol_id)


def _protocol_to_dict(p) -> dict:
    return {
        "id": p.id, "name": p.name, "version": p.version, "author": p.author,
        "protocol_json": p.protocol_json,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "use_count": p.use_count, "last_used_at": p.last_used_at,
    }


# ─────────────────────────────────────────────
# Boxes
# ─────────────────────────────────────────────

@app.get("/api/boxes")
def list_boxes():
    boxes = db_ops.list_boxes()
    result = []
    for b in boxes:
        used = db_ops.get_box_slot_count(b.id)
        result.append({**_box_to_dict(b), "used_slots": used,
                        "total_slots": b.box_size * b.box_size})
    return result


@app.post("/api/boxes", status_code=201)
def create_box(body: BoxCreate):
    b = db_ops.create_box(body.box_name, body.box_size, body.notes)
    return _box_to_dict(b)


@app.get("/api/boxes/{box_id}")
def get_box(box_id: int):
    b = db_ops.get_box(box_id)
    if not b:
        raise HTTPException(404, "Box not found")
    used = db_ops.get_box_slot_count(box_id)
    return {**_box_to_dict(b), "used_slots": used, "total_slots": b.box_size * b.box_size}


@app.patch("/api/boxes/{box_id}")
def update_box(box_id: int, body: BoxUpdate):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    updates = body.model_dump(exclude_none=True)
    b = db_ops.update_box(box_id, **updates)
    return _box_to_dict(b)


@app.delete("/api/boxes/{box_id}", status_code=204)
def delete_box(box_id: int):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    db_ops.delete_box(box_id)


@app.get("/api/boxes/{box_id}/slots")
def get_slots(box_id: int):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    slots = db_ops.get_box_slots(box_id)
    return [_slot_to_dict(s) for s in slots]


@app.put("/api/boxes/{box_id}/slots/{position}")
def upsert_slot(box_id: int, position: str, body: SlotUpdate):
    """position format: 'A1', 'B3', etc."""
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    if len(position) < 2:
        raise HTTPException(400, "Invalid position format (e.g. 'A1')")
    row_label = position[0].upper()
    col_label = position[1:]
    slot = db_ops.upsert_slot(
        box_id=box_id, row_label=row_label, col_label=col_label,
        sample_name=body.sample_name, notes=body.notes,
        experiment_id=body.experiment_id, step_id=body.step_id,
    )
    return _slot_to_dict(slot)


@app.delete("/api/boxes/{box_id}/slots/{position}", status_code=204)
def clear_slot(box_id: int, position: str):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    row_label = position[0].upper()
    col_label = position[1:]
    db_ops.clear_slot(box_id, row_label, col_label)


def _box_to_dict(b) -> dict:
    return {"id": b.id, "box_name": b.box_name, "box_size": b.box_size,
            "created_at": b.created_at, "notes": b.notes}


def _slot_to_dict(s) -> dict:
    return {
        "id": s.id, "box_id": s.box_id,
        "row_label": s.row_label, "col_label": s.col_label,
        "position": s.position,
        "sample_name": s.sample_name, "notes": s.notes,
        "experiment_id": s.experiment_id, "step_id": s.step_id,
        "created_at": s.created_at,
    }


# ─────────────────────────────────────────────
# Storage items
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/storage")
def get_storage(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    items = db_ops.get_storage_items(exp_id)
    return [_storage_to_dict(i) for i in items]


@app.post("/api/experiments/{exp_id}/storage")
def create_storage(exp_id: int, body: StorageCreate):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    if not body.item_label.strip():
        raise HTTPException(400, "item_label is required")
    item = db_ops.create_storage_item(
        experiment_id=exp_id,
        item_label=body.item_label.strip(),
        tube_type=body.tube_type.strip(),
        notes_template=body.notes_template.strip(),
        default_box=body.default_box.strip(),
    )
    return _storage_to_dict(item)


@app.post("/api/experiments/{exp_id}/storage/register")
def register_storage(exp_id: int, body: StorageRegister):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    item = db_ops.register_storage_item(
        item_id=body.item_id, box_id=body.box_id,
        row_label=body.row_label, col_label=body.col_label,
        notes=body.notes,
    )
    if not item:
        raise HTTPException(404, "Storage item not found")
    # Also write to box_slots
    db_ops.upsert_slot(
        box_id=body.box_id, row_label=body.row_label, col_label=body.col_label,
        sample_name=item.item_label, notes=body.notes,
        experiment_id=exp_id,
    )
    return _storage_to_dict(item)


def _storage_to_dict(i) -> dict:
    return {
        "id": i.id, "experiment_id": i.experiment_id,
        "item_key": i.item_key, "item_label": i.item_label,
        "tube_type": i.tube_type, "notes_template": i.notes_template,
        "default_box": i.default_box,
        "box_id": i.box_id, "row_label": i.row_label, "col_label": i.col_label,
        "position": i.position, "is_registered": i.is_registered,
        "notes": i.notes, "registered_at": i.registered_at,
    }


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/report")
def get_report(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_steps(exp_id)
    storage_items = db_ops.get_storage_items(exp_id)
    boxes = {b.id: b for b in db_ops.list_boxes()}
    md = generate_report(exp, steps, storage_items, boxes, db_ops.list_timer_events(exp_id))
    return {"experiment_id": exp_id, "markdown": md}


@app.post("/api/experiments/{exp_id}/report/save")
def save_report(exp_id: int):
    try:
        return db_ops.save_report(exp_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
