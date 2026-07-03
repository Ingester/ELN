"""
ELN App — Shared web design system + global widgets.

BASE_CSS       : design tokens + common components for every server page.
TIMER_DOCK_HTML: floating timer dock (active step timers + quick timers),
                 include right before </body> on every page.

These are plain strings (no f-string braces issues); pages interpolate them.
"""

from __future__ import annotations

BASE_CSS = """
    :root {
      color-scheme: light;
      --bg: #f6f4f0;
      --card: #ffffff;
      --ink: #1f2328;
      --muted: #79736b;
      --line: #e9e5de;
      --accent: #e8730c;
      --accent-strong: #d05e00;
      --accent-soft: #fdf0e2;
      --green: #2e9e5b;
      --green-soft: #e9f7ee;
      --red: #d64545;
      --red-soft: #fdeeee;
      --radius: 14px;
      --shadow: 0 1px 2px rgba(31, 35, 40, .04), 0 4px 16px rgba(31, 35, 40, .06);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html { font-size: 16px; }
    body {
      margin: 0; background: var(--bg); color: var(--ink);
      font-family: "PingFang SC", "Microsoft YaHei UI", system-ui, -apple-system, "Segoe UI", sans-serif;
      line-height: 1.55;
      padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px));
    }
    header.app-bar {
      position: sticky; top: 0; z-index: 30;
      display: flex; gap: 10px; align-items: center;
      padding: 12px 16px;
      padding-top: max(12px, env(safe-area-inset-top, 0px));
      background: rgba(255, 255, 255, .86);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--line);
    }
    header.app-bar h1 {
      margin: 0; flex: 1; font-size: 17px; font-weight: 700;
      letter-spacing: .01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    main { max-width: 960px; margin: 0 auto; padding: 14px 14px 24px; }
    section, .card {
      background: var(--card); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 16px; box-shadow: var(--shadow);
    }
    h2 { font-size: 15px; font-weight: 700; margin: 0 0 10px; }
    button, a.button, label.button, select, input, textarea { font: inherit; color: inherit; }
    button, a.button, label.button {
      appearance: none; border: 0; cursor: pointer; text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      min-height: 42px; padding: 9px 16px; border-radius: 11px;
      background: linear-gradient(180deg, #f28118, var(--accent-strong));
      color: #fff; font-weight: 600; font-size: 15px;
      box-shadow: 0 1px 2px rgba(208, 94, 0, .35);
      transition: transform .06s ease, filter .15s ease;
    }
    button:active, a.button:active, label.button:active { transform: scale(.97); }
    button.secondary, a.secondary, label.secondary {
      background: #f1efeb; color: #43413d; box-shadow: none;
    }
    button.green { background: linear-gradient(180deg, #35ad66, #278a4f); box-shadow: 0 1px 2px rgba(39, 138, 79, .35); }
    button.ghost { background: transparent; color: var(--accent-strong); box-shadow: none; min-height: 34px; padding: 4px 8px; font-weight: 600; }
    button.danger-ghost { background: transparent; color: var(--red); box-shadow: none; min-height: 34px; padding: 4px 8px; }
    button:disabled, a.button.disabled { background: #d8d4cd; color: #fff; box-shadow: none; cursor: default; }
    input, select, textarea {
      width: 100%; border: 1.5px solid var(--line); border-radius: 11px;
      padding: 10px 12px; background: #fff; min-height: 42px;
      transition: border-color .15s ease, box-shadow .15s ease;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(232, 115, 12, .14);
    }
    textarea { min-height: 110px; resize: vertical; line-height: 1.55; }
    label { color: var(--muted); font-size: 13px; }
    .muted, .small { color: var(--muted); font-size: 12.5px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
    .notice { border-radius: 12px; padding: 12px 14px; font-weight: 600; }
    .notice.ok { background: var(--green-soft); color: #1d6f3f; border: 1px solid #bfe5cc; }
    .notice.error { background: var(--red-soft); color: #b13232; border: 1px solid #f3c6c6; }
    .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, "SF Mono", Consolas, monospace; }
    ::selection { background: rgba(232, 115, 12, .22); }
"""

