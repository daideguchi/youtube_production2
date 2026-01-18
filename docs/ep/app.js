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

function setupEpisodeDescriptionCopy(){
  const panel=document.getElementById("descPanel");
  if(!panel)return;

  const status=document.getElementById("descStatus");
  const studioLink=document.getElementById("descStudioLink");
  const titleTa=document.getElementById("ytTitle");
  const tagsTa=document.getElementById("ytTags");
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

  panel.querySelectorAll("[data-copy-target]").forEach((btn)=>{
    btn.addEventListener("click",async()=>{
      const id=String(btn.getAttribute("data-copy-target")||"").trim();
      const ta=document.getElementById(id);
      const text=String(ta?.value||"");
      const labelById={ytTitle:"タイトル",descFull:"概要欄",ytTags:"タグ",descEpisode:"概要欄(動画ごと)",descChannel:"概要欄(チャンネル固定)"};
      const label=labelById[id]||id;
      try{
        const ok=await copyText(text);
        setStatus(ok?`コピーしました（${label}）`:`コピー失敗（${label}）`);
      }catch(_e){
        setStatus(`コピー失敗（${label}）`);
      }
    });
  });

  // HTML側で textarea は埋め込み済み。JSは「コピー導線」だけ担当する。
  updateCopyButtons();
  setStudioHref(String(studioLink?.getAttribute("href")||"").trim().replace(/^#$/,""));
  const hasAny=!!(String(titleTa?.value||"").trim()||String(fullTa?.value||"").trim()||String(tagsTa?.value||"").trim());
  setStatus(hasAny?"準備OK（①タイトル→②概要欄→③タグ をコピーして貼り付け）":"概要欄が空です（Planningを確認してください）");
}

function stripScriptSeparators(text){
  const lines=String(text||"").replace(/\r\n/g,"\n").split("\n");
  const out=[];
  for(const line of lines){
    if(String(line).trim()==="---")continue;
    out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g,"\n\n");
}

function setupEpisodeScriptCopy(){
  const pre=document.querySelector("pre.pre");
  if(!pre)return;

  const currentTab=document.querySelector("nav.tabs a[aria-current='page']");
  if(currentTab){
    const label=String(currentTab.textContent||"").trim();
    if(label&&label!=="台本")return;
  }

  if(document.getElementById("scriptCopyActions"))return;

  const actions=document.createElement("div");
  actions.id="scriptCopyActions";
  actions.className="copy-actions";
  actions.style.marginTop="12px";

  const btnRaw=document.createElement("button");
  btnRaw.type="button";
  btnRaw.className="btn btn--accent";
  btnRaw.textContent="台本コピー";

  const btnNoSep=document.createElement("button");
  btnNoSep.type="button";
  btnNoSep.className="btn";
  btnNoSep.textContent="台本コピー（---なし）";

  const status=document.createElement("div");
  status.id="scriptCopyStatus";
  status.className="muted";
  status.style.marginTop="8px";
  status.textContent="台本コピー: ボタンを押してください";

  actions.appendChild(btnRaw);
  actions.appendChild(btnNoSep);
  pre.parentNode?.insertBefore(actions, pre);
  pre.parentNode?.insertBefore(status, pre);

  function setStatus(msg){
    status.textContent=String(msg||"");
  }

  btnRaw.addEventListener("click", async()=>{
    try{
      const ok=await copyText(pre.textContent||"");
      setStatus(ok?"コピーしました（台本）":"コピー失敗（台本）");
    }catch(_e){
      setStatus("コピー失敗（台本）");
    }
  });

  btnNoSep.addEventListener("click", async()=>{
    try{
      const stripped=stripScriptSeparators(pre.textContent||"");
      const ok=await copyText(stripped);
      setStatus(ok?"コピーしました（台本・---なし）":"コピー失敗（台本・---なし）");
    }catch(_e){
      setStatus("コピー失敗（台本・---なし）");
    }
  });
}

document.addEventListener("DOMContentLoaded",()=>{
  setupEpJumpForm();
  setupEpisodeDescriptionCopy();
  setupEpisodeScriptCopy();
});
