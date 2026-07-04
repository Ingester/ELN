"""
ELN App — Shared web design system + global widgets.

Aesthetic: clean, minimal, "Claude-style" — warm paper background, clay
(terracotta) accent, hairline borders, monochrome line icons (Lucide-style,
inlined so it works offline), no colorful emoji. Light mode only.

Exports
    BASE_CSS        design tokens + common components for every server page
    ICONS / icon()  inline SVG line-icon set (server-side)
    ICON_JS         same icons for client-rendered HTML: svgIcon(name, size)
    TIMER_DOCK_HTML floating timer dock, include right before </body>
    page_head()     standard <head>
"""

from __future__ import annotations

# ── Line icons (Lucide-style, 24px viewBox, stroke = currentColor) ───────────
ICONS = {
    "mic": '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z"/><circle cx="12" cy="13" r="3"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="1.6"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 21"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.4 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.4-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.8 1.1Z"/>',
    "flask": '<path d="M14 2v6.3a2 2 0 0 0 .6 1.4l5.6 5.7A2 2 0 0 1 18.8 19H5.2a2 2 0 0 1-1.4-3.4l5.6-5.7A2 2 0 0 0 10 8.3V2"/><path d="M6.5 15h11"/><path d="M8.5 2h7"/>',
    "note": '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.4 2.6a2 2 0 0 1 2.8 2.8L12 15l-4 1 1-4Z"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-9-9c2.5 0 4.9 1 6.7 2.7L21 8"/><path d="M21 3v5h-5"/>',
    "pencil": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "sparkle": '<path d="M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z"/>',
    "timer": '<circle cx="12" cy="14" r="8"/><path d="M12 14V10.5"/><path d="M9 2h6"/>',
    "home": '<path d="M15 21v-8H9v8"/><path d="M3 10a2 2 0 0 1 .7-1.5l7-6a2 2 0 0 1 2.6 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "audio": '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M18 13h2a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-1a1 1 0 0 1-1-1z"/><path d="M6 13H4a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1z"/>',
    "play": '<path d="m7 4 12 8-12 8Z"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "upload": '<path d="M12 19V6"/><path d="m6 11 6-6 6 6"/><path d="M5 21h14"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "clipboard": '<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M8 5H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 0 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 0 1 0-4h.1A1.7 1.7 0 0 0 4.6 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9.5A1.7 1.7 0 0 0 10.6 3V3a2 2 0 0 1 4 0v.1A1.7 1.7 0 0 0 17 4.6l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9.5a1.7 1.7 0 0 0 1.5 1.1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.4z"/>',
}


def icon(name: str, size: int = 20, cls: str = "") -> str:
    body = ICONS.get(name, "")
    c = ("icon " + cls).strip()
    return (f'<svg class="{c}" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" aria-hidden="true">{body}</svg>')


def _icons_js() -> str:
    import json
    return "window.ELN_ICONS=" + json.dumps(ICONS) + ";\n" + (
        "function svgIcon(name,size){size=size||20;"
        "return '<svg class=\"icon\" width=\"'+size+'\" height=\"'+size+'\" "
        "viewBox=\"0 0 24 24\" fill=\"none\" aria-hidden=\"true\">'+(window.ELN_ICONS[name]||'')+'</svg>';}"
    )


ICON_JS = _icons_js()


