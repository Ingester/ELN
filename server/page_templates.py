"""Large embedded page templates (HTML/CSS/JS) extracted from api.py.
These are plain strings with __PLACEHOLDER__ tokens filled at request
time by the route functions in api.py."""

_CAPTURE_BODY = """
<body>
  <header class="app-bar">
    <h1>速记</h1>
    <a class="button secondary" href="/inbox" id="inboxLink">__I_INBOX__ 收件箱<span id="inboxCount"></span></a>
  </header>
  <main>
    <section class="cap-card">
      <div class="field exp-pick">
        <label>属于哪个实验（可不选，交给 AI 判断）</label>
        <select id="expPick"><option value="">让 AI 判断属于哪个实验</option></select>
      </div>
      <textarea id="capText" placeholder="刚做了什么、看到了什么？直接打字，或点下面的话筒说出来。"></textarea>
      <div id="micState"></div>
      <div class="thumbs" id="thumbs"></div>
      <div class="cap-tools">
        <button id="capMic" class="secondary" onclick="toggleCapMic()">__I_MIC__<span id="capMicLabel">说</span></button>
        <label class="button secondary" for="capCam">__I_CAM__ 拍照</label>
        <label class="button secondary" for="capGal">__I_IMG__ 相册</label>
        <input id="capCam" type="file" accept="image/*" capture="environment" multiple onchange="addImages(this)" />
        <input id="capGal" type="file" accept="image/*" multiple onchange="addImages(this)" />
      </div>
      <div class="archive-row">
        <button class="green" id="archiveBtn" onclick="archive()">__I_ARCH__ 打包存档</button>
      </div>
      <div class="small" id="capHint" style="margin-top:8px"></div>
    </section>

    <div class="pending-head">
      <h2>待归档</h2>
      <a class="edit-link" href="/inbox">全部 · 历史 __I_ARR__</a>
    </div>
    <div id="pendingList"></div>
  </main>
__NAV__
<script>
__ICON_JS__
const heldImages = [];   // {file, kind, status, progress, staged}
const heldFiles = [];    // {file, kind, status, progress, staged}
const heldAudio = { blob: null };
const capVoice = { rec: null, recognizing: false, mr: null, chunks: [] };
const uploadState = { active: false };

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
async function api(path, opts={}){
  const res = await fetch(path, {headers:{"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

async function loadExperiments(){
  try {
    const exps = await api("/api/experiment_summaries");
    const sel = document.getElementById("expPick");
    for(const e of exps){
      const o = document.createElement("option");
      o.value = e.id; o.textContent = e.name + " · " + e.completed_steps + "/" + e.total_steps;
      sel.appendChild(o);
    }
  } catch {}
}

function renderThumbs(){
  const box = document.getElementById("thumbs");
  const x = svgIcon("x", 13);
  const chipState = item => {
    if(item.status === "uploading") return `上传中 ${Math.max(1, Math.round(item.progress || 0))}%`;
    if(item.status === "done") return "已上传";
    if(item.status === "error") return "失败：" + (item.error || "上传失败");
    return formatBytes(item.file?.size || 0);
  };
  const progressBar = item => item.status === "uploading"
    ? `<div class="bar"><span style="width:${Math.max(1, Math.round(item.progress || 0))}%"></span></div>`
    : "";
  const imageHtml = heldImages.map((item, i) =>
    `<span class="thumb" title="${esc(item.file.name || "图片")}"><img src="${item.preview || URL.createObjectURL(item.file)}" /><button class="rm" onclick="rmImage(${i})">${x}</button></span>`
  ).join("");
  const fileHtml = heldFiles.map((item, i) =>
    `<span class="file-chip ${item.status === "error" ? "error" : ""}" title="${esc(item.file.name || "未命名文件")}">${svgIcon("clipboard",18)}<span class="file-main"><span class="fn">${esc(item.file.name || "未命名文件")}</span><span class="file-state">${esc(chipState(item))}</span>${progressBar(item)}</span><button class="rm" onclick="rmFile(${i})">${x}</button></span>`
  ).join("");
  const audioHtml = heldAudio.blob ? `<span class="thumb" style="display:flex;align-items:center;justify-content:center;color:var(--muted)">${svgIcon("audio",24)}<button class="rm" onclick="rmAudio()">${x}</button></span>` : "";
  box.innerHTML = imageHtml + fileHtml + audioHtml;
}
function addImages(input){
  for(const f of input.files) addHeldFile(f);
  input.value = "";
  renderThumbs();
}
function isStagedAttachment(value){
  return !!(value && typeof value === "object" && typeof value.rel_path === "string" && value.rel_path);
}
function addHeldFile(file, staged=null){
  if(!file) return;
  staged = isStagedAttachment(staged) ? staged : null;
  const item = {
    file,
    kind: (file.type || "").startsWith("image/") ? "image" : "file",
    status: staged ? "done" : "queued",
    progress: staged ? 100 : 0,
    staged,
    error: "",
    preview: (file.type || "").startsWith("image/") ? URL.createObjectURL(file) : "",
  };
  if(item.kind === "image") heldImages.push(item);
  else heldFiles.push(item);
}
function rmImage(i){ heldImages.splice(i,1); renderThumbs(); }
function rmFile(i){ heldFiles.splice(i,1); renderThumbs(); }
function rmAudio(){ heldAudio.blob = null; renderThumbs(); }
function formatBytes(n){
  n = Number(n || 0);
  if(n < 1024) return n + " B";
  const units = ["KB","MB","GB","TB"];
  let v = n / 1024, i = 0;
  while(v >= 1024 && i < units.length - 1){ v /= 1024; i++; }
  return `${v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
}
function uploadStagedAttachment(item){
  if(isStagedAttachment(item.staged)) return Promise.resolve(item.staged);
  item.staged = null;
  if((item.file?.size || 0) > 32 * 1024 * 1024) return uploadStagedAttachmentChunked(item);
  item.status = "uploading"; item.progress = 0; item.error = ""; renderThumbs();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    fd.append("file", item.file);
    fd.append("kind", item.kind);
    xhr.open("POST", "/api/inbox/staged-media");
    xhr.upload.onprogress = ev => {
      if(ev.lengthComputable){
        item.progress = Math.min(99, (ev.loaded / ev.total) * 100);
        scheduleThumbRender();
        document.getElementById("capHint").textContent = `正在上传 ${item.file.name || "文件"}：${Math.round(item.progress)}%`;
      }
    };
    xhr.onload = () => {
      if(xhr.status >= 200 && xhr.status < 300){
        try {
          item.staged = JSON.parse(xhr.responseText);
          item.status = "done"; item.progress = 100; renderThumbs();
          resolve(item.staged);
        } catch(e){
          item.status = "error"; item.error = "响应异常"; renderThumbs(); reject(e);
        }
      } else {
        item.status = "error"; item.error = xhr.responseText || `HTTP ${xhr.status}`; renderThumbs();
        reject(new Error(item.error));
      }
    };
    xhr.onerror = () => {
      item.status = "error"; item.error = "网络中断"; renderThumbs();
      reject(new Error(item.error));
    };
    xhr.send(fd);
  });
}
async function uploadStagedAttachmentChunked(item){
  if(isStagedAttachment(item.staged)) return item.staged;
  item.staged = null;
  const localPage = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
  const chunkSize = (localPage ? 128 : 32) * 1024 * 1024;
  const total = Math.ceil(item.file.size / chunkSize);
  const uploadId = item.uploadId || (crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : String(Date.now()) + Math.random().toString(16).slice(2));
  item.uploadId = uploadId;
  item.status = "uploading"; item.progress = 0; item.error = ""; renderThumbs();
  for(let index = 0; index < total; index++){
    const start = index * chunkSize;
    const end = Math.min(item.file.size, start + chunkSize);
    const chunk = item.file.slice(start, end);
    const result = await uploadOneChunk(item, uploadId, index, total, start, chunk);
    if(result){
      item.staged = result;
      item.status = "done"; item.progress = 100; renderThumbs();
      return result;
    }
  }
  throw new Error("分片上传未完成");
}
function uploadOneChunk(item, uploadId, index, total, offset, chunk){
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const qs = new URLSearchParams({
      upload_id: uploadId,
      index: String(index),
      total: String(total),
      offset: String(offset),
      total_size: String(item.file.size),
      filename: item.file.name || "attachment.bin",
      kind: item.kind,
    });
    xhr.open("POST", "/api/inbox/staged-chunk-raw?" + qs.toString());
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = ev => {
      if(ev.lengthComputable){
        item.progress = Math.min(99, ((offset + ev.loaded) / item.file.size) * 100);
        scheduleThumbRender();
        document.getElementById("capHint").textContent = `正在上传 ${item.file.name || "文件"}：${Math.round(item.progress)}%`;
      }
    };
    xhr.onload = () => {
      if(xhr.status >= 200 && xhr.status < 300){
        try {
          const payload = JSON.parse(xhr.responseText || "{}");
          resolve(payload.done ? payload : null);
        } catch(e){ reject(e); }
      } else {
        item.status = "error"; item.error = xhr.responseText || `HTTP ${xhr.status}`; renderThumbs();
        reject(new Error(item.error));
      }
    };
    xhr.onerror = () => {
      item.status = "error"; item.error = "网络中断"; renderThumbs();
      reject(new Error(item.error));
    };
    xhr.send(chunk);
  });
}
async function uploadAllAttachments(){
  const all = [...heldImages, ...heldFiles];
  const staged = new Array(all.length);
  let cursor = 0;
  async function worker(){
    while(cursor < all.length){
      const index = cursor++;
      staged[index] = await uploadStagedAttachment(all[index]);
    }
  }
  await Promise.all(Array.from({length: Math.min(2, all.length)}, worker));
  return staged;
}

let thumbRenderTimer = 0;
function scheduleThumbRender(){
  if(thumbRenderTimer) return;
  thumbRenderTimer = setTimeout(() => {
    thumbRenderTimer = 0;
    renderThumbs();
  }, 180);
}

async function stageLocalClipboardFiles(files){
  const localPage = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
  if(!localPage || !files.some(file => file.size > 32 * 1024 * 1024)) return [];
  try {
    const response = await api("/api/inbox/stage-local-clipboard", {
      method: "POST",
      body: JSON.stringify({files: files.map(file => ({
        name: file.name || "attachment.bin",
        size: Number(file.size || 0),
        last_modified: Number(file.lastModified || 0),
        kind: (file.type || "").startsWith("image/") ? "image" : "file",
      }))}),
    });
    return Array.isArray(response.items) ? response.items : [];
  } catch(_err){
    return [];
  }
}

async function handleCapPaste(event){
  const ta = document.getElementById("capText");
  if(document.activeElement !== ta) return;
  const files = Array.from(event.clipboardData?.files || []);
  if(!files.length){
    const items = Array.from(event.clipboardData?.items || []);
    for(const item of items){
      if(item.kind === "file"){
        const f = item.getAsFile();
        if(f) files.push(f);
      }
    }
  }
  if(!files.length) return;
  event.preventDefault();
  const localItems = await stageLocalClipboardFiles(files);
  files.forEach((file, index) => addHeldFile(file, localItems[index]?.staged || null));
  renderThumbs();
  const imageCount = files.filter(f => (f.type || "").startsWith("image/")).length;
  const fileCount = files.length - imageCount;
  const parts = [];
  if(imageCount) parts.push(`${imageCount} 张图片`);
  if(fileCount) parts.push(`${fileCount} 个文件`);
  document.getElementById("capHint").textContent = `已从剪贴板加入 ${parts.join("、")}，打包存档时会一起上传。`;
}

function handleCapDragOver(event){
  const dt = event.dataTransfer;
  if(!dt || !Array.from(dt.types || []).includes("Files")) return;
  event.preventDefault();
  document.querySelector(".cap-card")?.classList.add("drop-on");
}

function handleCapDragLeave(event){
  const card = document.querySelector(".cap-card");
  if(card && !card.contains(event.relatedTarget)) card.classList.remove("drop-on");
}

function handleCapDrop(event){
  const files = Array.from(event.dataTransfer?.files || []);
  if(!files.length) return;
  event.preventDefault();
  document.querySelector(".cap-card")?.classList.remove("drop-on");
  files.forEach(file => addHeldFile(file));
  renderThumbs();
  const imageCount = files.filter(f => (f.type || "").startsWith("image/")).length;
  const fileCount = files.length - imageCount;
  const parts = [];
  if(imageCount) parts.push(`${imageCount} 张图片`);
  if(fileCount) parts.push(`${fileCount} 个文件`);
  document.getElementById("capHint").textContent = `已加入 ${parts.join("、")}，打包存档时会一起上传。`;
}

function speechSupported(){ return !!(window.SpeechRecognition || window.webkitSpeechRecognition); }

function toggleCapMic(){
  if(capVoice.recognizing || capVoice.mr){ stopCapMic(); return; }
  startCapRecord();
}
function setMicUI(on){
  const b = document.getElementById("capMic");
  b.classList.toggle("rec", on);
  const lbl = document.getElementById("capMicLabel");
  if(lbl) lbl.textContent = on ? "停" : "说";
}
function startCapSpeech(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR(); rec.lang="zh-CN"; rec.continuous=true; rec.interimResults=true;
  const live = document.getElementById("micState");
  rec.onresult = e => {
    let interim="";
    for(let i=e.resultIndex;i<e.results.length;i++){
      const r=e.results[i];
      if(r.isFinal){ const t=r[0].transcript.trim(); if(t){ const ta=document.getElementById("capText"); ta.value=(ta.value?ta.value+" ":"")+t; } }
      else interim+=r[0].transcript;
    }
    live.textContent = interim;
  };
  rec.onend = () => { if(capVoice.recognizing){ try{rec.start();}catch{ capVoice.recognizing=false; setMicUI(false);} } };
  rec.onerror = ev => { live.textContent=""; if(ev.error==="not-allowed"){ capVoice.recognizing=false; capVoice.rec=null; setMicUI(false); document.getElementById("capHint").textContent="麦克风被拒绝，可改用键盘听写。"; } };
  capVoice.rec=rec; capVoice.recognizing=true; setMicUI(true);
  try{ rec.start(); }catch{}
}
async function startCapRecord(){
  if(!(navigator.mediaDevices && window.MediaRecorder)){ document.getElementById("capHint").textContent="此环境不支持录音，请打字。"; return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = ["audio/mp4;codecs=mp4a.40.2","audio/mp4","audio/webm;codecs=opus","audio/webm"];
    const mimeType = preferred.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));
    const mr = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream); capVoice.chunks=[];
    mr.ondataavailable = e => { if(e.data && e.data.size) capVoice.chunks.push(e.data); };
    mr.onstop = () => { stream.getTracks().forEach(t=>t.stop()); const type=mr.mimeType||mimeType||"audio/mp4"; const blob=new Blob(capVoice.chunks,{type}); capVoice.chunks=[]; if(blob.size>0){ heldAudio.blob=blob; renderThumbs(); document.getElementById("capHint").textContent="已录一段语音，存档后会自动转写。"; } };
    capVoice.mr=mr; mr.start(); setMicUI(true);
  } catch(e){ document.getElementById("capHint").textContent="无法录音："+e.message; }
}
function stopCapMic(){
  if(capVoice.rec){ capVoice.recognizing=false; try{capVoice.rec.stop();}catch{} capVoice.rec=null; }
  if(capVoice.mr){ try{capVoice.mr.stop();}catch{} capVoice.mr=null; }
  document.getElementById("micState").textContent="";
  setMicUI(false);
}

async function archive(){
  stopCapMic();
  const text = document.getElementById("capText").value.trim();
  if(!text && !heldImages.length && !heldFiles.length && !heldAudio.blob){ document.getElementById("capHint").textContent="先说点什么、拍张照，粘贴文件，或打段字。"; return; }
  const btn = document.getElementById("archiveBtn");
  btn.disabled = true; document.getElementById("capHint").textContent = "准备上传…";
  uploadState.active = true;
  try {
    const hint = document.getElementById("expPick").value;
    const attachments = await uploadAllAttachments();
    document.getElementById("capHint").textContent = "写入速记…";
    const entry = await api("/api/inbox", {method:"POST", body: JSON.stringify({text, hinted_experiment_id: hint ? Number(hint) : null, attachments})});
    if(heldAudio.blob){
      const fd = new FormData();
      const ext = (heldAudio.blob.type||"").includes("mp4") ? ".m4a" : ".webm";
      fd.append("file", new File([heldAudio.blob], "voice"+ext, {type:heldAudio.blob.type}));
      fd.append("kind", "audio");
      const audioRes = await fetch(`/api/inbox/${entry.id}/media`, {method:"POST", body:fd});
      if(!audioRes.ok) throw new Error(await audioRes.text());
    }
    document.getElementById("capText").value = "";
    heldImages.length = 0; heldFiles.length = 0; heldAudio.blob = null; renderThumbs();
    document.getElementById("capHint").textContent = "已存进收件箱";
    setTimeout(()=>{ document.getElementById("capHint").textContent=""; }, 2500);
    loadPending();
  } catch(e){
    document.getElementById("capHint").textContent = "存档失败："+(e.message||e);
  } finally { uploadState.active = false; btn.disabled = false; }
}

async function loadPending(){
  try {
    const items = await api("/api/inbox?status=pending");
    const c = document.getElementById("inboxCount");
    if(c) c.textContent = items.length ? (" " + items.length) : "";
    const box = document.getElementById("pendingList");
    if(!items.length){ box.innerHTML = '<div class="small" style="padding:0 2px">还没有待归档的速记。</div>'; return; }
    box.innerHTML = items.map(it => {
      const firstFile = it.file_urls && it.file_urls[0];
      const thumb = it.image_urls && it.image_urls[0]
        ? `<span class="thumb edit-thumb" onclick="openPendingEdit(${it.id})" role="button" title="修改识别文字" aria-label="修改识别文字"><img src="${esc(thumbFromUrl(it.image_urls[0],96))}" loading="lazy" onerror="this.onerror=null;this.src='${esc(it.image_urls[0])}'"></span>`
        : `<span class="thumb ph edit-thumb" onclick="openPendingEdit(${it.id})" role="button" title="修改识别文字" aria-label="修改识别文字">${svgIcon(firstFile ? "clipboard" : (it.audio_url && !it.text ? "audio" : "note"), 20)}</span>`;
      const t = new Date(it.created_at);
      const stamp = `${t.getMonth()+1}/${t.getDate()} ` + String(t.getHours()).padStart(2,"0")+":"+String(t.getMinutes()).padStart(2,"0");
      const fileText = firstFile ? `文件：${esc(firstFile.name || "未命名文件")}` : "";
      const placeholder = it.audio_url ? "语音待识别或未识别到文字" : (fileText || "图片");
      const body = it.text ? esc(it.text) : `<span class="ph-text">${placeholder}</span>`;
      const fileLinks = (it.file_urls || []).map(f => `<a href="${esc(f.url)}" target="_blank" rel="noopener">${svgIcon("clipboard",14)} ${esc(f.name || "文件")}</a>`).join(" ");
      return `<div class="pending-item" id="pending-${it.id}">
        ${thumb}
        <div class="pt">
          <div class="pending-text" id="pending-text-${it.id}" data-raw="${esc(it.text || "")}" onclick="startPendingEdit(${it.id})" title="点击修改">${body}</div>
          ${fileLinks ? `<div class="pm">${fileLinks}</div>` : ""}
          <div class="pm">${stamp}${it.hinted_experiment_id?" · 已标实验":""}</div>
        </div>
      </div>`;
    }).join("");
  } catch {}
}

// Click the note text to edit it right there (Markdown-friendly, no popup box).
function openPendingEdit(id){ startPendingEdit(id); }
function startPendingEdit(id){
  const node = document.getElementById("pending-text-"+id);
  if(!node || node.dataset.editing) return;
  node.dataset.editing = "1";
  node.onclick = null;
  const raw = node.getAttribute("data-raw") || "";
  node.innerHTML = `<textarea class="pending-inline" id="pending-raw-${id}" placeholder="用 Markdown 记录…">${esc(raw)}</textarea>
    <div class="pending-edit-row">
      <button class="green" onclick="savePendingText(${id})">保存</button>
      <button class="secondary" onclick="loadPending()">取消</button>
      <span class="small" id="pending-status-${id}"></span>
    </div>`;
  const ta = document.getElementById("pending-raw-"+id);
  if(ta){ ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; }
}
async function savePendingText(id){
  const ta = document.getElementById("pending-raw-"+id);
  if(!ta) return;
  const st = document.getElementById("pending-status-"+id);
  try {
    if(st) st.textContent = "保存中…";
    await api(`/api/inbox/${id}`, {method:"PATCH", body: JSON.stringify({text: ta.value})});
    loadPending();
  } catch(e){
    if(st) st.textContent = "保存失败：" + (e.message || e);
  }
}

loadExperiments();
document.getElementById("capText").addEventListener("paste", handleCapPaste);
const capCard = document.querySelector(".cap-card");
if(capCard){
  capCard.addEventListener("dragover", handleCapDragOver);
  capCard.addEventListener("dragleave", handleCapDragLeave);
  capCard.addEventListener("drop", handleCapDrop);
}
window.addEventListener("beforeunload", event => {
  if(!uploadState.active) return;
  event.preventDefault();
  event.returnValue = "";
});
document.addEventListener("click", event => {
  if(!uploadState.active) return;
  const link = event.target.closest && event.target.closest("a[href]");
  if(!link) return;
  event.preventDefault();
  document.getElementById("capHint").textContent = "文件还在上传，完成前不要切换页面。";
});
loadPending();
// refresh so background transcription text shows up; skip while editing a note
setInterval(() => { if(!document.querySelector(".pending-edit.open")) loadPending(); }, 12000);
</script>
</body>
</html>
"""