# Floating timer dock: shows all running/overtime step timers (server-side)
# plus local "quick timers" you can start on any page. Include before </body>.
TIMER_DOCK_HTML = """
<style>
  #elnDock {
    position: fixed; left: 12px; bottom: calc(14px + env(safe-area-inset-bottom, 0px));
    z-index: 60; display: flex; flex-direction: column; gap: 8px; align-items: flex-start;
    max-width: min(78vw, 340px);
  }
  #elnDock .dock-pill {
    display: flex; align-items: center; gap: 8px;
    background: rgba(28, 30, 33, .92); color: #fff;
    border: 0; border-radius: 999px; padding: 8px 14px 8px 12px;
    box-shadow: 0 4px 18px rgba(0, 0, 0, .25);
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    cursor: pointer; font-size: 13.5px; max-width: 100%;
    min-height: 40px;
  }
  #elnDock .dock-pill .t {
    font-variant-numeric: tabular-nums; font-weight: 800; font-size: 15px; letter-spacing: .02em;
  }
  #elnDock .dock-pill .n {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: .85;
  }
  #elnDock .dock-pill.over { background: rgba(198, 40, 40, .94); animation: elnPulse 1.2s ease infinite; }
  #elnDock .dock-pill .x { opacity: .6; padding: 0 2px; font-size: 15px; }
  #elnDock .dock-pill .x:active { opacity: 1; }
  @keyframes elnPulse { 50% { transform: scale(1.03); } }
  #elnDockAdd {
    width: 42px; height: 42px; border-radius: 999px; border: 0; cursor: pointer;
    background: rgba(28, 30, 33, .88); color: #fff; font-size: 19px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, .22);
    display: flex; align-items: center; justify-content: center;
  }
  #elnDockForm {
    display: none; background: #fff; border: 1px solid #e9e5de; border-radius: 14px;
    padding: 12px; box-shadow: 0 10px 30px rgba(0, 0, 0, .18); width: 230px;
  }
  #elnDockForm.open { display: block; }
  #elnDockForm input {
    width: 100%; border: 1.5px solid #e9e5de; border-radius: 10px; padding: 8px 10px;
    font: inherit; margin-bottom: 8px; min-height: 40px; box-sizing: border-box;
  }
  #elnDockForm .row { display: flex; gap: 8px; }
  #elnDockForm button {
    flex: 1; border: 0; border-radius: 10px; min-height: 38px; cursor: pointer;
    font: inherit; font-weight: 600;
  }
  #elnDockForm .go { background: #e8730c; color: #fff; }
  #elnDockForm .no { background: #f1efeb; color: #43413d; }
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
  <button id="elnDockAdd" title="快速计时" aria-label="快速计时">⏱</button>
</div>
<script>
(function(){
  const QT_KEY = "eln.quicktimers";
  let serverTimers = [];
  let audioCtx = null;

  function loadQuick(){ try { return JSON.parse(localStorage.getItem(QT_KEY) || "[]"); } catch { return []; } }
  function saveQuick(list){ localStorage.setItem(QT_KEY, JSON.stringify(list)); }
  function fmt(sec){
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const ms = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    return h ? h + ":" + ms : ms;
  }
  function unlockAudio(){
    try {
      if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if(audioCtx.state === "suspended") audioCtx.resume();
    } catch {}
  }
  function beep(){
    try {
      if(!audioCtx) return;
      for(let i = 0; i < 3; i++){
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = 880; o.type = "sine";
        o.connect(g); g.connect(audioCtx.destination);
        const t = audioCtx.currentTime + i * 0.45;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.4, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
        o.start(t); o.stop(t + 0.4);
      }
    } catch {}
  }
  function alertOnce(){
    try { navigator.vibrate && navigator.vibrate([300, 120, 300, 120, 600]); } catch {}
    beep();
  }

  async function pollServer(){
    try {
      const res = await fetch("/api/timers/active", {headers: {"Accept": "application/json"}});
      if(!res.ok) return;
      const list = await res.json();
      const now = Date.now();
      serverTimers = list.map(t => {
        const updated = Date.parse(t.updated_at || "") || now;
        return {
          key: "srv-" + t.experiment_id + "-" + t.step_id,
          experiment_id: t.experiment_id, step_id: t.step_id,
          label: (t.step_title || ("Step " + ((t.step_index ?? 0) + 1))),
          exp: t.experiment_name || "",
          status: t.status,
          endAt: t.status === "running" ? updated + t.remaining_seconds * 1000 : null,
          overBase: t.overtime_seconds || 0, overSince: updated
        };
      });
    } catch {}
  }

  function render(){
    const box = document.getElementById("elnDockPills");
    if(!box) return;
    const now = Date.now();
    const parts = [];

    for(const t of serverTimers){
      let over = false, secs = 0;
      if(t.status === "running" && t.endAt){
        secs = Math.round((t.endAt - now) / 1000);
        if(secs <= 0){ over = true; secs = -secs; }
      } else {
        over = true;
        secs = t.overBase + Math.round((now - t.overSince) / 1000);
      }
      const label = t.label + (t.exp ? " · " + t.exp : "");
      parts.push(
        '<button class="dock-pill' + (over ? " over" : "") + '" ' +
        'onclick="ElnDock.openStep(' + t.experiment_id + ',' + t.step_id + ')">' +
        '<span class="t">' + (over ? "+" : "") + fmt(secs) + '</span>' +
        '<span class="n">' + escHtml(label) + '</span></button>'
      );
    }

    let quick = loadQuick();
    let dirty = false;
    for(const q of quick){
      const remain = Math.round((q.endAt - now) / 1000);
      const over = remain <= 0;
      if(over && !q.alerted){ q.alerted = true; dirty = true; alertOnce(); }
      parts.push(
        '<button class="dock-pill' + (over ? " over" : "") + '" onclick="ElnDock.dismissQuick(\\'' + q.id + '\\')">' +
        '<span class="t">' + (over ? "+" + fmt(-remain) : fmt(remain)) + '</span>' +
        '<span class="n">' + escHtml(q.label || "快速计时") + '</span>' +
        '<span class="x">✕</span></button>'
      );
    }
    if(dirty) saveQuick(quick);
    box.innerHTML = parts.join("");
    box.style.display = parts.length ? "flex" : "none";
    box.style.flexDirection = "column";
    box.style.gap = "8px";
    box.style.alignItems = "flex-start";
  }

  function escHtml(v){
    return String(v ?? "").replace(/[&<>"']/g, s => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[s]));
  }

  window.ElnDock = {
    toggleForm(show){
      unlockAudio();
      const f = document.getElementById("elnDockForm");
      const open = show === undefined ? !f.classList.contains("open") : show;
      f.classList.toggle("open", open);
      if(open) setTimeout(() => document.getElementById("elnDockMin").focus(), 50);
    },
    startQuick(){
      const min = parseFloat(document.getElementById("elnDockMin").value);
      if(!min || min <= 0){ document.getElementById("elnDockMin").focus(); return; }
      const label = document.getElementById("elnDockLabel").value.trim();
      const list = loadQuick();
      list.push({
        id: Date.now() + "-" + Math.random().toString(16).slice(2),
        label, endAt: Date.now() + Math.round(min * 60000), alerted: false
      });
      saveQuick(list);
      document.getElementById("elnDockMin").value = "";
      document.getElementById("elnDockLabel").value = "";
      ElnDock.toggleForm(false);
      render();
    },
    dismissQuick(id){
      saveQuick(loadQuick().filter(q => q.id !== id));
      render();
    },
    openStep(expId, stepId){
      location.href = "/run?experiment_id=" + expId + "&step_id=" + stepId;
    }
  };

  document.getElementById("elnDockAdd").addEventListener("click", () => ElnDock.toggleForm());
  pollServer();
  setInterval(pollServer, 5000);
  setInterval(render, 1000);
  render();
})();
</script>
"""


def page_head(title: str, extra_css: str = "") -> str:
    """Standard <head> for server-rendered pages."""
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />\n'
        '  <meta name="theme-color" content="#f6f4f0" />\n'
        f"  <title>{title}</title>\n"
        f"  <style>{BASE_CSS}{extra_css}</style>\n"
        "</head>"
    )