BASE_CSS = """
    :root {
      color-scheme: light;
      --bg: #f4f2ec;
      --card: #ffffff;
      --inset: #faf8f3;
      --ink: #2a2620;
      --muted: #6f6a61;
      --faint: #9c968b;
      --line: #e7e3da;
      --line-strong: #d9d4c8;
      --clay: #bd5b3d;
      --clay-ink: #9c4326;
      --clay-soft: #f4e8e2;
      --clay-line: #e6cdc0;
      --pos: #3f7a57;
      --pos-soft: #eaf1ea;
      --neg: #b0462e;
      --neg-soft: #f6e9e5;
      --r: 10px;
      --r-lg: 14px;
      /* compatibility aliases for older per-page CSS */
      --radius: 14px;
      --shadow: none;
      --accent: var(--clay);
      --accent-strong: var(--clay-ink);
      --accent-soft: var(--clay-soft);
      --green: var(--pos);
      --green-soft: var(--pos-soft);
      --red: var(--neg);
      --red-soft: var(--neg-soft);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html { font-size: 16px; }
    body {
      margin: 0; background: var(--bg); color: var(--ink);
      font-family: "PingFang SC", "Microsoft YaHei UI", system-ui, -apple-system, "Segoe UI", "Noto Sans SC", sans-serif;
      line-height: 1.6;
      padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px));
    }
    svg.icon { flex: 0 0 auto; stroke: currentColor; fill: none;
      stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; vertical-align: -3px; }
    header.app-bar {
      position: sticky; top: 0; z-index: 30;
      display: flex; gap: 10px; align-items: center;
      padding: 12px 16px; padding-top: max(12px, env(safe-area-inset-top, 0px));
      background: rgba(244, 242, 236, .82);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--line);
    }
    header.app-bar h1 {
      margin: 0; flex: 1; font-size: 16.5px; font-weight: 600;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    main { max-width: 720px; margin: 0 auto; padding: 14px 14px 24px; }
    section, .card {
      background: var(--card); border: 1px solid var(--line);
      border-radius: var(--r-lg); padding: 16px;
    }
    h2 { font-size: 14.5px; font-weight: 600; margin: 0 0 10px; }
    button, a.button, label.button, select, input, textarea { font: inherit; color: inherit; }
    button, a.button, label.button {
      appearance: none; cursor: pointer; text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center; gap: 7px;
      min-height: 40px; padding: 8px 15px; border-radius: var(--r);
      border: 1px solid var(--ink); background: var(--ink); color: #faf8f3;
      font-weight: 500; font-size: 14.5px; letter-spacing: .01em;
      transition: transform .05s ease, background .15s ease, border-color .15s ease, color .15s ease;
    }
    button:active, a.button:active, label.button:active { transform: scale(.98); }
    button.secondary, a.secondary, label.secondary {
      background: transparent; color: var(--ink); border-color: var(--line-strong);
    }
    button.secondary:hover, a.secondary:hover, label.secondary:hover { background: var(--inset); }
    button.green, a.green, button.accent, a.accent {
      background: var(--clay); border-color: var(--clay); color: #fff;
    }
    button.ghost, a.ghost {
      background: transparent; border-color: transparent; color: var(--muted);
      min-height: 32px; padding: 5px 8px; font-weight: 500;
    }
    button.ghost:hover { color: var(--ink); }
    button.danger-ghost {
      background: transparent; border-color: transparent; color: var(--neg);
      min-height: 32px; padding: 5px 8px;
    }
    .icon-btn {
      background: transparent; color: var(--ink); border: 1px solid var(--line-strong);
      min-height: 38px; min-width: 38px; padding: 7px 9px;
    }
    button:disabled, a.button.disabled { opacity: .45; cursor: default; }
    input, select, textarea {
      width: 100%; border: 1px solid var(--line-strong); border-radius: var(--r);
      padding: 9px 12px; background: #fff; min-height: 40px;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: var(--clay);
      box-shadow: 0 0 0 3px rgba(189, 91, 61, .13);
    }
    textarea { min-height: 110px; resize: vertical; line-height: 1.6; }
    label { color: var(--muted); font-size: 13px; }
    .muted, .small { color: var(--muted); font-size: 12.5px; }
    a { color: var(--clay-ink); }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
    .edit-link {
      background: transparent; border-color: transparent; color: var(--clay-ink);
      min-height: 30px; padding: 3px 6px; font-size: 13px; font-weight: 500;
    }
    .notice { border-radius: var(--r); padding: 11px 14px; font-weight: 500; border: 1px solid; }
    .notice.ok { background: var(--pos-soft); color: #2c5c40; border-color: #cfe2d5; }
    .notice.error { background: var(--neg-soft); color: #8c3620; border-color: #e8cabf; }
    .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, "SF Mono", Consolas, monospace; }
    ::selection { background: rgba(189, 91, 61, .18); }
"""