_INBOX_BODY = """
<body>
  <header class="app-bar">
    <a class="button secondary" href="/more">__I_BACK__ 更多</a>
    <h1>速记收件箱</h1>
    <button class="icon-btn" onclick="loadAll()" title="刷新" aria-label="刷新">__I_REFRESH__</button>
  </header>
  <main>
    <div class="ai-bar">
      <span class="t">这里回看历史速记、听录音、看照片。归档交给你开着的 Claude Code / Codex：它先把计划列给你看，你确认后直接写进实验（走订阅，不花 token）。</span>
      <button onclick="toggleAiPanel()">__I_SPARK__ AI 归档指令</button>
    </div>
    <div class="ai-panel" id="aiPanel">
      <div class="small" style="margin-bottom:8px">复制下面这段，粘贴进你的 Claude Code / Codex 对话里发送。它会读速记、看图片，先把「哪条写到哪个实验哪一步」的完整计划发给你，等你确认后才直接写入。（可直接在框里改）</div>
      <textarea id="aiPrompt" spellcheck="false"></textarea>
      <div class="actions" style="margin-top:10px">
        <button class="green" onclick="copyPrompt()">复制指令</button>
        <button class="secondary" onclick="resetPrompt()">恢复默认</button>
        <button class="secondary" onclick="toggleAiPanel()">收起</button>
        <span class="small" id="copyHint"></span>
      </div>
    </div>
    <div class="chips" id="chips">
      <button class="chip active" data-f="pending" onclick="setFilter('pending')">待归档</button>
      <button class="chip" data-f="filed" onclick="setFilter('filed')">已归档</button>
      <button class="chip" data-f="all" onclick="setFilter('all')">全部</button>
    </div>
    <div id="entries"></div>
  </main>
__NAV__
<script>
__ICON_JS__
const AI_PROMPT = `帮我把 ELN 速记收件箱归档进实验记录。本地接口 http://127.0.0.1:8600（本机免密）。

重要：先把完整计划列给我看，等我明确说“确认”后，你再真正写入。别未经确认就写。

一、读取
1. GET /api/inbox?status=pending —— 待归档速记（id、text、image_urls、audio_url、hinted_experiment_id、created_at）。有图片就打开 image_urls 看清内容；只有 audio_url 而 text 为空，说明语音还没转写，提醒我别瞎猜。
2. GET /api/experiment_summaries —— 现有实验（id、name、进度）。对可能相关的实验 GET /api/experiments/{id}/steps 看每一步：id、step_index、title、description、fields（每个 key/label/type/options）、values（当前已填值）。

二、整理并把计划发给我（先别写）
注意：速记文字多半是语音输入转写来的，可能有同音字、错字、断句混乱。请结合我之前的速记和现有实验的上下文（步骤名、试剂、术语、进度）推断我到底在说什么，别被识别错字带偏；拿不准的地方在计划里标出来问我，别硬猜也别编造。
逐条速记：把关键信息提炼出来（去掉口语的重复啰嗦，忠实原意、不要编造），判断它该写到哪个实验、哪一步、哪些字段。数值只有我明确说了才填，带单位只填数字。
- 一条速记可以拆开写到多个步骤/字段。
- 那一步没有合适的字段时，可以给这步新增一个字段来装，或写进该步备注。
- 明显不属于任何现有实验的，提议新建实验（给出实验名和步骤结构，参考仓库里的 ELN_Protocol_Format.md / protocol_templates/）。
整理成一份清单：每条速记 → 目标实验/步骤 → 要写入的值 / 要新增的字段 / 要新建的实验。发给我，然后停下等我确认。

三、我确认后再写（用颗粒接口直接写；写前记下旧值，方便我让你撤销）
- 写某步：GET /api/steps/{step_id} 拿当前 values 和 fields → 把要写的值并进去 → PATCH /api/steps/{step_id}，body {"values_json":"<整个 values 的 JSON 字符串>"}。要加字段就同时传 "fields_json"（在原 fields 数组后追加 {"key","label","type"，需要时"options"}），再把值填进 values 对应 key。
- 长段观察、解释、异常、AI 总结不要塞进数字/短文本字段；写进 values["__eln_step_notes"]，内容可以用 Markdown。若旧值已有内容，就在后面追加新段落，不要覆盖。
- 新建实验：POST /api/experiments，body {"name":"...","protocol_json":"<ProtocolDefinition 的 JSON 字符串>"}，建好后按上面往它的步骤写。
- 每条写完：POST /api/inbox/{id}/filed，body {"experiment_id":3,"step_id":12,"summary":"一句话说写了什么"} —— 把它移出待办并留档。

四、全部写完，逐条告诉我写到了哪、加了什么字段、建了什么实验。哪条放错了我会让你改或撤销。`;
const PROMPT_KEY = "eln.aiPrompt";
function toggleAiPanel(){
  const p = document.getElementById("aiPanel");
  const open = !p.classList.contains("open");
  p.classList.toggle("open", open);
  const ta = document.getElementById("aiPrompt");
  if(open && ta && !ta.value){ ta.value = localStorage.getItem(PROMPT_KEY) || AI_PROMPT; }
}
function resetPrompt(){
  const ta = document.getElementById("aiPrompt");
  if(ta){ ta.value = AI_PROMPT; localStorage.removeItem(PROMPT_KEY); }
}
async function copyPrompt(){
  const h = document.getElementById("copyHint");
  const ta = document.getElementById("aiPrompt");
  const text = (ta && ta.value) || AI_PROMPT;
  localStorage.setItem(PROMPT_KEY, text);   // remember your edits
  try { await navigator.clipboard.writeText(text); h.textContent = "已复制，去粘贴给 AI"; h.style.color = "var(--pos)"; }
  catch { if(ta){ ta.focus(); ta.select(); } h.textContent = "已选中，按 Ctrl+C 复制"; }
}
let filter = "pending";

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
async function api(path, opts={}){
  const res = await fetch(path, {headers:{"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

function setFilter(f){
  filter = f;
  for(const b of document.querySelectorAll("#chips .chip")){ b.classList.toggle("active", b.dataset.f === f); }
  loadAll();
}

async function loadAll(){
  let items = [];
  try { items = await api("/api/inbox?status=" + encodeURIComponent(filter)); } catch {}
  const root = document.getElementById("entries");
  if(!items.length){
    const msg = filter === "pending" ? "没有待归档的速记，都处理好了。"
              : filter === "filed" ? "还没有已归档的速记。" : "还没有速记。";
    root.innerHTML = '<div class="empty">' + msg + '</div>';
    return;
  }
  root.innerHTML = "";
  for(const it of items){ root.appendChild(renderEntry(it)); }
}

function statusBadge(s){
  if(s === "filed") return '<span class="badge filed">已归档</span>';
  if(s === "dismissed") return '<span class="badge dismissed">已忽略</span>';
  return '<span class="badge pending">待归档</span>';
}

function renderEntry(it){
  const el = document.createElement("div");
  el.className = "entry"; el.id = "entry-"+it.id;
  const t = new Date(it.created_at);
  const stamp = t.toLocaleString();
  const media = (it.image_urls||[]).map(u => `<a href="${esc(u)}" target="_blank"><img src="${esc(thumbFromUrl(u,220))}" loading="lazy" onerror="this.onerror=null;this.src='${esc(u)}'"></a>`).join("");
  const files = (it.file_urls||[]).map(f => `<a class="efile" href="${esc(f.url)}" target="_blank" rel="noopener">${svgIcon("clipboard",14)} ${esc(f.name||"文件")}</a>`).join("");
  const audio = it.audio_url ? `<audio controls preload="none" src="${esc(it.audio_url)}"></audio>` : "";
  const bodyText = it.text ? esc(it.text) : (it.audio_url ? "<i>语音，尚未转写</i>" : "<i>（无文字）</i>");

  let filedTo = "";
  if(it.status === "filed"){
    const p = it.proposal || {};
    const where = it.filed_experiment_id
      ? `实验#${it.filed_experiment_id}${it.filed_step_id?(" · 步骤#"+it.filed_step_id):""}` : "";
    const sum = p.summary ? esc(p.summary) : "";
    if(where || sum){
      filedTo = `<div class="filed-to"><span class="lbl">${svgIcon("check",14)}已写入</span>`
        + (where?` ${where}`:"") + (sum?`<div class="rs">${sum}</div>`:"") + `</div>`;
    }
  }

  el.innerHTML = `
    <div class="etime">${stamp} · ${statusBadge(it.status)}${it.hinted_experiment_id?" · 已标实验":""}</div>
    <div class="etext">${bodyText}</div>
    <div class="emedia">${media}</div>
    ${files ? `<div class="efiles">${files}</div>` : ""}
    ${audio}
    ${filedTo}
    <details class="raw-edit">
      <summary>修正识别文字</summary>
      <textarea id="raw-${it.id}" placeholder="识别文字">${esc(it.text||"")}</textarea>
      <button class="secondary" onclick="saveEntryText(${it.id})">保存</button>
      <span class="small" id="es-${it.id}"></span>
    </details>
    <div class="entry-actions">
      <button class="danger-ghost" onclick="deleteEntry(${it.id})">${svgIcon("trash",16)}删除</button>
    </div>`;
  return el;
}

async function saveEntryText(id){
  const st = document.getElementById("es-"+id);
  try {
    const edited = document.getElementById("raw-"+id).value;
    st.textContent = "保存中…";
    await api(`/api/inbox/${id}`, {method:"PATCH", body: JSON.stringify({text: edited})});
    st.textContent = "已保存";
  } catch(e){
    st.textContent = "保存失败："+(e.message||e);
  }
}
async function deleteEntry(id){
  if(!confirm("删除这条速记？")) return;
  await api(`/api/inbox/${id}`, {method:"DELETE"});
  loadAll();
}

loadAll();
// periodic refresh, but never rebuild the list while audio is playing (it would
// destroy the <audio> element and stop playback) or while editing a transcript
setInterval(() => {
  const playing = Array.from(document.querySelectorAll("#entries audio")).some(a => !a.paused && !a.ended && a.currentTime > 0);
  if(playing) return;
  if(document.querySelector("#entries details[open]")) return;
  loadAll();
}, 15000);
</script>
</body>
</html>
"""


