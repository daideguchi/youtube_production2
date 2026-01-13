function normalizeChannel(raw){
  const s=String(raw||"").trim().toUpperCase();
  const m=s.match(/^CH(\d{1,3})$/);
  if(m){
    const n=Number(m[1]);
    if(Number.isFinite(n))return `CH${String(n).padStart(2,"0")}`;
  }
  return s;
}
function normalizeVideo(raw){
  const s=String(raw||"").trim();
  if(/^\d{3}$/.test(s))return s;
  const n=Number(s);
  if(Number.isFinite(n))return String(n).padStart(3,"0");
  return s;
}
function gotoEpisode(ch, video, view){
  const C=normalizeChannel(ch);
  const V=normalizeVideo(video);
  if(!/^CH\d{2,3}$/.test(C)||!/^\d{3}$/.test(V))return;
  let path=`./${C}/${V}/`;
  if(view&&view!=="script")path+=`${view}/`;
  location.href=path;
}

function textOrEmpty(el){
  const s=String((el?.innerText||el?.textContent||"")||"").replace(/\r\n/g,"\n").trim();
  return s;
}
async function copyText(text){
  const s=String(text||"");
  if(!s.trim())return false;
  if(navigator.clipboard&&window.isSecureContext){
    await navigator.clipboard.writeText(s);
    return true;
  }
  const ta=document.createElement("textarea");
  ta.value=s;
  ta.style.position="fixed";
  ta.style.top="-1000px";
  ta.style.left="-1000px";
  ta.setAttribute("readonly","");
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  let ok=false;
  try{ok=document.execCommand("copy");}catch(_e){ok=false;}
  document.body.removeChild(ta);
  return ok;
}

function setupEpJumpForm(){
  const form=document.getElementById("epJumpForm");
  if(!form)return;
  const input=document.getElementById("epJumpInput");
  const viewSel=document.getElementById("epJumpView");
  form.addEventListener("submit",(e)=>{
    e.preventDefault();
    const raw=(input?.value||"").trim();
    if(!raw)return;
    const norm=raw.toUpperCase().replace(/\s+/g,"-");
    const m=norm.match(/^CH\d{1,3}[-]?\d{1,4}$/);
    if(!m)return;
    const parts=norm.split("-");
    const ch=parts[0];
    const video=parts[1]||"";
    gotoEpisode(ch, video, String(viewSel?.value||"script"));
  });
}

function getIframeDoc(frame){
  try{return frame?.contentDocument||frame?.contentWindow?.document||null;}catch(_e){return null;}
}

function setupEpisodeDescriptionCopy(){
  const panel=document.getElementById("descPanel");
  if(!panel)return;
  const frame=document.querySelector("iframe.frame");
  if(!frame)return;

  const status=document.getElementById("descStatus");
  const studioLink=document.getElementById("descStudioLink");
  const fullTa=document.getElementById("descFull");
  const epTa=document.getElementById("descEpisode");
  const chTa=document.getElementById("descChannel");

  function setStatus(msg){
    if(status)status.textContent=String(msg||"");
  }
  function setStudioHref(href){
    if(!studioLink)return;
    const s=String(href||"").trim();
    if(!s){
      studioLink.setAttribute("aria-disabled","true");
      studioLink.classList.remove("btn--accent");
      studioLink.href="#";
      return;
    }
    studioLink.removeAttribute("aria-disabled");
    studioLink.classList.add("btn--accent");
    studioLink.href=s;
  }
  function updateCopyButtons(){
    panel.querySelectorAll("[data-copy-target]").forEach((btn)=>{
      const id=String(btn.getAttribute("data-copy-target")||"").trim();
      const ta=document.getElementById(id);
      const ok=!!(ta&&String(ta.value||"").trim());
      if(ok)btn.removeAttribute("disabled");
      else btn.setAttribute("disabled","");
    });
  }

  async function refreshFromIframe(){
    const doc=getIframeDoc(frame);
    if(!doc){
      setStatus("iframe読み込み中…（しばらく待ってください）");
      return false;
    }
    const full=textOrEmpty(doc.getElementById("ytFullDescPre"));
    const ep=textOrEmpty(doc.getElementById("ytEpisodeDescPre"));
    const ch=textOrEmpty(doc.getElementById("ytChannelDescPre"));
    if(fullTa)fullTa.value=full;
    if(epTa)epTa.value=ep;
    if(chTa)chTa.value=ch;

    const studioHref=String(doc.getElementById("openYtStudio")?.href||"").trim();
    setStudioHref(studioHref);

    const hasAny=!!(full||ep||ch);
    if(hasAny)setStatus("準備OK（コピーボタンを押してください）");
    else setStatus("概要欄が未生成/未表示です（Script Viewer側の「YouTube貼り付け」を確認）");
    updateCopyButtons();
    return hasAny;
  }

  panel.querySelectorAll("[data-copy-target]").forEach((btn)=>{
    btn.addEventListener("click",async()=>{
      const id=String(btn.getAttribute("data-copy-target")||"").trim();
      const ta=document.getElementById(id);
      const text=String(ta?.value||"");
      const labelById={descFull:"全文",descEpisode:"動画ごと",descChannel:"チャンネル固定"};
      const label=labelById[id]||id;
      try{
        const ok=await copyText(text);
        setStatus(ok?`コピーしました（${label}）`:`コピー失敗（${label}）`);
      }catch(_e){
        setStatus(`コピー失敗（${label}）`);
      }
    });
  });

  let tries=0;
  async function poll(){
    tries+=1;
    const ok=await refreshFromIframe();
    if(ok)return;
    if(tries>=60)return;
    window.setTimeout(poll,250);
  }
  frame.addEventListener("load",()=>{
    tries=0;
    poll();
  });
  poll();
}

document.addEventListener("DOMContentLoaded",()=>{
  setupEpJumpForm();
  setupEpisodeDescriptionCopy();
});