# Floating timer dock — dark pills for contrast, clay for the quick-timer form.
TIMER_DOCK_HTML = """
<style>
  #elnDock { position: fixed; left: 12px; bottom: calc(78px + env(safe-area-inset-bottom, 0px));
    z-index: 55; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; max-width: min(78vw, 340px); }
  #elnDock .dock-pill { display: flex; align-items: center; gap: 8px; background: var(--ink); color: #f4f2ec;
    border: 0; border-radius: 999px; padding: 8px 14px 8px 12px; box-shadow: 0 4px 16px rgba(0,0,0,.18);
    cursor: pointer; font-size: 13.5px; max-width: 100%; min-height: 38px; }
  #elnDock .dock-pill .t { font-variant-numeric: tabular-nums; font-weight: 600; font-size: 15px; letter-spacing: .02em; }
  #elnDock .dock-pill .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .82; }
  #elnDock .dock-pill.over { background: #a63a24; animation: elnPulse 1.2s ease infinite; }
  #elnDock .dock-pill .x { opacity: .6; display: inline-flex; }
  #elnDock .dock-pill svg { stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
  @keyframes elnPulse { 50% { transform: scale(1.03); } }
  #elnDockAdd { width: 40px; height: 40px; border-radius: 999px; border: 0; cursor: pointer;
    background: var(--ink); color: #f4f2ec; box-shadow: 0 4px 12px rgba(0,0,0,.16);
    display: flex; align-items: center; justify-content: center; }
  #elnDockAdd svg { stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
  #elnDockForm { display: none; background: #fff; border: 1px solid var(--line); border-radius: 14px;
    padding: 12px; box-shadow: 0 10px 28px rgba(0,0,0,.14); width: 230px; }
  #elnDockForm.open { display: block; }
  #elnDockForm input { width: 100%; border: 1px solid var(--line-strong); border-radius: 10px; padding: 8px 10px;
    font: inherit; margin-bottom: 8px; min-height: 40px; box-sizing: border-box; }
  #elnDockForm .row { display: flex; gap: 8px; }
  #elnDockForm button { flex: 1; border: 0; border-radius: 10px; min-height: 38px; cursor: pointer; font: inherit; font-weight: 500; }
  #elnDockForm .go { background: var(--clay); color: #fff; }
  #elnDockForm .no { background: var(--inset); color: var(--ink); }
</style>
<div id="elnDock">
  <div id="elnDockPills"></div>
  <div id="elnDockForm">
    <input id="elnDockLabel" placeholder="计时名称（如：孵育）" />
    <input id="elnDockMin" type="number" inputmode="decimal" min="0.1" step="0.5" placeholder="分钟" />
    <div class="row">
      <button class="go" onclick="ElnDock.startQuick()">开始</button>
      <button class="no" onclick="ElnDock.toggleForm(false)">取消</button>
    </div>
  </div>
  <button id="elnDockAdd" title="快速计时" aria-label="快速计时">__ADD_ICON__</button>
</div>
<script>
(function(){
  const QT_KEY = "eln.quicktimers";
  const XICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  let serverTimers = [];
  let audioCtx = null;
  function loadQuick(){ try { return JSON.parse(localStorage.getItem(QT_KEY) || "[]"); } catch { return []; } }
  function saveQuick(list){ localStorage.setItem(QT_KEY, JSON.stringify(list)); }
  function fmt(sec){ sec = Math.max(0, Math.floor(sec)); const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
    const ms = String(m).padStart(2,"0")+":"+String(s).padStart(2,"0"); return h ? h+":"+ms : ms; }
  function unlockAudio(){ try { if(!audioCtx) audioCtx = new (window.AudioContext||window.webkitAudioContext)(); if(audioCtx.state==="suspended") audioCtx.resume(); } catch {} }
  function beep(){ try { if(!audioCtx) return; for(let i=0;i<3;i++){ const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.frequency.value=880; o.type="sine"; o.connect(g); g.connect(audioCtx.destination); const t=audioCtx.currentTime+i*0.45;
    g.gain.setValueAtTime(0.0001,t); g.gain.exponentialRampToValueAtTime(0.4,t+0.02); g.gain.exponentialRampToValueAtTime(0.0001,t+0.35); o.start(t); o.stop(t+0.4); } } catch {} }
  function alertOnce(){ try { navigator.vibrate && navigator.vibrate([300,120,300,120,600]); } catch {} beep(); }
  async function pollServer(){ try { const res = await fetch("/api/timers/active", {headers:{"Accept":"application/json"}});
    if(!res.ok) return; const list = await res.json(); const now = Date.now();
    serverTimers = list.map(t => { const updated = Date.parse(t.updated_at||"")||now;
      return { experiment_id:t.experiment_id, step_id:t.step_id, label:(t.step_title||("Step "+((t.step_index??0)+1))),
        exp:t.experiment_name||"", status:t.status, endAt:t.status==="running"?updated+t.remaining_seconds*1000:null,
        overBase:t.overtime_seconds||0, overSince:updated }; }); } catch {} }
  function render(){ const box = document.getElementById("elnDockPills"); if(!box) return; const now = Date.now(); const parts = [];
    for(const t of serverTimers){ let over=false, secs=0;
      if(t.status==="running" && t.endAt){ secs = Math.round((t.endAt-now)/1000); if(secs<=0){ over=true; secs=-secs; } }
      else { over=true; secs = t.overBase + Math.round((now-t.overSince)/1000); }
      const label = t.label + (t.exp ? " · "+t.exp : "");
      parts.push('<button class="dock-pill'+(over?" over":"")+'" onclick="ElnDock.openStep('+t.experiment_id+','+t.step_id+')">'
        +'<span class="t">'+(over?"+":"")+fmt(secs)+'</span><span class="n">'+escHtml(label)+'</span></button>'); }
    let quick = loadQuick(); let dirty=false;
    for(const q of quick){ const remain = Math.round((q.endAt-now)/1000); const over = remain<=0;
      if(over && !q.alerted){ q.alerted=true; dirty=true; alertOnce(); }
      parts.push('<button class="dock-pill'+(over?" over":"")+'" onclick="ElnDock.dismissQuick(\\''+q.id+'\\')">'
        +'<span class="t">'+(over?"+"+fmt(-remain):fmt(remain))+'</span><span class="n">'+escHtml(q.label||"快速计时")+'</span>'
        +'<span class="x">'+XICON+'</span></button>'); }
    if(dirty) saveQuick(quick);
    box.innerHTML = parts.join(""); box.style.display = parts.length ? "flex" : "none";
    box.style.flexDirection = "column"; box.style.gap = "8px"; box.style.alignItems = "flex-start"; }
  function escHtml(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[s])); }
  window.ElnDock = {
    toggleForm(show){ unlockAudio(); const f = document.getElementById("elnDockForm");
      const open = show===undefined ? !f.classList.contains("open") : show; f.classList.toggle("open", open);
      if(open) setTimeout(() => document.getElementById("elnDockMin").focus(), 50); },
    startQuick(){ const min = parseFloat(document.getElementById("elnDockMin").value);
      if(!min || min<=0){ document.getElementById("elnDockMin").focus(); return; }
      const label = document.getElementById("elnDockLabel").value.trim(); const list = loadQuick();
      list.push({ id: Date.now()+"-"+Math.random().toString(16).slice(2), label, endAt: Date.now()+Math.round(min*60000), alerted:false });
      saveQuick(list); document.getElementById("elnDockMin").value=""; document.getElementById("elnDockLabel").value="";
      ElnDock.toggleForm(false); render(); },
    dismissQuick(id){ saveQuick(loadQuick().filter(q => q.id !== id)); render(); },
    openStep(expId, stepId){ location.href = "/run?experiment_id="+expId+"&step_id="+stepId; }
  };
  document.getElementById("elnDockAdd").addEventListener("click", () => ElnDock.toggleForm());
  pollServer(); setInterval(pollServer, 5000); setInterval(render, 1000); render();
})();
</script>
""".replace("__ADD_ICON__", icon("timer", 20).replace("\n", ""))


def page_head(title: str, extra_css: str = "") -> str:
    """Standard <head> for server-rendered pages."""
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />\n'
        '  <meta name="theme-color" content="#f4f2ec" />\n'
        f"  <title>{title}</title>\n"
        f"  <style>{BASE_CSS}{extra_css}</style>\n"
        "</head>"
    )