_RUNNER_BODY = """
<body>
  <header class="app-bar">
    <button class="icon-btn" id="boardBackBtn" onclick="showBoard()" title="返回看板" aria-label="返回看板" style="display:none">__I_BACK__</button>
    <div class="exp-wrap" id="expWrap">
      <select id="experimentSelect" onchange="selectExperiment(this.value)" aria-label="选择实验"></select>
    </div>
    <span id="boardTitle">实验看板</span>
    <span id="net" class="status">连接中</span>
    <button class="icon-btn" id="micBtn" onclick="openVoicePanel()" title="语音速记" aria-label="语音速记">__I_MIC__</button>
    <button class="icon-btn" onclick="refreshCurrent()" title="刷新" aria-label="刷新">__I_REFRESH__</button>
  </header>
  <main>
    <div id="boardAlarms" class="board-alarms"></div>
    <div id="board"></div>
    <div id="queueInfo" class="queue-info"></div>
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

  <div id="voiceBackdrop" class="sheet-backdrop" onclick="closeVoicePanel()"></div>
  <div id="voiceSheet" class="sheet">
    <div class="grab"></div>
    <h2>语音速记</h2>
    <div class="small" id="voiceHint"></div>
    <div id="voiceLive"></div>
    <textarea id="voiceText" placeholder="说完的内容出现在这里，可以先修改再保存"></textarea>
    <div class="voice-controls">
      <button id="voiceRecBtn" onclick="toggleVoiceRec()">开始说话</button>
      <button class="green" onclick="saveVoiceText()">存入当前步骤</button>
    </div>
    <div class="voice-controls" style="margin-top:8px">
      <button class="secondary" onclick="runAiOrganize()">__I_SPARK__ AI 整理全部速记</button>
    </div>
    <div class="small" id="aiHint" style="margin-top:4px"></div>
    <div class="voice-all">
      <div class="section-head" style="margin-top:0"><h2>本实验全部速记</h2></div>
      <div id="voiceAllList" class="voice-list"></div>
    </div>
  </div>

  <div id="aiBackdrop" class="sheet-backdrop" onclick="closeAiPanel()"></div>
  <div id="aiSheet" class="sheet">
    <div class="grab"></div>
    <h2>AI 整理草稿</h2>
    <div class="small" id="aiDraftHint">AI 已把你的口语整理成下面的草稿。确认无误再写入记录，数字类字段请核对。</div>
    <div id="aiDraftBody"></div>
    <div class="voice-controls">
      <button class="green" onclick="applyAllAi()">全部写入记录</button>
      <button class="secondary" onclick="closeAiPanel()">关闭</button>
    </div>
  </div>

<script>
__ICON_JS__
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
let focusStepIdParam = new URLSearchParams(window.location.search).get("step_id") || "";
let steps = [];
let experiments = [];
let voiceNotes = [];
const STEP_NOTES_KEY = "__eln_step_notes";
const timerSync = {};
const timerLastSync = {};
const initializedStepPosition = {};
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
function renderQueueInfo(){ const n = getQueue().length; document.getElementById("queueInfo").textContent = n ? ("待同步：" + n + " 项") : ""; }

async function api(path, opts={}){
  const res = await fetch(path, {headers: {"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

async function loadExperiments(){
  try {
    const active = await api("/api/experiments?status=active");
    const wrap = await api("/api/experiments?status=needs_wrapup");
    experiments = [...active, ...wrap];
    localStorage.setItem(LS.experiments, JSON.stringify(experiments));
    renderExperiments(experiments);
    net("已连接", true);
  } catch(e) {
    net("离线缓存", false);
    experiments = JSON.parse(localStorage.getItem(LS.experiments) || "[]");
    renderExperiments(experiments);
  }
  return experiments;
}

let view = "board";
const STATUS_LABEL = {active:"进行中", needs_wrapup:"待收尾", completed:"已完成", abandoned:"已放弃", archived:"已归档"};

function setHeaderMode(v){
  view = v;
  const board = v === "board";
  document.getElementById("boardBackBtn").style.display = board ? "none" : "inline-flex";
  document.getElementById("expWrap").style.display = board ? "none" : "block";
  document.getElementById("boardTitle").style.display = board ? "block" : "none";
  document.getElementById("micBtn").style.display = board ? "none" : "inline-flex";
  document.getElementById("board").style.display = board ? "block" : "none";
  document.getElementById("steps").style.display = board ? "none" : "block";
  document.getElementById("queueInfo").style.display = board ? "none" : "block";
  const dock = document.getElementById("elnDock");   // floating timer dock: only inside an experiment
  if(dock) dock.style.display = board ? "none" : "flex";
  const ba = document.getElementById("boardAlarms"); // alarms/timers countdown shown on the board
  if(ba) ba.style.display = board ? "flex" : "none";
  if(board) renderBoardAlarms();
}

// Show active dock timers/alarms (localStorage) as live countdowns on the board.
function renderBoardAlarms(){
  const el = document.getElementById("boardAlarms");
  if(!el || view !== "board") return;
  let list = [];
  try { list = JSON.parse(localStorage.getItem("eln.quicktimers") || "[]"); } catch {}
  if(!list.length){ el.style.display = "none"; el.innerHTML = ""; return; }
  const now = Date.now();
  el.style.display = "flex";
  el.innerHTML = list.map(q => {
    const remain = Math.round((q.endAt - now) / 1000);
    const over = remain <= 0;
    const icon = svgIcon(q.kind === "alarm" ? "clock" : "timer", 14);
    return `<span class="board-alarm ${over?"over":""}">${icon}<b>${(over?"+":"")+fmtHMS(Math.abs(remain))}</b><span class="ba-label">${esc(q.label || (q.kind==="alarm"?"闹钟":"计时"))}</span></span>`;
  }).join("");
}

async function showBoard(){
  setHeaderMode("board");
  renderBoard(experiments);
  await loadBoardTimers();
  boardTimerKeys = Object.keys(boardTimers).sort().join(",");
  if(view === "board") renderBoard(experiments);
}

function renderBoard(exps){
  const root = document.getElementById("board");
  if(!exps || !exps.length){
    root.innerHTML = '<div class="board-empty">还没有进行中的实验。<br><a href="/protocols">去协议库新建实验 →</a></div>';
    return;
  }
  root.innerHTML = exps.map(e => {
    const total = e.total_steps || 0, done = e.completed_steps || 0;
    const pct = total ? Math.round(done / total * 100) : 0;
    const label = STATUS_LABEL[e.status] || e.status || "";
    const timerChip = boardTimers[String(e.id)]
      ? `<span class="bc-timer" id="bctimer-${e.id}">${svgIcon("timer",12)}<span class="tv"></span></span>` : "";
    return `<div class="board-card" onclick="enterExperiment('${e.id}')">
      <div class="bc-top"><span class="bc-name">${esc(e.name)}</span><span class="bc-badge s-${esc(e.status)}">${esc(label)}</span></div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <div class="bc-foot">
        <div class="bc-foot-l"><span class="bc-meta">#${esc(e.id)} · ${done}/${total} 步 · ${pct}%</span>${timerChip}</div>
        <button class="bc-abandon" onclick="event.stopPropagation(); abandonExperiment('${e.id}', ${esc(JSON.stringify(e.name))})">放弃</button>
      </div>
    </div>`;
  }).join("");
  tickBoardTimers();
}

let boardTimers = {};
let boardTimerKeys = "";

// like fmt() but shows hours for long timers: 16:00:00, otherwise MM:SS
function fmtHMS(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? (h + ":" + mm + ":" + ss) : (mm + ":" + ss);
}

async function loadBoardTimers(){
  try {
    const list = await api("/api/timers/active");
    const now = Date.now();
    const map = {};
    for(const t of list){
      const updated = parseServerTime(t.updated_at);
      const rec = {
        status: t.status,
        endAt: t.status === "running" ? updated + (t.remaining_seconds || 0) * 1000 : null,
        overBase: t.overtime_seconds || 0,
        overSince: updated,
      };
      const key = String(t.experiment_id);
      const cur = map[key];
      // prefer a running timer (soonest end) over an overtime one
      if(!cur || (rec.status === "running" && (cur.status !== "running" || rec.endAt < cur.endAt))){
        map[key] = rec;
      }
    }
    boardTimers = map;
  } catch {}
}

function tickBoardTimers(){
  if(view !== "board") return;
  renderBoardAlarms();
  const now = Date.now();
  for(const key in boardTimers){
    const el = document.getElementById("bctimer-" + key);
    if(!el) continue;
    const t = boardTimers[key];
    let over = false, secs = 0;
    if(t.status === "running" && t.endAt){ secs = Math.round((t.endAt - now) / 1000); if(secs <= 0){ over = true; secs = -secs; } }
    else { over = true; secs = t.overBase + Math.round((now - t.overSince) / 1000); }
    el.classList.toggle("over", over);
    const tv = el.querySelector(".tv");
    if(tv) tv.textContent = (over ? "+" : "") + fmtHMS(secs);
  }
}

async function pollBoardTimers(){
  if(view !== "board") return;
  await loadBoardTimers();
  const keys = Object.keys(boardTimers).sort().join(",");
  if(keys !== boardTimerKeys){ boardTimerKeys = keys; if(view === "board") renderBoard(experiments); }
  else { tickBoardTimers(); }
}

async function enterExperiment(id){
  selectedExperiment = String(id);
  localStorage.setItem(LS.selected, selectedExperiment);
  initializedStepPosition[id] = false;
  if(!experiments.find(e => String(e.id) === String(id))){
    try { const full = await api(`/api/experiments/${id}`); experiments.push(full); } catch {}
  }
  renderExperiments(experiments);
  setHeaderMode("exp");
  await loadSteps(selectedExperiment);
}

async function abandonExperiment(id, name){
  if(!confirm("放弃实验「" + name + "」？它会移出看板，可在 历史 里找到。")) return;
  try {
    await api(`/api/experiments/${id}`, {method:"PATCH", body: JSON.stringify({status:"abandoned"})});
    experiments = experiments.filter(e => String(e.id) !== String(id));
    await loadExperiments();
    showBoard();
  } catch(e){ alert("放弃失败：" + (e.message || e)); }
}

async function refreshCurrent(){
  await loadExperiments();
  if(view === "board"){ showBoard(); }
  else if(selectedExperiment){ await loadSteps(selectedExperiment); }
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
  initializedStepPosition[id] = false;
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
  await restoreTimersFromServer(expId);
  await loadVoiceNotes();
  ensureInitialStepPosition(expId);
  if(focusStepIdParam){
    const fi = steps.findIndex(s => String(s.id) === String(focusStepIdParam));
    if(fi >= 0) setCurrentStepIndex(fi);
    focusStepIdParam = "";
  }
  renderSteps(steps);
}

function mergedValues(step){
  let vals = {...(step.values || {})};
  try { vals = {...vals, ...JSON.parse(localStorage.getItem(draftKey(step.id)) || "{}")}; } catch {}
  return vals;
}

function normalizedFields(step){
  const used = new Set();
  return (step.fields || []).map((field, index) => {
    let base = String(field.key || "").trim();
    if(!base){
      base = String(field.label || "")
        .toLowerCase()
        .replace(/µ/g, "u")
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "");
    }
    if(!base) base = `field_${index + 1}`;
    let key = base;
    let suffix = 2;
    while(used.has(key)) key = `${base}_${suffix++}`;
    used.add(key);
    return {...field, key};
  });
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
function timerHours(seconds){ return String(Math.floor((seconds || 0) / 3600)); }
function timerMins(seconds){ return String(Math.round(((seconds || 0) % 3600) / 60)); }

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

function ensureInitialStepPosition(expId){
  if(initializedStepPosition[expId]) return;
  if(!steps.length){ initializedStepPosition[expId] = true; return; }
  const open = steps.findIndex(s => !s.completed_at);
  const idx = open >= 0 ? open : steps.length - 1;
  localStorage.setItem(stepIndexKey(expId), String(idx));
  initializedStepPosition[expId] = true;
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

function renderAttachments(step, attachments){
  return attachments.map(item => {
    const url = attachmentUrl(item.path);
    const renameButton = `<button type="button" class="attachment-rename"
      title="修改附件名称" aria-label="修改 ${esc(item.name)} 的名称"
      data-step-id="${step.id}" data-path="${esc(item.path)}" data-name="${esc(item.name)}"
      onclick="renameAttachment(this)">${svgIcon("pencil",15)}</button>`;
    if(isImageAttachment(item.path)){
      const disp = displayUrl(item.path);   // full-size (original or PNG preview) for opening
      const thumb = thumbUrl(item.path, 400);
      return `<span class="attachment-item image">
        <a class="attachment-preview" href="${esc(disp)}" target="_blank" rel="noopener" title="打开原图">
          <img src="${esc(thumb)}" alt="${esc(item.name)}" loading="lazy" onerror="this.onerror=null;this.src='${esc(disp)}'" />
        </a>
        <span class="attachment-caption">
          <a href="${esc(url)}" target="_blank" rel="noopener" title="${esc(item.name)}（下载原图）">${esc(item.name)}</a>
          ${renameButton}
        </span>
      </span>`;
    }
    return `<span class="attachment-item file">
      <a href="${esc(url)}" target="_blank" rel="noopener">${esc(item.name)}</a>
      ${renameButton}
    </span>`;
  }).join("");
}

function attachmentUrl(path){
  const clean = String(path || "").replace(/\\\\/g, "/").replace(/^\\/+/, "");
  return "/photos/" + clean.split("/").map(encodeURIComponent).join("/");
}

function needsConvert(path){ return /\\.(tiff?|bmp)$/i.test(String(path || "")); }

// Small cached thumbnail for previews (any raster image incl. TIFF). SVG renders
// natively and is tiny, so use it as-is.
function thumbUrl(path, w){
  const clean = String(path || "").replace(/\\\\/g, "/").replace(/^\\/+/, "");
  if(/\\.svg$/i.test(clean)) return attachmentUrl(path);
  return "/api/thumb?path=" + encodeURIComponent(clean) + "&w=" + (w||360);
}

// URL to show in <img>: browsers can't render TIFF/BMP, so use the server PNG preview.
function displayUrl(path){
  if(!needsConvert(path)) return attachmentUrl(path);
  const clean = String(path || "").replace(/\\\\/g, "/").replace(/^\\/+/, "");
  return "/api/preview?path=" + encodeURIComponent(clean);
}

function isImageAttachment(path){
  return /\\.(jpe?g|png|gif|webp|bmp|tiff?|svg)$/i.test(String(path || ""));
}

function renameAttachment(button){
  const stepId = Number(button.dataset.stepId);
  const attachmentPath = button.dataset.path || "";
  const currentName = button.dataset.name || "";
  openModal(
    "修改附件名称",
    `<div class="field"><label>附件名称</label><input id="editAttachmentName" value="${esc(currentName)}" maxlength="240" /></div>`,
    async () => {
      const name = document.getElementById("editAttachmentName").value.trim();
      if(!name) throw new Error("附件名称不能为空");
      const updated = await api(`/api/steps/${stepId}/attachments/name`, {
        method:"PATCH",
        body:JSON.stringify({path:attachmentPath, name})
      });
      setLocalStep(stepId, {attachments:updated.attachments, photo_paths:updated.photo_paths});
      renderSteps(steps);
    }
  );
}

function jumpStep(i){ setCurrentStepIndex(i); renderSteps(steps); }

function renderSteps(items){
  const root = document.getElementById("steps");
  root.innerHTML = "";
  if(!items.length){ root.innerHTML = '<div class="card small">暂无缓存步骤。联网后点右上角刷新按钮。</div>'; return; }
  steps = items;
  const idx = currentStepIndex();
  const step = items[idx];
  const doneCount = items.filter(s => s.completed_at).length;
  const pct = Math.round((doneCount / items.length) * 100);
  const vals = mergedValues(step);
  const totalSeconds = mergedTimerSeconds(step);
  const isLast = idx >= items.length - 1;
  const card = document.createElement("article");
  card.className = "card";
  const chips = items.map((s, i) =>
    `<button class="chip ${s.completed_at ? "done" : ""} ${i === idx ? "cur" : ""}" onclick="jumpStep(${i})" title="${esc(s.title)}">${i + 1}</button>`
  ).join("");
  const fields = normalizedFields(step).map(f => {
    const v = vals[f.key] ?? f.default ?? "";
    if(f.type === "dropdown"){
      const opts = (f.options || []).map(o => `<option value="${esc(o)}" ${o==v?"selected":""}>${esc(o)}</option>`).join("");
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><select data-step="${step.id}" data-key="${esc(f.key)}" onchange="saveDraft(${step.id})">${opts}</select></div>`;
    }
    if(f.type === "number"){
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><input type="number" data-step="${step.id}" data-key="${esc(f.key)}" value="${esc(v)}" oninput="saveDraft(${step.id})" /></div>`;
    }
    const hasValue = String(v).trim().length > 0;
    const keyArg = JSON.stringify(f.key);
    return `<div class="field field-md ${hasValue ? "has-value" : ""}" data-field-step="${step.id}" data-field-key="${esc(f.key)}">
      <label>${esc(f.label)}${f.required ? " *" : ""}</label>
      <div class="field-preview ${hasValue ? "" : "empty"}" onclick='openFieldEdit(${step.id}, ${keyArg})'>${hasValue ? markdownToHtml(v) : "点击填写"}</div>
      <textarea data-step="${step.id}" data-key="${esc(f.key)}" oninput='saveDraft(${step.id}); updateFieldPreview(${step.id}, ${keyArg})' onblur='closeFieldEdit(${step.id}, ${keyArg})' placeholder="${esc(f.label)}">${esc(v)}</textarea>
    </div>`;
  }).join("");
  const notesValue = vals[STEP_NOTES_KEY] || "";
  const hasMdNote = String(notesValue).trim().length > 0;
  const notesBlock = `
    <div class="field notes md-slot ${hasMdNote ? "has-md" : ""}" id="md-slot-${step.id}">
      <div class="md-line">
        <button type="button" class="md-chip" onclick="toggleMdSlot(${step.id})" title="输入或修改 Markdown 记录">md</button>
      </div>
      <div class="md-preview ${hasMdNote ? "" : "empty"}" id="md-preview-${step.id}">${hasMdNote ? markdownToHtml(notesValue) : ""}</div>
      <div class="md-box" id="md-box-${step.id}">
        <textarea data-step="${step.id}" data-key="${STEP_NOTES_KEY}" oninput="saveDraft(${step.id}); updateMdSlot(${step.id})" placeholder="Markdown 记录；报告中会按 Markdown 渲染">${esc(notesValue)}</textarea>
        <div class="small">支持 Markdown。适合放观察、异常、解释、AI 整理结果；数字和短字段仍填上面的结构化字段。</div>
      </div>
    </div>`;
  const attachments = step.attachments || (step.photo_paths || []).map((p,i) => ({path:p, name:`附件 ${i+1}`}));
  const photos = renderAttachments(step, attachments);
  const timerBlock = totalSeconds > 0 ? `
    <div class="timer" id="timer-box-${step.id}">
      <div class="small">步骤计时 · 电脑端负责响铃</div>
      <div class="timer-display" id="timer-display-${step.id}">${fmtHMS(totalSeconds)}</div>
      <div class="field timer-edit">
        <input type="number" min="0" step="1" id="th-${step.id}" value="${timerHours(totalSeconds)}" onchange="saveTimerHM(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""} />
        <span class="small">时</span>
        <input type="number" min="0" step="1" id="tm-${step.id}" value="${timerMins(totalSeconds)}" onchange="saveTimerHM(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""} />
        <span class="small">分</span>
      </div>
      <div class="actions">
        <button onclick="startLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})" ${step.completed_at ? "disabled" : ""}>开始</button>
        <button class="secondary" onclick="pauseLocalTimer(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""}>暂停</button>
        <button class="secondary" onclick="resetLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})" ${step.completed_at ? "disabled" : ""}>重置</button>
      </div>
      <div class="status" id="timer-status-${step.id}"></div>
    </div>` : "";
  const voiceBlock = renderStepVoice(step);
  const photoBlock = `
    <div class="field">
      <label>附件 / 拍照记录</label>
      <div class="photos">${photos || '<span class="small">暂无附件</span>'}</div>
      <form class="photo-row" onsubmit="uploadPhoto(event, ${step.id})">
        <div class="pr-btns">
          <label class="button secondary" for="cam-${step.id}">${svgIcon("camera",16)} 拍照</label>
          <label class="button secondary" for="gal-${step.id}">${svgIcon("image",16)} 相册</label>
          <label class="button secondary" for="any-${step.id}">文件</label>
          <button type="button" class="secondary" onclick="pasteClipboard(${step.id})">剪贴板</button>
        </div>
        <input id="cam-${step.id}" name="file" type="file" accept="image/*" capture="environment" onchange="markFile(this)" />
        <input id="gal-${step.id}" name="file2" type="file" accept="image/*" onchange="markFile(this)" />
        <input id="any-${step.id}" name="file3" type="file" onchange="markFile(this)" />
        <input id="name-${step.id}" name="attachment_name" type="text" placeholder="附件名称（默认原文件名）" />
        <div class="pr-submit">
          <button type="submit" class="secondary">${svgIcon("upload",16)} 上传</button>
          <span class="small" id="file-${step.id}">未选择</span>
        </div>
      </form>
    </div>`;
  const wrapupBlock = isLast ? `
    <div class="wrapup">
      <div class="section-head" style="margin-top:0">
        <h2>实验收尾</h2>
      </div>
      <div class="small">最后一步完成后，补充储存物品、登记 Box 位置、查看和保存报告。</div>
      <div class="actions">
        <a class="button" href="/run/storage/${selectedExperiment}">储存物品 / 登记位置</a>
        <a class="button secondary" href="/run/report/${selectedExperiment}">查看报告</a>
        <button class="secondary" onclick="finishExperiment()">结束实验</button>
      </div>
    </div>` : "";
  card.innerHTML = `
    <div class="chips">${chips}</div>
    <div class="stepper">
      <button class="secondary" onclick="goStep(-1)" ${idx === 0 ? "disabled" : ""}>← 上一步</button>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <button class="secondary" onclick="goStep(1)" ${idx >= items.length - 1 ? "disabled" : ""}>下一步 →</button>
    </div>
    <div class="section-head" style="margin-top:4px">
      <div class="small">#${esc(selectedExperiment)} · Step ${idx + 1} / ${items.length} · 已完成 ${doneCount}/${items.length}${step.completed_at ? ' · <span class="done">本步已完成 ✓</span>' : ''}</div>
      <button class="edit-link" onclick="editExperimentName()">改实验名</button>
    </div>
    <div class="section-head" style="margin-top:2px">
      <div class="step-title">${esc(step.title)}</div>
      <button class="edit-link" onclick="editStepText(${step.id}, 'title', '修改步骤标题')">${svgIcon("pencil",15)}</button>
    </div>
    <div class="section-head">
      <h2>步骤说明</h2>
      <button class="edit-link" onclick="editStepText(${step.id}, 'description', '修改步骤说明')">编辑</button>
    </div>
    <div class="desc">${renderDescription(step)}</div>
    ${timerBlock}
    <div class="section-head">
      <h2>记录数据</h2>
      <button class="edit-link" onclick="editFields(${step.id})">编辑字段</button>
    </div>
    ${fields}
    ${notesBlock}
    ${voiceBlock}
    ${photoBlock}
    ${wrapupBlock}
    <div class="main-actions">
      <button class="secondary" onclick="saveAndSync(${step.id})">保存</button>
      <button class="green" onclick="completeStep(${step.id})" ${step.completed_at ? "disabled" : ""}>完成步骤 ✓</button>
      <span class="status" id="status-${step.id}"></span>
    </div>`;
  root.appendChild(card);
  refreshTimers();
}

function collectValues(stepId){
  const vals = {};
  document.querySelectorAll(`[data-step="${stepId}"]`).forEach(el => {
    const key = String(el.dataset.key || "").trim();
    if(key) vals[key] = el.value || "";
  });
  return vals;
}
function saveDraft(stepId){ localStorage.setItem(draftKey(stepId), JSON.stringify(collectValues(stepId))); }
function status(stepId, text){ const el=document.getElementById("status-"+stepId); if(el) el.textContent=text; }

function fieldSlot(stepId, key){
  return Array.from(document.querySelectorAll(`[data-field-step="${stepId}"]`))
    .find(el => el.dataset.fieldKey === String(key)) || null;
}

function fieldTextarea(stepId, key){
  const slot = fieldSlot(stepId, key);
  return slot ? slot.querySelector("textarea") : null;
}

function openFieldEdit(stepId, key){
  const slot = fieldSlot(stepId, key);
  const ta = fieldTextarea(stepId, key);
  if(!slot || !ta) return;
  slot.classList.add("editing");
  setTimeout(() => {
    ta.focus();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }, 20);
}

function closeFieldEdit(stepId, key){
  updateFieldPreview(stepId, key);
  const slot = fieldSlot(stepId, key);
  if(slot) slot.classList.remove("editing");
}

function updateFieldPreview(stepId, key){
  const slot = fieldSlot(stepId, key);
  const ta = fieldTextarea(stepId, key);
  const preview = slot ? slot.querySelector(".field-preview") : null;
  if(!slot || !ta || !preview) return;
  const hasText = !!ta.value.trim();
  slot.classList.toggle("has-value", hasText);
  preview.classList.toggle("empty", !hasText);
  preview.innerHTML = hasText ? markdownToHtml(ta.value) : "点击填写";
}

function mdTextarea(stepId){
  return document.querySelector(`textarea[data-step="${stepId}"][data-key="${STEP_NOTES_KEY}"]`);
}

function toggleMdSlot(stepId){
  const slot = document.getElementById("md-slot-" + stepId);
  const ta = mdTextarea(stepId);
  if(!slot) return;
  slot.classList.toggle("editing");
  if(slot.classList.contains("editing") && ta){
    setTimeout(() => {
      ta.focus();
      ta.selectionStart = ta.selectionEnd = ta.value.length;
    }, 30);
  } else {
    updateMdSlot(stepId);
  }
}

function updateMdSlot(stepId){
  const ta = mdTextarea(stepId);
  const slot = document.getElementById("md-slot-" + stepId);
  const preview = document.getElementById("md-preview-" + stepId);
  const hasText = !!(ta && ta.value.trim());
  if(slot) slot.classList.toggle("has-md", hasText);
  if(preview){
    preview.classList.toggle("empty", !hasText);
    preview.innerHTML = hasText ? markdownToHtml(ta.value) : "";
  }
}

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
  applyTimerSeconds(expId, stepId, Math.max(0, Math.round(value * 60)));
}
function saveTimerHM(expId, stepId){
  const h = parseFloat(document.getElementById("th-" + stepId).value) || 0;
  const m = parseFloat(document.getElementById("tm-" + stepId).value) || 0;
  if(h < 0 || m < 0){ status(stepId, "计时请输入有效的时和分"); return; }
  applyTimerSeconds(expId, stepId, Math.max(0, Math.round(h * 3600 + m * 60)));
}
function applyTimerSeconds(expId, stepId, seconds){
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

function parseServerTime(value){
  const t = Date.parse(value || "");
  return Number.isFinite(t) ? t : Date.now();
}

function serverTimerToLocal(record){
  const total = Math.max(0, parseInt(record.total_seconds || 0, 10) || 0);
  const remainingAtServer = Math.max(0, parseInt(record.remaining_seconds || 0, 10) || 0);
  const overtimeAtServer = Math.max(0, parseInt(record.overtime_seconds || 0, 10) || 0);
  const updatedAt = parseServerTime(record.updated_at);
  const elapsed = Math.max(0, Math.floor((Date.now() - updatedAt) / 1000));
  if(record.status === "running"){
    if(elapsed >= remainingAtServer){
      return {
        status:"overtime",
        total,
        remaining:0,
        pausedRemaining:0,
        overtime:overtimeAtServer + elapsed - remainingAtServer,
        startedAt:null,
        updatedAt:Date.now()
      };
    }
    const remaining = remainingAtServer - elapsed;
    return {
      status:"running",
      total,
      remaining,
      pausedRemaining:remaining,
      startedAt:Date.now(),
      updatedAt:Date.now()
    };
  }
  if(record.status === "overtime"){
    return {
      status:"overtime",
      total,
      remaining:0,
      pausedRemaining:0,
      overtime:overtimeAtServer + elapsed,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  if(record.status === "paused"){
    return {
      status:"paused",
      total,
      remaining:remainingAtServer,
      pausedRemaining:remainingAtServer,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  if(record.status === "confirmed"){
    return {
      status:"confirmed",
      total,
      remaining:remainingAtServer,
      pausedRemaining:remainingAtServer,
      overtime:overtimeAtServer,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  return {status:"idle", total, remaining:total, pausedRemaining:total, startedAt:null, updatedAt:Date.now()};
}

async function restoreTimersFromServer(expId){
  try {
    const active = await api(`/api/timers/experiment/${expId}`);
    const timers = getTimers();
    for(const record of active){
      timers[record.step_id] = serverTimerToLocal(record);
      timerLastSync[record.step_id] = Date.now();
    }
    setTimers(timers);
  } catch(e) {
    // 离线时继续使用本机缓存。
  }
}

function timerState(stepId, total){
  const timers = getTimers();
  const t = timers[stepId];
  if(!t) return {status:"idle", total, remaining:total, startedAt:null, pausedRemaining:total, updatedAt:Date.now()};
  if(t.status === "running"){
    const elapsed = Math.max(0, Math.floor((Date.now() - (t.startedAt || Date.now())) / 1000));
    const remaining = (t.pausedRemaining ?? t.remaining ?? total) - elapsed;
    if(remaining <= 0){
      return {
        ...t,
        status:"overtime",
        remaining:0,
        pausedRemaining:0,
        overtime:Math.abs(remaining),
        updatedAt:Date.now()
      };
    }
    return {...t, remaining, pausedRemaining:remaining};
  }
  if(t.status === "overtime"){
    const elapsed = Math.max(0, Math.floor((Date.now() - (t.updatedAt || Date.now())) / 1000));
    return {
      ...t,
      remaining:0,
      pausedRemaining:0,
      overtime:Math.max(0, Math.floor(t.overtime || 0)) + elapsed
    };
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
    const overtimeSeconds = Math.max(0, Math.floor(state.overtime ?? (state.remaining < 0 ? Math.abs(state.remaining) : 0) ?? 0));
    const payload = {
      total_seconds: state.total,
      remaining_seconds: state.status === "overtime" ? 0 : Math.max(0, Math.floor(state.remaining ?? state.pausedRemaining ?? state.total)),
      overtime_seconds: overtimeSeconds,
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
  timers[stepId] = {status:"running", total, pausedRemaining:remaining, remaining, startedAt:Date.now(), updatedAt:Date.now()};
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
  timers[stepId] = {status:"paused", total:current.total, pausedRemaining:remaining, remaining, startedAt:null, updatedAt:Date.now()};
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
  timers[stepId] = {status:"idle", total, pausedRemaining:total, remaining:total, startedAt:null, updatedAt:Date.now()};
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
    if(current.status === "overtime" || (current.status === "running" && current.remaining <= 0)){
      const overtime = Math.max(0, Math.floor(current.overtime ?? Math.abs(current.remaining || 0)));
      display.textContent = "+" + fmtHMS(overtime);
      box.classList.add("over");
      if(text) text.textContent = "时间到。电脑端会响铃；当前页面同步显示。";
      if(timers[step.id]?.status !== "overtime"){
        timers[step.id] = {
          ...(timers[step.id] || {}),
          status:"overtime",
          total:current.total,
          remaining:0,
          pausedRemaining:0,
          overtime,
          startedAt:null,
          updatedAt:Date.now()
        };
        setTimers(timers);
      } else if(Math.abs((timers[step.id]?.overtime || 0) - overtime) > 5){
        timers[step.id] = {...timers[step.id], overtime, updatedAt:Date.now()};
        setTimers(timers);
      }
      if(!timers[step.id]?.alerted){
        try { navigator.vibrate && navigator.vibrate([300,120,300,120,600]); } catch {}
        timers[step.id] = {...(timers[step.id] || {}), alerted:true};
        setTimers(timers);
      }
    } else {
      display.textContent = fmtHMS(current.remaining ?? totalSeconds);
      box.classList.remove("over");
      if(text) text.textContent = current.status === "running"
        ? "计时中"
        : (current.status === "paused" ? "已暂停" : (current.status === "confirmed" ? "已停止" : "未开始"));
    }
    if((current.status === "running" || current.status === "overtime") && Date.now() - (timerLastSync[step.id] || 0) > 3000){
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
  return normalizedFields(step).filter(f => f.required && !String(vals[f.key] || "").trim()).map(f => f.label);
}

function completeStep(stepId){
  const step = steps.find(s => s.id === stepId);
  const errs = requiredErrors(step);
  if(errs.length){ status(stepId, "必填未填：" + errs.join("、")); return; }
  const timers = getTimers();
  const total = mergedTimerSeconds(step);
  if(total > 0){
    const current = timerState(stepId, total);
    const remaining = Math.max(0, current.remaining ?? current.pausedRemaining ?? 0);
    timers[stepId] = {
      ...current,
      status:"confirmed",
      remaining,
      pausedRemaining:remaining,
      startedAt:null,
      updatedAt:Date.now()
    };
    setTimers(timers);
  }
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
  if(stepId && input.files && input.files.length) {
    document.getElementById("file-"+stepId).textContent = input.files[0].name;
    const nameInput = document.getElementById("name-"+stepId);
    if(nameInput && !nameInput.value.trim()) nameInput.value = input.files[0].name;
  }
}

async function uploadPhoto(event, stepId){
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.file?.files?.[0] || form.file2?.files?.[0] || form.file3?.files?.[0];
  if(!file){ document.getElementById("file-"+stepId).textContent = "请先选择照片或文件"; return; }
  const nameInput = document.getElementById("name-"+stepId);
  await uploadFileToStep(file, stepId, nameInput ? nameInput.value : file.name);
}

function clipboardFileName(blob, index=0){
  const types = {
    "image/png":"png",
    "image/jpeg":"jpg",
    "image/webp":"webp",
    "image/gif":"gif",
    "application/pdf":"pdf"
  };
  const ext = types[blob.type] || (blob.type.split("/")[1] || "bin").replace(/[^a-z0-9]+/gi, "");
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "_",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0")
  ].join("");
  return `clipboard_${stamp}${index ? "_" + (index + 1) : ""}.${ext || "bin"}`;
}

async function uploadFileToStep(file, stepId, requestedName=""){
  const fd = new FormData();
  fd.append("file", file);
  fd.append("attachment_name", String(requestedName || file.name || "").trim() || clipboardFileName(file));
  try {
    const res = await fetch(`/api/photos/upload?step_id=${stepId}`, {method:"POST", body:fd});
    if(!res.ok) throw new Error(await res.text());
    document.getElementById("file-"+stepId).textContent = "上传完成";
    await loadSteps(selectedExperiment);
  } catch(e) {
    document.getElementById("file-"+stepId).textContent = "上传失败，文件保留在本机，请联网后重试";
  }
}

async function uploadClipboardFiles(files, stepId){
  if(!files.length) return false;
  const nameInput = document.getElementById("name-"+stepId);
  const requestedName = nameInput ? nameInput.value.trim() : "";
  for(let index = 0; index < files.length; index++){
    const source = files[index];
    const file = source.name
      ? source
      : new File([source], clipboardFileName(source, index), {type:source.type || "application/octet-stream"});
    const displayName = requestedName
      ? (files.length > 1 ? `${requestedName} ${index + 1}` : requestedName)
      : file.name;
    await uploadFileToStep(file, stepId, displayName);
  }
  return true;
}

async function pasteClipboard(stepId){
  const fileStatus = document.getElementById("file-"+stepId);
  if(!navigator.clipboard || !navigator.clipboard.read){
    if(fileStatus) fileStatus.textContent = "请在页面中按 Ctrl+V，手机端可尝试长按粘贴";
    return;
  }
  try {
    const clipboardItems = await navigator.clipboard.read();
    const files = [];
    for(const item of clipboardItems){
      for(const type of item.types){
        if(type === "text/plain" || type === "text/html") continue;
        files.push(await item.getType(type));
      }
    }
    if(!await uploadClipboardFiles(files, stepId)){
      if(fileStatus) fileStatus.textContent = "剪贴板中没有图片或文件";
    }
  } catch(e) {
    if(fileStatus) fileStatus.textContent = "无法主动读取，请在页面中按 Ctrl+V 或长按粘贴";
  }
}

document.addEventListener("paste", async event => {
  const step = steps[currentStepIndex()];
  if(!step) return;
  const files = Array.from(event.clipboardData?.files || []);
  if(!files.length){
    for(const item of Array.from(event.clipboardData?.items || [])){
      if(item.kind !== "file") continue;
      const file = item.getAsFile();
      if(file) files.push(file);
    }
  }
  if(!files.length) return;
  event.preventDefault();
  await uploadClipboardFiles(files, step.id);
});

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }

// ── 语音速记（voice notes）───────────────────────────────
const voiceState = {recognizing:false, recognition:null, mediaRecorder:null, chunks:[]};

async function loadVoiceNotes(){
  if(!selectedExperiment){ voiceNotes = []; return; }
  try { voiceNotes = await api(`/api/experiments/${selectedExperiment}/voice_notes`); }
  catch { /* 离线时保留上次内容 */ }
}

function voiceTime(iso){
  const t = new Date(iso);
  if(Number.isNaN(t.getTime())) return "";
  const p = n => String(n).padStart(2, "0");
  return `${p(t.getHours())}:${p(t.getMinutes())}`;
}

function voiceNoteHtml(n){
  let body;
  if(n.text){
    body = `<span onclick="editVoiceNote(${n.id})">${esc(n.text)}</span>`;
    if(n.audio_url) body += `<audio controls preload="none" src="${esc(n.audio_url)}"></audio>`;
  } else if(n.audio_url){
    const tag = n.status === "pending" ? "转写中…" : "录音 · 待转写";
    body = `<span class="vtag">${tag}</span><audio controls preload="none" src="${esc(n.audio_url)}"></audio>`;
  } else {
    body = '<span class="vtag">空</span>';
  }
  return `<div class="voice-note">
    <span class="vtime">${voiceTime(n.created_at)}</span>
    <span class="vbody">${body}</span>
    <span class="vops"><button onclick="deleteVoiceNote(${n.id})" title="删除" aria-label="删除速记">✕</button></span>
  </div>`;
}

function renderStepVoice(step){
  const list = voiceNotes.filter(n => n.step_id === step.id);
  const notes = list.length ? `<div class="voice-list" style="margin-top:8px">${list.map(voiceNoteHtml).join("")}</div>` : "";
  return `
    <button type="button" class="secondary mic-btn" onclick="openVoicePanel()" title="录一段语音，自动转成文字记进这一步" aria-label="语音速记">${svgIcon("mic",17)} 语音速记</button>
    ${notes}`;
}

function renderVoiceAll(){
  const box = document.getElementById("voiceAllList");
  if(!box) return;
  if(!voiceNotes.length){ box.innerHTML = '<span class="small">还没有速记。</span>'; return; }
  const byStep = {};
  for(const s of steps) byStep[s.id] = s;
  box.innerHTML = voiceNotes.slice().reverse().map(n => {
    const s = byStep[n.step_id];
    const tag = s ? `<div class="small" style="margin-top:4px">Step ${s.step_index + 1} · ${esc(s.title)}</div>` : "";
    return tag + voiceNoteHtml(n);
  }).join("");
}

function editVoiceNote(id){
  const n = voiceNotes.find(x => x.id === id);
  if(!n) return;
  openModal("编辑速记", `<div class="field"><textarea id="editVoiceText">${esc(n.text || "")}</textarea></div>`, async () => {
    const text = document.getElementById("editVoiceText").value.trim();
    await api(`/api/voice_notes/${id}`, {method:"PATCH", body:JSON.stringify({text})});
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  });
}

async function deleteVoiceNote(id){
  if(!confirm("删除这条速记？")) return;
  try {
    await api(`/api/voice_notes/${id}`, {method:"DELETE"});
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) { alert("删除失败：" + e.message); }
}

function openVoicePanel(){
  document.getElementById("voiceBackdrop").classList.add("open");
  document.getElementById("voiceSheet").classList.add("open");
  renderVoiceAll();
}

function closeVoicePanel(){
  stopVoiceRec();
  document.getElementById("voiceBackdrop").classList.remove("open");
  document.getElementById("voiceSheet").classList.remove("open");
}

function speechSupported(){
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function initVoice(){
  const hint = document.getElementById("voiceHint");
  if(navigator.mediaDevices && window.MediaRecorder){
    hint.textContent = "点「开始说话」录一段，停止后自动上传保存并转写。";
  } else {
    hint.textContent = "此环境不支持录音。点下方输入框，用键盘上的听写（麦克风键）也可以。";
  }
}

function setRecUI(on, label){
  const b = document.getElementById("voiceRecBtn");
  const mic = document.getElementById("micBtn");
  if(b){ b.textContent = on ? (label || "停止") : "开始说话"; b.classList.toggle("rec", on); }
  if(mic) mic.classList.toggle("rec", on);
}

function toggleVoiceRec(){
  if(voiceState.recognizing || voiceState.mediaRecorder){ stopVoiceRec(); return; }
  startRecording();
}

function startSpeech(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR();
  rec.lang = "zh-CN";
  rec.continuous = true;
  rec.interimResults = true;
  const live = document.getElementById("voiceLive");
  rec.onresult = e => {
    let interim = "";
    for(let i = e.resultIndex; i < e.results.length; i++){
      const r = e.results[i];
      if(r.isFinal){
        const t = r[0].transcript.trim();
        if(t){
          const ta = document.getElementById("voiceText");
          ta.value = (ta.value ? ta.value + " " : "") + t;
        }
      } else {
        interim += r[0].transcript;
      }
    }
    live.textContent = interim;
  };
  rec.onend = () => {
    if(voiceState.recognizing){
      try { rec.start(); } catch { voiceState.recognizing = false; setRecUI(false); }
    }
  };
  rec.onerror = e => {
    live.textContent = "";
    if(e.error === "not-allowed" || e.error === "service-not-allowed"){
      voiceState.recognizing = false;
      voiceState.recognition = null;
      setRecUI(false);
      document.getElementById("voiceHint").textContent = "麦克风权限被拒绝。可改用键盘听写，或在系统设置里允许麦克风。";
    }
  };
  voiceState.recognition = rec;
  voiceState.recognizing = true;
  setRecUI(true, "停止听写");
  try { rec.start(); } catch {}
}

async function startRecording(){
  if(!(navigator.mediaDevices && window.MediaRecorder)){
    document.getElementById("voiceHint").textContent = "此环境不支持录音，请用键盘听写。";
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = ["audio/mp4;codecs=mp4a.40.2","audio/mp4","audio/webm;codecs=opus","audio/webm"];
    const mimeType = preferred.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));
    const mr = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream);
    voiceState.chunks = [];
    mr.ondataavailable = e => { if(e.data && e.data.size) voiceState.chunks.push(e.data); };
    mr.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const type = mr.mimeType || mimeType || "audio/mp4";
      const blob = new Blob(voiceState.chunks, {type});
      voiceState.chunks = [];
      if(blob.size > 0) await uploadVoiceAudio(blob, type);
    };
    voiceState.mediaRecorder = mr;
    mr.start();
    setRecUI(true, "停止录音");
  } catch(e) {
    document.getElementById("voiceHint").textContent = "无法打开麦克风：" + e.message;
  }
}

function stopVoiceRec(){
  if(voiceState.recognition){
    voiceState.recognizing = false;
    try { voiceState.recognition.stop(); } catch {}
    voiceState.recognition = null;
  }
  if(voiceState.mediaRecorder){
    try { voiceState.mediaRecorder.stop(); } catch {}
    voiceState.mediaRecorder = null;
  }
  const live = document.getElementById("voiceLive");
  if(live) live.textContent = "";
  setRecUI(false);
}

function currentStepIdForVoice(){
  const s = steps[currentStepIndex()];
  return s ? s.id : null;
}

async function uploadVoiceAudio(blob, type){
  const ext = type.includes("mp4") ? ".m4a" : (type.includes("ogg") ? ".ogg" : (type.includes("webm") ? ".webm" : ".bin"));
  const fd = new FormData();
  fd.append("file", new File([blob], "voice" + ext, {type}));
  const sid = currentStepIdForVoice();
  if(sid) fd.append("step_id", String(sid));
  const hint = document.getElementById("voiceHint");
  try {
    const res = await fetch(`/api/experiments/${selectedExperiment}/voice_notes/audio`, {method:"POST", body:fd});
    if(!res.ok) throw new Error(await res.text());
    hint.textContent = "录音已上传。转写完成后文字会自动出现在记录里。";
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) {
    hint.textContent = "录音上传失败：" + e.message;
  }
}

async function saveVoiceText(){
  const ta = document.getElementById("voiceText");
  const text = ta.value.trim();
  if(!text){ ta.focus(); return; }
  stopVoiceRec();
  try {
    await api(`/api/experiments/${selectedExperiment}/voice_notes`, {
      method:"POST",
      body:JSON.stringify({text, step_id: currentStepIdForVoice()})
    });
    ta.value = "";
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) {
    document.getElementById("voiceHint").textContent = "保存失败（网络断了？）：" + e.message;
  }
}

// ── AI 整理草稿 ─────────────────────────────────────────
let aiDraft = null;

async function runAiOrganize(){
  const hint = document.getElementById("aiHint");
  hint.textContent = "正在让 AI 整理…（首次可能要几秒到十几秒）";
  hint.style.color = "#6d5ae0";
  try {
    const draft = await api(`/api/experiments/${selectedExperiment}/ai_organize`, {
      method:"POST", body: JSON.stringify({})
    });
    aiDraft = draft;
    hint.textContent = "";
    renderAiDraft(draft);
    openAiPanel();
  } catch(e) {
    hint.style.color = "#d98200";
    hint.textContent = "整理失败：" + (e.message || e);
  }
}

function openAiPanel(){
  document.getElementById("aiBackdrop").classList.add("open");
  document.getElementById("aiSheet").classList.add("open");
}
function closeAiPanel(){
  document.getElementById("aiBackdrop").classList.remove("open");
  document.getElementById("aiSheet").classList.remove("open");
}

function renderAiDraft(draft){
  const body = document.getElementById("aiDraftBody");
  const stepsHtml = (draft.steps || []).map((s, si) => {
    const fields = (s.fields || []).map((f, fi) => {
      const changed = f.current && f.current !== f.suggested;
      const cur = f.current ? `<span class="chg">原值 <b>${esc(f.current)}</b> → 建议 <b>${esc(f.suggested)}</b></span>`
                            : `<span class="chg">建议填 <b>${esc(f.suggested)}</b></span>`;
      const rs = f.reason ? `<div class="rs">依据：${esc(f.reason)}</div>` : "";
      return `<label class="ai-field">
        <span class="fl"><span class="k">${esc(f.label)}</span>${cur}${rs}</span>
        <input type="checkbox" data-si="${si}" data-fi="${fi}" checked />
      </label>`;
    }).join("");
    const noteBox = `<textarea data-note="${si}" placeholder="这一步的 Markdown 记录（可改）">${esc(s.note || "")}</textarea>`;
    return `<div class="ai-step" id="ai-step-${si}">
      <h3>第${s.step_index + 1}步 · ${esc(s.title)}</h3>
      ${noteBox}
      ${fields}
      <div class="voice-controls" style="margin-top:8px">
        <button class="green" onclick="applyAiStep(${si})">写入这一步</button>
      </div>
      <div class="ai-applied" id="ai-applied-${si}" style="display:none">✓ 已写入</div>
    </div>`;
  }).join("");
  const un = (draft.unassigned || "").trim()
    ? `<div class="ai-unassigned"><b>未能归入步骤：</b>${esc(draft.unassigned)}</div>` : "";
  const empty = (!draft.steps || !draft.steps.length) && !un
    ? '<div class="small">AI 没能从速记里提取到可写入的内容。</div>' : "";
  body.innerHTML = `<div class="small" style="margin-bottom:8px">模型：${esc(draft.provider)}/${esc(draft.model)} · 来源 ${draft.source_note_count} 条速记</div>${stepsHtml}${un}${empty}`;
}

async function applyAiStep(si){
  const s = aiDraft && aiDraft.steps && aiDraft.steps[si];
  if(!s) return;
  const stepId = s.step_id;
  const step = steps.find(x => x.id === stepId);
  if(!step){ alert("步骤未找到，请先刷新实验。"); return; }
  const values = {...(step.values || {})};
  // fields
  document.querySelectorAll(`input[data-si="${si}"]:checked`).forEach(cb => {
    const f = s.fields[Number(cb.dataset.fi)];
    if(f) values[f.key] = f.suggested;
  });
  // note (append to existing, avoid duplicating)
  const ta = document.querySelector(`textarea[data-note="${si}"]`);
  const noteText = ta ? ta.value.trim() : (s.note || "");
  if(noteText){
    const prev = String(values[STEP_NOTES_KEY] || "").trim();
    values[STEP_NOTES_KEY] = prev && !prev.includes(noteText) ? (prev + "\\n\\n" + noteText) : (prev || noteText);
  }
  try {
    await api(`/api/steps/${stepId}`, {method:"PATCH", body: JSON.stringify({values_json: JSON.stringify(values)})});
    setLocalStep(stepId, {values});
    const badge = document.getElementById("ai-applied-" + si);
    if(badge) badge.style.display = "block";
    renderSteps(steps);
  } catch(e) {
    alert("写入失败：" + e.message);
  }
}

async function applyAllAi(){
  if(!aiDraft || !aiDraft.steps) { closeAiPanel(); return; }
  for(let si = 0; si < aiDraft.steps.length; si++){
    await applyAiStep(si);
  }
  document.getElementById("aiHint").textContent = "已全部写入记录。";
  document.getElementById("aiHint").style.color = "#2e9e5b";
  setTimeout(closeAiPanel, 600);
}

// 语音速记列表定时刷新：等待中的转写完成后自动出现
setInterval(async () => {
  if(!voiceNotes.some(n => n.status === "pending")) return;
  const before = JSON.stringify(voiceNotes);
  await loadVoiceNotes();
  if(JSON.stringify(voiceNotes) !== before){
    renderSteps(steps);
    renderVoiceAll();
  }
}, 8000);

window.addEventListener("online", syncNow);
setInterval(syncNow, 15000);
setInterval(refreshTimers, 1000);
setInterval(tickBoardTimers, 1000);
setInterval(pollBoardTimers, 5000);
function applyBackTarget(){
  const link = document.getElementById("backToFlet");
  if(!link) return;
  // Web-only: home is the capture page.
  link.href = "/capture";
  link.title = "速记";
}
renderQueueInfo();
applyBackTarget();
initVoice();

async function initRunner(){
  await loadExperiments();
  const urlExp = new URLSearchParams(window.location.search).get("experiment_id");
  if(urlExp){ await enterExperiment(urlExp); }
  else { showBoard(); }
}
initRunner();
</script>
"""

