/* ELN comment overlay — injected into every page.
   Shows comment pins for the current page, and (in comment mode) lets you click
   any empty spot to leave an anchored comment. Owner and openview visitors both
   see pins; author name comes from the server (IP nickname for openview). */
(function(){
  if (window.__elnOverlay) return; window.__elnOverlay = true;
  var MODE="none", ME="", CAN=false, commentMode=false;
  var page = location.pathname + location.search;
  var layer=null, toolbar=null, btn=null, composer=null, popover=null, comments=[];

  function esc(v){ return String(v==null?"":v).replace(/[&<>"']/g,function(s){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[s];}); }
  function api(p,opts){ opts=opts||{}; opts.headers=Object.assign({'Content-Type':'application/json'},opts.headers||{}); return fetch(p,opts).then(function(r){ if(!r.ok) throw new Error(r.status); return r.status===204?null:r.json(); }); }

  function cssPath(el){
    if(!el || el===document.body || el.nodeType!==1) return "";
    if(el.id) return "#"+CSS.escape(el.id);
    var parts=[], depth=0;
    while(el && el.nodeType===1 && el!==document.body && depth<6){
      var p=el.parentElement; if(!p){ break; }
      var same=Array.prototype.filter.call(p.children,function(c){return c.tagName===el.tagName;});
      parts.unshift(el.tagName.toLowerCase()+":nth-of-type("+(same.indexOf(el)+1)+")");
      el=p; depth++;
    }
    return parts.join(">");
  }
  function isInteractive(el){ return !!(el && el.closest && el.closest('a,button,input,select,textarea,label,summary,details,[onclick],[role="button"],.chip,.nav-cell,.bc-abandon,audio,video')); }

  function ensureLayer(){
    if(layer) return;
    layer=document.createElement('div');
    layer.id='elnCommentLayer';
    layer.style.cssText='position:absolute;top:0;left:0;width:0;height:0;z-index:2147483000;';
    document.body.appendChild(layer);
  }
  function anchorPos(c){
    if(c.anchor){ try{ var el=document.querySelector(c.anchor); if(el){ var r=el.getBoundingClientRect(); return {x:r.left+window.scrollX+r.width-6, y:r.top+window.scrollY+6}; } }catch(e){} }
    return {x:c.x||20, y:c.y||20};
  }
  function renderPins(){
    ensureLayer(); layer.innerHTML='';
    comments.forEach(function(c){
      var pos=anchorPos(c);
      var pin=document.createElement('button'); pin.type='button';
      pin.style.cssText='position:absolute;left:'+pos.x+'px;top:'+pos.y+'px;transform:translate(-50%,-50%);width:26px;height:26px;border-radius:50% 50% 50% 3px;background:#bd5b3d;color:#fff;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);cursor:pointer;font-size:12px;line-height:1;padding:0;';
      pin.textContent='C';
      pin.title=(c.author||'')+': '+String(c.text||'').slice(0,60);
      pin.onclick=function(ev){ ev.stopPropagation(); showPopover(c,pos); };
      layer.appendChild(pin);
    });
  }
  function closePopover(){ if(popover){ popover.remove(); popover=null; } }
  function showPopover(c,pos){
    closePopover();
    popover=document.createElement('div');
    popover.style.cssText='position:absolute;left:'+pos.x+'px;top:'+(pos.y+18)+'px;max-width:260px;background:#fff;border:1px solid #ddd;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.18);padding:10px 12px;z-index:2147483001;font-size:13.5px;color:#222;';
    var onwhat=c.anchor_text?('<div style="color:#8a5a44;font-size:12px;margin-bottom:4px">对：'+esc(c.anchor_text)+'</div>'):'';
    var t=c.created_at?new Date(c.created_at).toLocaleString():'';
    var del=(MODE==='owner')?'<button data-del="'+esc(c.id)+'" style="margin-top:8px;font-size:12px;background:none;border:0;color:#c0503a;cursor:pointer;padding:0">删除</button>':'';
    popover.innerHTML=onwhat+'<div style="white-space:pre-wrap;word-break:break-word">'+esc(c.text)+'</div><div style="color:#999;font-size:11.5px;margin-top:6px">'+esc(c.author||'访客')+' · '+t+'</div>'+del;
    popover.addEventListener('click',function(e){ e.stopPropagation(); });
    Array.prototype.forEach.call(popover.querySelectorAll('[data-del]'),function(b){ b.onclick=function(){ if(!confirm('删除这条评论？'))return; api('/api/openview/comments/'+b.getAttribute('data-del'),{method:'DELETE'}).then(function(){ closePopover(); reload(); }); }; });
    layer.appendChild(popover);
    setTimeout(function(){ document.addEventListener('click',closePopover,{once:true}); },0);
  }
  // Only label a comment with what it's "on" when the clicked element is
  // specific — not a big container (body/main/section) whose textContent is the
  // whole page. Empty-space clicks become pure position pins (no misleading 对：).
  function anchorLabel(el){
    if(!el || el.nodeType!==1) return '';
    var tag=(el.tagName||'').toLowerCase();
    if(['body','html','main','section','nav','header','footer','form','ul','ol','article'].indexOf(tag)>=0) return '';
    var lab=(el.getAttribute&&(el.getAttribute('aria-label')||el.getAttribute('placeholder')))||'';
    var txt=String(lab||el.textContent||'').trim().replace(/\s+/g,' ');
    if(!txt || txt.length>70) return '';
    return txt.slice(0,60);
  }
  function closeComposer(){ if(composer){ composer.remove(); composer=null; } }
  function openComposer(px,py,target){
    closeComposer();
    var atext=anchorLabel(target);
    var anchor=atext?cssPath(target):'';
    composer=document.createElement('div');
    composer.style.cssText='position:absolute;left:'+px+'px;top:'+py+'px;z-index:2147483002;background:#fff;border:1px solid #ccc;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.22);padding:10px;width:240px;';
    composer.innerHTML=(atext?'<div style="color:#8a5a44;font-size:12px;margin-bottom:6px">对：'+esc(atext)+'</div>':'')+
      '<textarea id="elnCmtText" placeholder="写下评论…" style="width:100%;min-height:64px;font:inherit;border:1px solid #ddd;border-radius:8px;padding:6px;box-sizing:border-box"></textarea>'+
      '<div style="display:flex;gap:8px;margin-top:8px"><button id="elnCmtSave" style="flex:1;background:#3f8f5f;color:#fff;border:0;border-radius:8px;padding:7px;cursor:pointer">留下评论</button><button id="elnCmtCancel" style="background:#eee;border:0;border-radius:8px;padding:7px 10px;cursor:pointer">取消</button></div>';
    composer.addEventListener('click',function(e){ e.stopPropagation(); });
    layer.appendChild(composer);
    var ta=composer.querySelector('#elnCmtText'); ta.focus();
    composer.querySelector('#elnCmtCancel').onclick=closeComposer;
    composer.querySelector('#elnCmtSave').onclick=function(){
      var text=ta.value.trim(); if(!text) return;
      api('/api/openview/comments',{method:'POST',body:JSON.stringify({page:page,text:text,x:px,y:py,anchor:anchor,anchor_text:atext})})
        .then(function(){ closeComposer(); reload(); }).catch(function(e){ alert('评论失败：'+e); });
    };
  }
  function reload(){ api('/api/openview/comments?page='+encodeURIComponent(page)).then(function(list){ comments=list||[]; renderPins(); }).catch(function(){}); }
  function onDocClick(e){
    if(!commentMode) return;
    if(e.target.closest && e.target.closest('#elnCommentLayer')) return;
    if(isInteractive(e.target)) return;
    e.preventDefault(); e.stopPropagation();
    openComposer(e.pageX,e.pageY,e.target);
  }
  function setMode(on){
    commentMode=on;
    document.documentElement.style.cursor= on?'crosshair':'';
    if(btn){ btn.textContent= on?'评论中（点任意位置留言）':'评论'; btn.style.background= on?'#bd5b3d':'#2b2b2b'; }
    if(!on) closeComposer();
  }
  function buildToolbar(){
    toolbar=document.createElement('div');
    toolbar.style.cssText='position:fixed;right:12px;bottom:130px;z-index:2147483002;display:flex;flex-direction:column;gap:8px;align-items:flex-end;';
    btn=document.createElement('button'); btn.type='button'; btn.textContent='评论';
    btn.style.cssText='background:#2b2b2b;color:#fff;border:0;border-radius:999px;padding:9px 16px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.25);cursor:pointer;';
    btn.onclick=function(){ setMode(!commentMode); };
    var tag=document.createElement('div');
    tag.textContent=(MODE==='openview'?('查看/评论 · '+ME):'评论（主人）');
    tag.style.cssText='background:rgba(0,0,0,.55);color:#fff;font-size:11px;padding:3px 8px;border-radius:999px;';
    toolbar.appendChild(btn); toolbar.appendChild(tag);
    document.body.appendChild(toolbar);
  }
  function buildBanner(){
    if(MODE!=='openview') return;
    var bar=document.createElement('div');
    bar.textContent='分享查看/评论模式 · '+ME+' — 这是别人分享给你的视图，你的改动会被记录';
    bar.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483003;background:#bd5b3d;color:#fff;font-size:12.5px;font-weight:600;text-align:center;padding:6px 12px;box-shadow:0 1px 4px rgba(0,0,0,.2);';
    document.body.appendChild(bar);
    document.body.style.paddingTop='30px';
  }
  var rt=null;
  function scheduleReposition(){ if(rt)return; rt=setTimeout(function(){ rt=null; renderPins(); },120); }

  api('/api/openview/whoami').then(function(w){
    MODE=w.mode; ME=w.name; CAN=w.can_comment;
    if(MODE==='none') return;
    ensureLayer(); buildToolbar(); buildBanner();
    document.addEventListener('click', onDocClick, true);
    window.addEventListener('scroll', scheduleReposition, true);
    window.addEventListener('resize', scheduleReposition);
    reload(); setInterval(reload, 15000);
  }).catch(function(){});
})();
