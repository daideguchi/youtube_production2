#!/usr/bin/env python3
"""pages_episode_routes.py — GitHub Pages 用の「綺麗なURL」(path-based) を生成する

狙い:
- Script Viewer は `/?id=CH27-001&view=thumb` のような query で状態を持つため、共有URLが混雑しがち。
- GitHub Pages は rewrite が無いので、静的な wrapper HTML を生成して「外側URLを整理」する。

方針:
- wrapper は iframe で Script Viewer を表示する（中身のUI/UXは既存を維持）。
- 共有すべきURLは wrapper 側（/ep/...）に統一する。
- alt thumb は `docs/media/thumbs_alt/<variant>/<CHxx>/<NNN>.jpg` がある場合に生成する。

出力（Pages で配信される想定）:
- docs/ep/index.html
- docs/ep/CH27/index.html
- docs/ep/CH27/001/index.html
- docs/ep/CH27/001/audio/index.html
- docs/ep/CH27/001/thumb/index.html
- docs/ep/CH27/001/images/index.html
- docs/ep/CH27/001/thumb/<variant>/index.html（任意）
- docs/ep/CH27/thumb/<variant>/index.html（任意・ギャラリー）
- docs/ep/styles.css / docs/ep/app.js

Usage:
  python3 scripts/ops/pages_episode_routes.py --write --clean
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as fpaths  # noqa: E402


CHANNEL_RE = re.compile(r"^CH\d{2,3}$")
VIDEO_DIR_RE = re.compile(r"^\d+$")
VIDEO_RE_3 = re.compile(r"^\d{3}$")

# Cache-bust for docs/ep static assets (styles/app).
# Bump this string when updating /docs/ep UX.
EP_ASSET_VERSION = "20260113_06"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _channel_sort_key(channel: str) -> tuple[int, str]:
    m = re.match(r"^CH(\d+)$", channel)
    return (int(m.group(1)) if m else 10**9, channel)


def _discover_assembled_path(episode_dir: Path) -> Path | None:
    """Prefer `content/assembled_human.md`, fallback to `content/assembled.md`, then legacy `assembled.md`."""
    human = episode_dir / "content" / "assembled_human.md"
    if human.exists():
        return human
    candidate = episode_dir / "content" / "assembled.md"
    if candidate.exists():
        return candidate
    legacy = episode_dir / "assembled.md"
    if legacy.exists():
        return legacy
    return None


def _load_planning_titles(repo_root: Path) -> dict[tuple[str, int], str]:
    """Map (CHxx, video_number_int) -> title from Planning CSV."""
    out: dict[tuple[str, int], str] = {}
    planning_root = repo_root / "workspaces" / "planning" / "channels"
    if not planning_root.exists():
        return out

    for csv_path in sorted(planning_root.glob("CH*.csv")):
        channel = csv_path.stem
        if not CHANNEL_RE.match(channel):
            continue
        try:
            raw = csv_path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        try:
            reader = csv.DictReader(raw.splitlines())
        except Exception:
            continue
        if not reader.fieldnames:
            continue
        for row in reader:
            try:
                video_raw = (row.get("動画番号") or "").strip()
                if not video_raw:
                    continue
                video_num = int(video_raw)
            except Exception:
                continue
            title = (row.get("タイトル") or "").strip()
            if not title:
                continue
            out[(channel, video_num)] = title
    return out


def _load_channels_info(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Map channel_id -> metadata (from packages/script_pipeline/channels/channels_info.json)."""
    path = repo_root / "packages" / "script_pipeline" / "channels" / "channels_info.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ent in raw:
        if not isinstance(ent, dict):
            continue
        ch = str(ent.get("channel_id") or "").strip().upper()
        if not CHANNEL_RE.match(ch):
            continue
        out[ch] = ent
    return out


def _channel_display_name(meta: dict[str, Any] | None, channel_id: str) -> str:
    if not meta:
        return channel_id
    yt = meta.get("youtube") if isinstance(meta, dict) else None
    yt = yt if isinstance(yt, dict) else {}
    name = str(yt.get("title") or meta.get("name") or "").strip()
    return name or channel_id


def _channel_avatar_url(meta: dict[str, Any] | None) -> str:
    if not meta:
        return ""
    branding = meta.get("branding") if isinstance(meta, dict) else None
    branding = branding if isinstance(branding, dict) else {}
    url = str(branding.get("avatar_url") or "").strip()
    return url if url.startswith("http://") or url.startswith("https://") else ""


def _escape_html(text: str) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _rel_href(from_dir: Path, to_path: Path, *, is_dir: bool) -> str:
    rel = os.path.relpath(str(to_path), start=str(from_dir)).replace(os.sep, "/")
    if is_dir and not rel.endswith("/"):
        rel += "/"
    return rel


def _with_asset_version(href: str) -> str:
    raw = str(href or "").strip()
    if not raw:
        return raw
    v = str(EP_ASSET_VERSION or "").strip()
    if not v:
        return raw
    sep = "&" if "?" in raw else "?"
    return f"{raw}{sep}v={v}"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _styles_css() -> str:
    return """\
:root{color-scheme:light dark;--bg:#0b0d10;--fg:#e7eef7;--muted:#9bb0c6;--card:#131820;--border:rgba(255,255,255,.10);--accent:#4ea1ff}
*{box-sizing:border-box}
html,body{max-width:100%;overflow-x:hidden}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,"Noto Sans JP",sans-serif;background:var(--bg);color:var(--fg)}
a{color:inherit;text-decoration:none}
code{background:rgba(255,255,255,.06);padding:2px 6px;border-radius:8px;overflow-wrap:anywhere;word-break:break-word}
.wrap{max-width:1120px;margin:0 auto;padding:16px}
.header{display:flex;gap:12px;align-items:baseline;justify-content:space-between;flex-wrap:wrap}
.title{font-size:16px;font-weight:800;margin:0}
.muted{color:var(--muted);font-size:12px}
.links{display:flex;gap:10px;flex-wrap:wrap}
.btn{display:inline-block;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.04)}
.btn:hover{border-color:rgba(255,255,255,.22)}
.btn--accent{border-color:rgba(78,161,255,.55)}
.btn--accent:hover{border-color:rgba(78,161,255,.9)}
.btn[disabled]{opacity:.55;cursor:not-allowed}
.btn[aria-disabled="true"]{opacity:.55;pointer-events:none}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.tab{padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.02)}
.tab[aria-current="page"]{border-color:rgba(78,161,255,.8);background:rgba(78,161,255,.10)}
.panel{margin-top:12px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--card)}
.panel__head{padding:10px 12px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}
.panel__body{padding:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-top:12px}
.card{display:block;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--card)}
.card:hover{border-color:rgba(255,255,255,.22)}
.card img{width:100%;height:auto;display:block;background:#000}
.card .meta{padding:10px 12px 12px}
.card .id{font-weight:800;font-size:13px}
.card .desc{margin-top:6px;font-size:12px;color:var(--muted);line-height:1.35}
.card .sub{margin-top:8px;font-size:12px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.frame{width:100%;height:min(84vh,980px);border:0;background:#000;border-radius:12px}
input,select{padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:rgba(255,255,255,.03);color:var(--fg)}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:999px;border:1px solid var(--border);background:rgba(255,255,255,.03);font-size:12px;white-space:nowrap}
.badge--ok{border-color:rgba(110,168,255,.55)}
.badge--warn{border-color:rgba(255,199,92,.55)}
.badge--off{opacity:.75}
.channel-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin-top:12px}
.channel-card{display:flex;gap:12px;align-items:center;padding:12px;border:1px solid var(--border);border-radius:14px;background:var(--card)}
.channel-card:hover{border-color:rgba(255,255,255,.22)}
.channel-card__avatar{width:56px;height:56px;border-radius:999px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);overflow:hidden;display:flex;align-items:center;justify-content:center;font-weight:800;color:rgba(233,238,255,.85)}
.channel-card__avatar img{width:100%;height:100%;object-fit:cover;display:block}
.channel-card__meta{min-width:0}
.channel-card__name{font-weight:900}
.channel-card__name span{font-weight:700;color:var(--muted)}
.channel-card__counts{margin-top:6px;display:flex;gap:8px;flex-wrap:wrap}
.copy-grid{display:grid;grid-template-columns:1fr;gap:12px}
.copy-block{border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.02);overflow:hidden}
.copy-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border-bottom:1px solid var(--border)}
.copy-title{font-weight:800}
.copy-actions{display:flex;gap:8px;flex-wrap:wrap}
.textarea{width:100%;min-height:140px;resize:vertical;padding:12px;border:0;outline:none;background:transparent;color:var(--fg);font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;line-height:1.4;white-space:pre-wrap}
@media (min-width:960px){.copy-grid{grid-template-columns:1fr 1fr}.copy-block[data-kind="full"]{grid-column:1 / -1}}
"""


def _app_js() -> str:
    # Used by /ep/index.html and per-episode script pages.
    return """\
function normalizeChannel(raw){
  const s=String(raw||"").trim().toUpperCase();
  const m=s.match(/^CH(\\d{1,3})$/);
  if(m){
    const n=Number(m[1]);
    if(Number.isFinite(n))return `CH${String(n).padStart(2,"0")}`;
  }
  return s;
}
function normalizeVideo(raw){
  const s=String(raw||"").trim();
  if(/^\\d{3}$/.test(s))return s;
  const n=Number(s);
  if(Number.isFinite(n))return String(n).padStart(3,"0");
  return s;
}
function gotoEpisode(ch, video, view){
  const C=normalizeChannel(ch);
  const V=normalizeVideo(video);
  if(!/^CH\\d{2,3}$/.test(C)||!/^\\d{3}$/.test(V))return;
  let path=`./${C}/${V}/`;
  if(view&&view!=="script")path+=`${view}/`;
  location.href=path;
}

function textOrEmpty(el){
  const s=String((el?.innerText||el?.textContent||"")||"").replace(/\\r\\n/g,"\\n").trim();
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
    const norm=raw.toUpperCase().replace(/\\s+/g,"-");
    const m=norm.match(/^CH\\d{1,3}[-]?\\d{1,4}$/);
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
"""


def _page_shell(
    *,
    title: str,
    subtitle: str,
    styles_href: str,
    script_href: str | None,
    links_html: str,
    body_html: str,
) -> str:
    styles_tag_href = _escape_html(_with_asset_version(styles_href))
    script_tag = (
        f'<script defer src="{_escape_html(_with_asset_version(script_href))}"></script>' if script_href else ""
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"ja\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        f"    <title>{_escape_html(title)}</title>\n"
        f"    <link rel=\"stylesheet\" href=\"{styles_tag_href}\" />\n"
        f"    {script_tag}\n"
        "  </head>\n"
        "  <body>\n"
        "    <div class=\"wrap\">\n"
        "      <header class=\"header\">\n"
        "        <div>\n"
        f"          <h1 class=\"title\">{_escape_html(title)}</h1>\n"
        f"          <div class=\"muted\">{_escape_html(subtitle)}</div>\n"
        "        </div>\n"
        f"        <div class=\"links\">{links_html}</div>\n"
        "      </header>\n"
        f"      {body_html}\n"
        "    </div>\n"
        "  </body>\n"
        "</html>\n"
    )


@dataclass(frozen=True)
class EpisodeItem:
    channel: str
    video: str  # 3-digit
    video_int: int
    title: str | None
    assembled_rel: str | None

    @property
    def video_id(self) -> str:
        return f"{self.channel}-{self.video}"

    @property
    def has_script(self) -> bool:
        return bool(self.assembled_rel)


def _collect_episodes(repo_root: Path) -> list[EpisodeItem]:
    """
    Collect episodes for /ep pages.

    Policy:
    - Prefer snapshot (planning-first) so /ep includes planning-only channels too.
    - Determine script presence by checking for assembled.md under workspaces/scripts.
    """
    titles = _load_planning_titles(repo_root)
    docs_root = repo_root / "docs"
    snapshot_channels_path = docs_root / "data" / "snapshot" / "channels.json"
    scripts_root = fpaths.script_data_root()
    items: list[EpisodeItem] = []

    def add_episode(*, channel: str, video_int: int, title: str | None) -> None:
        video = f"{int(video_int):03d}"
        assembled_rel: str | None = None
        episode_dir = scripts_root / channel / video
        assembled = _discover_assembled_path(episode_dir) if episode_dir.exists() else None
        if assembled:
            try:
                assembled_rel = assembled.relative_to(repo_root).as_posix()
            except Exception:
                assembled_rel = None
        items.append(
            EpisodeItem(
                channel=channel,
                video=video,
                video_int=int(video_int),
                title=title,
                assembled_rel=assembled_rel,
            )
        )

    if snapshot_channels_path.exists():
        try:
            snap = json.loads(snapshot_channels_path.read_text(encoding="utf-8"))
            channels = snap.get("channels") if isinstance(snap, dict) else None
            channels_list = channels if isinstance(channels, list) else []
        except Exception:
            channels_list = []

        for ent in channels_list:
            if not isinstance(ent, dict):
                continue
            channel = str(ent.get("channel") or "").strip()
            if not CHANNEL_RE.match(channel):
                continue
            data_path = str(ent.get("data_path") or "").strip()
            if not data_path:
                continue
            ch_json_path = docs_root / str(data_path).lstrip("/").replace("\\", "/")
            if not ch_json_path.exists():
                continue
            try:
                ch_obj = json.loads(ch_json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            eps = ch_obj.get("episodes") if isinstance(ch_obj, dict) else None
            eps_list = eps if isinstance(eps, list) else []
            for ep in eps_list:
                if not isinstance(ep, dict):
                    continue
                try:
                    video_int = int(str(ep.get("video") or "").strip())
                except Exception:
                    continue
                title = str(ep.get("title") or "").strip() or titles.get((channel, video_int))
                add_episode(channel=channel, video_int=video_int, title=title)

        items.sort(key=lambda it: (_channel_sort_key(it.channel), it.video_int))
        return items

    # Fallback (no snapshot): scripts-only.
    if scripts_root.exists():
        for channel_dir in sorted([p for p in scripts_root.iterdir() if p.is_dir()], key=lambda p: _channel_sort_key(p.name)):
            channel = channel_dir.name
            if not CHANNEL_RE.match(channel):
                continue
            for episode_dir in sorted([p for p in channel_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
                video_raw = episode_dir.name
                if not VIDEO_DIR_RE.match(video_raw):
                    continue
                try:
                    video_int = int(video_raw)
                except Exception:
                    continue
                title = titles.get((channel, video_int))
                add_episode(channel=channel, video_int=video_int, title=title)

    items.sort(key=lambda it: (_channel_sort_key(it.channel), it.video_int))
    return items


def _discover_thumb_alt_variants(repo_root: Path) -> dict[str, dict[str, set[str]]]:
    """Return: {CHxx: {variant: {NNN,...}}} from docs/media/thumbs_alt."""
    out: dict[str, dict[str, set[str]]] = {}
    root = repo_root / "docs" / "media" / "thumbs_alt"
    if not root.exists():
        return out

    for variant_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        variant = variant_dir.name.strip()
        if not variant:
            continue
        for ch_dir in sorted([p for p in variant_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            ch = ch_dir.name.strip()
            if not CHANNEL_RE.match(ch):
                continue
            for img in ch_dir.glob("*.jpg"):
                v = img.stem.strip()
                if not VIDEO_RE_3.fullmatch(v):
                    continue
                out.setdefault(ch, {}).setdefault(variant, set()).add(v)
    return out


def _thumbs_alt_index_json(*, variants: dict[str, dict[str, set[str]]], updated_at: str) -> str:
    """JSON for Script Viewer: available thumb_alt variants/videos by channel."""
    out: dict[str, Any] = {"generated_at": updated_at, "channels": {}}
    ch_obj: dict[str, Any] = {}
    for ch in sorted(variants.keys(), key=_channel_sort_key):
        per_variant = variants.get(ch) or {}
        v_obj: dict[str, Any] = {}
        for variant in sorted(per_variant.keys()):
            vids = sorted(per_variant.get(variant) or set())
            v_obj[variant] = vids
        if v_obj:
            ch_obj[ch] = v_obj
    out["channels"] = ch_obj
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def _thumb_alt_label(variant: str) -> str:
    v = str(variant or "").strip()
    if not v:
        return "イラストサムネ"
    if v == "illust_v1":
        return "イラストサムネ（縦長）"
    if "illust" in v.lower():
        return f"イラストサムネ（{v}）"
    return f"thumb_alt:{v}"


def _preferred_thumb_alt_variant(*, variants: dict[str, set[str]], channel: str, video: str) -> str | None:
    """Pick a default thumb_alt variant (used for CH27 default illustration thumbs)."""
    available = sorted([v for v, vids in (variants or {}).items() if video in (vids or set())])
    if not available:
        return None
    for cand in ("illust_v1",):
        if cand in available:
            return cand
    for v in available:
        if "illust" in v.lower():
            return v
    return available[0]


def _standard_links(*, page_dir: Path, docs_root: Path, ep_root: Path, channel_dir: Path | None) -> str:
    ep_href = _rel_href(page_dir, ep_root, is_dir=True)
    viewer_href = _rel_href(page_dir, docs_root / "index.html", is_dir=False)
    snapshot_href = _rel_href(page_dir, docs_root / "snapshot", is_dir=True)
    guide_href = _rel_href(page_dir, docs_root / "guide", is_dir=True)

    parts = [f'<a class="btn btn--accent" href="{_escape_html(ep_href)}">ep</a>']
    if channel_dir is not None:
        ch_href = _rel_href(page_dir, channel_dir, is_dir=True)
        parts.append(f'<a class="btn" href="{_escape_html(ch_href)}">{_escape_html(channel_dir.name)}</a>')

    parts.append(f'<a class="btn" href="{_escape_html(viewer_href)}" target="_blank" rel="noreferrer">Script Viewer</a>')
    parts.append(f'<a class="btn" href="{_escape_html(snapshot_href)}" target="_blank" rel="noreferrer">snapshot</a>')
    parts.append(f'<a class="btn" href="{_escape_html(guide_href)}" target="_blank" rel="noreferrer">guide</a>')
    return "".join(parts)


def _ep_index_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channels: list[str],
    channel_meta: dict[str, dict[str, Any]],
    stats: dict[str, tuple[int, int]],
    updated_at: str,
) -> str:
    cards: list[str] = []
    for ch in channels:
        meta = channel_meta.get(ch)
        name = _channel_display_name(meta, ch)
        avatar = _channel_avatar_url(meta)
        scripts_n, plan_n = stats.get(ch, (0, 0))
        href = f"./{_escape_html(ch)}/"
        if avatar:
            av_html = (
                f'<div class="channel-card__avatar"><img loading="lazy" src="{_escape_html(avatar)}"'
                f' alt="{_escape_html(ch)} avatar" /></div>'
            )
        else:
            av_html = f'<div class="channel-card__avatar">{_escape_html(ch.replace("CH", ""))}</div>'
        counts_html = (
            f'<div class="channel-card__counts">'
            f'<span class="badge badge--ok">script {scripts_n}</span>'
            f'<span class="badge badge--off">plan {plan_n}</span>'
            f"</div>"
        )
        cards.append(
            f'<a class="channel-card" href="{href}">'
            f"{av_html}"
            f'<div class="channel-card__meta">'
            f'<div class="channel-card__name">{_escape_html(name)} <span>({ch})</span></div>'
            f"{counts_html}"
            f"</div>"
            f"</a>"
        )
    cards_block = '<div class="channel-grid">' + "".join(cards) + "</div>" if cards else '<span class="muted">no channels</span>'

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=None)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    script_href = _rel_href(page_dir, ep_root / "app.js", is_dir=False)

    body = (
        "<main>\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        "      <div><strong>直ジャンプ</strong> <span class=\"muted\">CHxx-001 で開く（綺麗なURL）</span></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        "      <form id=\"epJumpForm\" style=\"display:flex;gap:8px;flex-wrap:wrap;align-items:center\">\n"
        "        <input id=\"epJumpInput\" placeholder=\"例: CH27-001\" style=\"flex:1;min-width:220px\" />\n"
        "        <select id=\"epJumpView\">\n"
        "          <option value=\"script\">台本</option>\n"
        "          <option value=\"audio\">音声</option>\n"
        "          <option value=\"thumb\">サムネ</option>\n"
        "          <option value=\"images\">画像</option>\n"
        "        </select>\n"
        "        <button class=\"btn btn--accent\" type=\"submit\">開く</button>\n"
        "      </form>\n"
        "      <div class=\"muted\" style=\"margin-top:10px\">URLルール: <code>/ep/CH27/001/</code> / <code>/ep/CH27/001/thumb/</code> など</div>\n"
        "      <div class=\"muted\" style=\"margin-top:10px\">※ /ep は共有用（綺麗URL）。制作作業（台本/概要欄コピー・チャンネル情報確認・企画/進捗）は Script Viewer を使ってください。</div>\n"
        "    </div>\n"
        "  </div>\n\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\"><div><strong>チャンネル一覧</strong></div><div class=\"muted\">/ep/CHxx/</div></div>\n"
        f"    <div class=\"panel__body\">{cards_block}</div>\n"
        "  </div>\n"
        "</main>\n"
    )

    return _page_shell(
        title="ep — clean URLs",
        subtitle="モバイルで迷わない: /ep/CHxx/NNN/（台本・サムネ・画像の入口）",
        styles_href=styles_href,
        script_href=script_href,
        links_html=links_html,
        body_html=body,
    )


def _channel_index_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel: str,
    episodes: list[EpisodeItem],
    channel_meta: dict[str, Any] | None,
    video_images_count_by_video_id: dict[str, int],
    updated_at: str,
    variants: dict[str, set[str]],
) -> str:
    cards: list[str] = []
    for it in episodes:
        thumb_path = docs_root / "media" / "thumbs" / it.channel / f"{it.video}.jpg"
        if it.channel == "CH27":
            variant = _preferred_thumb_alt_variant(variants=variants, channel=it.channel, video=it.video)
            if variant:
                thumb_path = docs_root / "media" / "thumbs_alt" / variant / it.channel / f"{it.video}.jpg"
        thumb_href = _rel_href(page_dir, thumb_path, is_dir=False)
        img_count = int(video_images_count_by_video_id.get(it.video_id, 0) or 0)
        script_badge = (
            '<span class="badge badge--ok">script ✓</span>' if it.has_script else '<span class="badge badge--off">script —</span>'
        )
        images_badge = (
            f'<span class="badge badge--ok">画像 {img_count}</span>'
            if img_count > 0
            else '<span class="badge badge--off">画像 —</span>'
        )
        cards.append(
            f'<a class="card" href="./{_escape_html(it.video)}/">'
            f'<img loading="lazy" src="{_escape_html(thumb_href)}" alt="{_escape_html(it.video_id)} thumb" />'
            f'<div class="meta"><div class="id">{_escape_html(it.video_id)}</div>'
            f'<div class="desc">{_escape_html(it.title or "")}</div>'
            f'<div class="sub">{script_badge}{images_badge}</div>'
            f"</div></a>"
        )
    variant_links = [
        f'<a class="btn" href="./thumb/{_escape_html(v)}/">{_escape_html(_thumb_alt_label(v))} 一覧</a>'
        for v in sorted(variants.keys())
    ]
    variant_block = (
        f"      <div style=\"margin-top:10px;display:flex;gap:10px;flex-wrap:wrap\">{''.join(variant_links)}</div>\n"
        if variant_links
        else ""
    )

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=None)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    display = _channel_display_name(channel_meta, channel)
    avatar = _channel_avatar_url(channel_meta)
    header_line = f"{_escape_html(display)} ({_escape_html(channel)})" if display and display != channel else _escape_html(channel)
    avatar_html = (
        f'<div class="channel-card__avatar"><img loading="lazy" src="{_escape_html(avatar)}" alt="{_escape_html(channel)} avatar" /></div>'
        if avatar
        else ""
    )
    script_n = sum(1 for it in episodes if it.has_script)
    plan_n = len(episodes)

    body = (
        "<main>\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        f"      <div style=\"display:flex;gap:10px;align-items:center\">{avatar_html}<div><strong>{header_line}</strong> <span class=\"muted\">script {script_n}/{plan_n}</span></div></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        f"      <div class=\"muted\">URL例: <code>/ep/{_escape_html(channel)}/001/thumb/</code></div>\n"
        f"{variant_block}"
        "    </div>\n"
        "  </div>\n"
        f"  <div class=\"grid\">{' '.join(cards) if cards else '<span class=\"muted\">no episodes</span>'}</div>\n"
        "</main>\n"
    )

    return _page_shell(
        title=f"{channel} — ep",
        subtitle="episode一覧（サムネ付き / 綺麗なURL）",
        styles_href=styles_href,
        script_href=None,
        links_html=links_html,
        body_html=body,
    )


def _episode_tabs_html(*, page_dir: Path, ep_base_dir: Path, active_key: str, variants: list[str]) -> str:
    def tab(label: str, target_dir: Path, key: str) -> str:
        href = _rel_href(page_dir, target_dir, is_dir=True)
        cur = ' aria-current="page"' if active_key == key else ""
        return f'<a class="tab" href="{_escape_html(href)}"{cur}>{_escape_html(label)}</a>'

    script_dir = ep_base_dir
    audio_dir = ep_base_dir / "audio"
    thumb_dir = ep_base_dir / "thumb"
    images_dir = ep_base_dir / "images"

    out = [
        tab("台本", script_dir, "script"),
        tab("音声", audio_dir, "audio"),
        tab("サムネ", thumb_dir, "thumb"),
        tab("画像", images_dir, "images"),
    ]
    for variant in variants:
        out.append(tab(_thumb_alt_label(variant), thumb_dir / variant, f"thumb:{variant}"))
    return '<nav class="tabs">' + "".join(out) + "</nav>"


def _episode_viewer_page_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel_dir: Path,
    ep_base_dir: Path,
    channel: str,
    video: str,
    title: str | None,
    view: str,
    updated_at: str,
    variants: list[str],
) -> str:
    vid = f"{channel}-{video}"
    view_label_map = {"script": "台本", "audio": "音声", "thumb": "サムネ", "images": "画像"}
    view_label = view_label_map.get(view, view)

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=channel_dir)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    tabs = _episode_tabs_html(page_dir=page_dir, ep_base_dir=ep_base_dir, active_key=view, variants=variants)

    docs_dir_href = _rel_href(page_dir, docs_root, is_dir=True)
    viewer_src = f"{docs_dir_href}?id={vid}&view={view}&embed=1&sv={EP_ASSET_VERSION}"

    desc_panel = ""
    script_href = None
    if view == "script":
        script_href = _rel_href(page_dir, ep_root / "app.js", is_dir=False)
        desc_panel = (
            "  <div class=\"panel\" id=\"descPanel\">\n"
            "    <div class=\"panel__head\">\n"
            "      <div><strong>概要欄</strong> <span class=\"muted\">YouTube貼り付け用</span></div>\n"
            "      <div class=\"copy-actions\">"
            "<a class=\"btn\" id=\"descStudioLink\" href=\"#\" target=\"_blank\" rel=\"noreferrer\">YouTube Studio</a>"
            "<button class=\"btn btn--accent\" type=\"button\" data-copy-target=\"descFull\" disabled>全文コピー</button>"
            "</div>\n"
            "    </div>\n"
            "    <div class=\"panel__body\">\n"
            "      <div class=\"muted\">手順: 「全文コピー」→ YouTube Studio の説明欄へ貼り付け</div>\n"
            "      <div class=\"copy-grid\" style=\"margin-top:10px\">\n"
            "        <div class=\"copy-block\" data-kind=\"full\">\n"
            "          <div class=\"copy-head\"><div class=\"copy-title\">全文（動画 + チャンネル固定文）</div>"
            "<div class=\"copy-actions\"><button class=\"btn btn--accent\" type=\"button\" data-copy-target=\"descFull\" disabled>コピー</button></div></div>\n"
            "          <textarea id=\"descFull\" class=\"textarea\" rows=\"10\" readonly placeholder=\"読み込み中…\"></textarea>\n"
            "        </div>\n"
            "        <div class=\"copy-block\" data-kind=\"episode\">\n"
            "          <div class=\"copy-head\"><div class=\"copy-title\">動画ごと</div>"
            "<div class=\"copy-actions\"><button class=\"btn\" type=\"button\" data-copy-target=\"descEpisode\" disabled>コピー</button></div></div>\n"
            "          <textarea id=\"descEpisode\" class=\"textarea\" rows=\"8\" readonly placeholder=\"—\"></textarea>\n"
            "        </div>\n"
            "        <div class=\"copy-block\" data-kind=\"channel\">\n"
            "          <div class=\"copy-head\"><div class=\"copy-title\">チャンネル固定</div>"
            "<div class=\"copy-actions\"><button class=\"btn\" type=\"button\" data-copy-target=\"descChannel\" disabled>コピー</button></div></div>\n"
            "          <textarea id=\"descChannel\" class=\"textarea\" rows=\"8\" readonly placeholder=\"—\"></textarea>\n"
            "        </div>\n"
            "      </div>\n"
            "      <div class=\"muted\" id=\"descStatus\" style=\"margin-top:10px\">読み込み中…</div>\n"
            "    </div>\n"
            "  </div>\n"
        )

    body = (
        "<main>\n"
        f"  {tabs}\n"
        f"{desc_panel}"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        f"      <div><strong>{_escape_html(vid)}</strong> <span class=\"muted\">{_escape_html(view_label)}</span></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        f"      <iframe class=\"frame\" src=\"{_escape_html(viewer_src)}\" loading=\"lazy\" referrerpolicy=\"no-referrer\"></iframe>\n"
        "    </div>\n"
        "  </div>\n"
        "</main>\n"
    )

    return _page_shell(
        title=f"{vid} — {view}",
        subtitle=str(title or "—"),
        styles_href=styles_href,
        script_href=script_href,
        links_html=links_html,
        body_html=body,
    )


def _episode_thumb_page_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel_dir: Path,
    ep_base_dir: Path,
    channel: str,
    video: str,
    title: str | None,
    updated_at: str,
    variants: list[str],
) -> str:
    vid = f"{channel}-{video}"

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=channel_dir)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    tabs = _episode_tabs_html(page_dir=page_dir, ep_base_dir=ep_base_dir, active_key="thumb", variants=variants)

    normal_img_path = docs_root / "media" / "thumbs" / channel / f"{video}.jpg"
    normal_img_href = _rel_href(page_dir, normal_img_path, is_dir=False)
    img_path = normal_img_path
    img_href = normal_img_href
    alt_img_href = ""
    chosen_variant = ""
    if channel == "CH27" and variants:
        # Prefer illust_v1 when available.
        chosen_variant = "illust_v1" if "illust_v1" in variants else variants[0]
        alt_img_path = docs_root / "media" / "thumbs_alt" / chosen_variant / channel / f"{video}.jpg"
        alt_img_href = _rel_href(page_dir, alt_img_path, is_dir=False)
        img_path = alt_img_path
        img_href = alt_img_href

    viewer_href = _rel_href(page_dir, docs_root, is_dir=True) + f"?id={vid}&view=thumb"

    primary_label = "通常サムネ（横長）"
    if alt_img_href and chosen_variant:
        primary_label = _thumb_alt_label(chosen_variant)

    normal_btn = ""
    if alt_img_href:
        normal_btn = f"        <a class=\"btn\" href=\"{_escape_html(normal_img_href)}\" target=\"_blank\" rel=\"noreferrer\">通常サムネ（横長）</a>\n"

    gal_btn = ""
    if alt_img_href and chosen_variant:
        gal_href = _rel_href(page_dir, channel_dir / "thumb" / chosen_variant, is_dir=True)
        gal_btn = f"        <a class=\"btn\" href=\"{_escape_html(gal_href)}\">イラスト一覧</a>\n"

    body = (
        "<main>\n"
        f"  {tabs}\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        f"      <div><strong>{_escape_html(vid)}</strong> <span class=\"muted\">サムネ</span></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        f"      <a href=\"{_escape_html(img_href)}\" target=\"_blank\" rel=\"noreferrer\">\n"
        f"        <img loading=\"lazy\" src=\"{_escape_html(img_href)}\" alt=\"{_escape_html(vid)} thumb\" style=\"width:100%;height:auto;border-radius:12px;border:1px solid var(--border);background:#000\" />\n"
        "      </a>\n"
        "      <div style=\"margin-top:10px;display:flex;gap:10px;flex-wrap:wrap\">\n"
        f"        <a class=\"btn btn--accent\" href=\"{_escape_html(img_href)}\" target=\"_blank\" rel=\"noreferrer\">{_escape_html(primary_label)} を開く（DL）</a>\n"
        f"{normal_btn}"
        f"{gal_btn}"
        f"        <a class=\"btn\" href=\"{_escape_html(viewer_href)}\" target=\"_blank\" rel=\"noreferrer\">Script Viewer</a>\n"
        "      </div>\n"
        "    </div>\n"
        "  </div>\n"
        "</main>\n"
    )

    return _page_shell(
        title=f"{vid} — thumb",
        subtitle=str(title or "—"),
        styles_href=styles_href,
        script_href=None,
        links_html=links_html,
        body_html=body,
    )


def _episode_images_page_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel_dir: Path,
    ep_base_dir: Path,
    channel: str,
    video: str,
    title: str | None,
    updated_at: str,
    variants: list[str],
    video_images_entry: dict[str, Any] | None,
) -> str:
    vid = f"{channel}-{video}"

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=channel_dir)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    tabs = _episode_tabs_html(page_dir=page_dir, ep_base_dir=ep_base_dir, active_key="images", variants=variants)

    viewer_href = _rel_href(page_dir, docs_root, is_dir=True) + f"?id={vid}&view=images"

    files = video_images_entry.get("files") if isinstance(video_images_entry, dict) else None
    files_list = files if isinstance(files, list) else []
    run_id = str(video_images_entry.get("run_id") or "").strip() if isinstance(video_images_entry, dict) else ""

    if files_list:
        cards: list[str] = []
        for f in files_list:
            if not isinstance(f, dict):
                continue
            rel = str(f.get("rel") or "").strip()
            if not rel:
                continue
            img_path = docs_root / rel
            img_href = _rel_href(page_dir, img_path, is_dir=False)
            summary = str(f.get("summary") or "").strip()
            sub = f'<div class="desc">{_escape_html(summary)}</div>' if summary else ""
            cards.append(
                f'<a class="card" href="{_escape_html(img_href)}" target="_blank" rel="noreferrer">'
                f'<img loading="lazy" src="{_escape_html(img_href)}" alt="{_escape_html(vid)} image" />'
                f'<div class="meta"><div class="id">{_escape_html(vid)}</div>{sub}</div></a>'
            )
        fallback = '<span class="muted">no images</span>'
        grid_inner = " ".join(cards) if cards else fallback
        grid_html = f'<div class="grid">{grid_inner}</div>'
        hint = (
            f'<div class="muted">run_id: <code>{_escape_html(run_id or "-")}</code></div>'
            if run_id
            else '<div class="muted">run_id: —</div>'
        )
        body_inner = (
            "  <div class=\"panel\">\n"
            "    <div class=\"panel__head\">\n"
            f"      <div><strong>{_escape_html(vid)}</strong> <span class=\"muted\">画像</span></div>\n"
            f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
            "    </div>\n"
            f"    <div class=\"panel__body\">{hint}</div>\n"
            "  </div>\n"
            f"  {grid_html}\n"
        )
    else:
        body_inner = (
            "  <div class=\"panel\">\n"
            "    <div class=\"panel__head\">\n"
            f"      <div><strong>{_escape_html(vid)}</strong> <span class=\"muted\">画像</span></div>\n"
            f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
            "    </div>\n"
            "    <div class=\"panel__body\">\n"
            "      <div class=\"muted\">動画内画像プレビューは未生成（またはrunが未作成）です。</div>\n"
            f"      <div class=\"muted\" style=\"margin-top:10px\">次: <code>python3 scripts/ops/pages_video_images_previews.py --channel {channel} --video {video} --write</code></div>\n"
            "      <div style=\"margin-top:10px;display:flex;gap:10px;flex-wrap:wrap\">\n"
            f"        <a class=\"btn\" href=\"{_escape_html(viewer_href)}\" target=\"_blank\" rel=\"noreferrer\">Script Viewer</a>\n"
            "      </div>\n"
            "    </div>\n"
            "  </div>\n"
        )

    body = "<main>\n" + f"  {tabs}\n" + body_inner + "</main>\n"

    return _page_shell(
        title=f"{vid} — images",
        subtitle=str(title or "—"),
        styles_href=styles_href,
        script_href=None,
        links_html=links_html,
        body_html=body,
    )


def _episode_thumb_alt_page_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel_dir: Path,
    ep_base_dir: Path,
    channel: str,
    video: str,
    title: str | None,
    updated_at: str,
    variants: list[str],
    variant: str,
) -> str:
    vid = f"{channel}-{video}"

    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=channel_dir)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    tabs = _episode_tabs_html(page_dir=page_dir, ep_base_dir=ep_base_dir, active_key=f"thumb:{variant}", variants=variants)

    img_path = docs_root / "media" / "thumbs_alt" / variant / channel / f"{video}.jpg"
    img_href = _rel_href(page_dir, img_path, is_dir=False)

    viewer_href = _rel_href(page_dir, docs_root, is_dir=True) + f"?id={vid}&view=thumb"
    normal_href = _rel_href(page_dir, ep_base_dir / "thumb", is_dir=True)

    body = (
        "<main>\n"
        f"  {tabs}\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        f"      <div><strong>{_escape_html(vid)}</strong> <span class=\"muted\">{_escape_html(_thumb_alt_label(variant))}</span></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        f"      <a href=\"{_escape_html(img_href)}\" target=\"_blank\" rel=\"noreferrer\">\n"
        f"        <img src=\"{_escape_html(img_href)}\" alt=\"{_escape_html(vid)} {_escape_html(variant)}\" style=\"width:100%;height:auto;border-radius:12px;border:1px solid var(--border);background:#000\" />\n"
        "      </a>\n"
        "      <div style=\"margin-top:10px;display:flex;gap:10px;flex-wrap:wrap\">\n"
        f"        <a class=\"btn btn--accent\" href=\"{_escape_html(img_href)}\" target=\"_blank\" rel=\"noreferrer\">画像を開く（DL）</a>\n"
        f"        <a class=\"btn\" href=\"{_escape_html(normal_href)}\">サムネ（通常/既定）</a>\n"
        f"        <a class=\"btn\" href=\"{_escape_html(viewer_href)}\" target=\"_blank\" rel=\"noreferrer\">Script Viewer</a>\n"
        "      </div>\n"
        "    </div>\n"
        "  </div>\n"
        "</main>\n"
    )

    return _page_shell(
        title=f"{vid} — thumb:{variant}",
        subtitle=str(title or "—"),
        styles_href=styles_href,
        script_href=None,
        links_html=links_html,
        body_html=body,
    )


def _variant_gallery_html(
    *,
    page_dir: Path,
    docs_root: Path,
    ep_root: Path,
    channel_dir: Path,
    channel: str,
    variant: str,
    videos: list[str],
    titles_by_video: dict[str, str],
    updated_at: str,
) -> str:
    links_html = _standard_links(page_dir=page_dir, docs_root=docs_root, ep_root=ep_root, channel_dir=channel_dir)
    styles_href = _rel_href(page_dir, ep_root / "styles.css", is_dir=False)
    label = _thumb_alt_label(variant)

    cards = []
    for v in videos:
        img_path = docs_root / "media" / "thumbs_alt" / variant / channel / f"{v}.jpg"
        img_href = _rel_href(page_dir, img_path, is_dir=False)
        ep_href = _rel_href(page_dir, channel_dir / v / "thumb" / variant, is_dir=True)
        cards.append(
            f'<a class="card" href="{_escape_html(ep_href)}">'
            f'<img loading="lazy" src="{_escape_html(img_href)}" alt="{_escape_html(channel)}-{_escape_html(v)} {_escape_html(variant)}" />'
            f'<div class="meta"><div class="id">{_escape_html(channel)}-{_escape_html(v)}</div><div class="desc">{_escape_html(titles_by_video.get(v, ""))}</div></div></a>'
        )

    body = (
        "<main>\n"
        "  <div class=\"panel\">\n"
        "    <div class=\"panel__head\">\n"
        f"      <div><strong>{_escape_html(channel)}</strong> <span class=\"muted\">{_escape_html(label)} · {len(videos)}枚</span></div>\n"
        f"      <div class=\"muted\">updated_at: {updated_at}</div>\n"
        "    </div>\n"
        "    <div class=\"panel__body\">\n"
        f"      <div class=\"muted\">URL: <code>/ep/{_escape_html(channel)}/thumb/{_escape_html(variant)}/</code></div>\n"
        "    </div>\n"
        "  </div>\n"
        f"  <div class=\"grid\">{' '.join(cards) if cards else '<span class=\"muted\">no thumbs</span>'}</div>\n"
        "</main>\n"
    )

    return _page_shell(
        title=f"{channel} — {label}",
        subtitle="イラストサムネ一覧（綺麗なURL）",
        styles_href=styles_href,
        script_href=None,
        links_html=links_html,
        body_html=body,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate clean episode routes under docs/ep for GitHub Pages.")
    ap.add_argument("--write", action="store_true", help="Write docs/ep pages (default: dry-run)")
    ap.add_argument("--clean", action="store_true", help="Delete docs/ep before writing (recommended in CI)")
    args = ap.parse_args()

    repo_root = fpaths.repo_root()
    docs_root = repo_root / "docs"
    ep_root = docs_root / "ep"

    updated_at = _now_iso_utc()

    episodes = _collect_episodes(repo_root)
    if not episodes:
        print("[pages_episode_routes] no episodes found.")
        return 0

    channel_meta_by_id = _load_channels_info(repo_root)

    video_images_by_video_id: dict[str, dict[str, Any]] = {}
    video_images_count_by_video_id: dict[str, int] = {}
    video_images_index_path = docs_root / "data" / "video_images_index.json"
    if video_images_index_path.exists():
        try:
            vi = json.loads(video_images_index_path.read_text(encoding="utf-8"))
            vi_items = vi.get("items") if isinstance(vi, dict) else None
            vi_list = vi_items if isinstance(vi_items, list) else []
            for ent in vi_list:
                if not isinstance(ent, dict):
                    continue
                vid = str(ent.get("video_id") or "").strip()
                if not vid:
                    continue
                video_images_by_video_id[vid] = ent
                files = ent.get("files") if isinstance(ent.get("files"), list) else []
                video_images_count_by_video_id[vid] = len(files)
        except Exception:
            video_images_by_video_id = {}
            video_images_count_by_video_id = {}

    by_channel: dict[str, list[EpisodeItem]] = {}
    titles_by_channel_video: dict[str, dict[str, str]] = {}
    for it in episodes:
        by_channel.setdefault(it.channel, []).append(it)
        titles_by_channel_video.setdefault(it.channel, {})[it.video] = str(it.title or "")

    channels = sorted(by_channel.keys(), key=_channel_sort_key)
    stats = {ch: (sum(1 for it in (by_channel.get(ch) or []) if it.has_script), len(by_channel.get(ch, []) or [])) for ch in channels}
    alt_variants = _discover_thumb_alt_variants(repo_root)

    if not args.write:
        print(
            f"[pages_episode_routes] DRY episodes={len(episodes)} channels={len(channels)} out={ep_root} "
            f"thumb_alt_channels={len(alt_variants)}"
        )
        return 0

    if args.clean and ep_root.exists():
        shutil.rmtree(ep_root)

    data_root = docs_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(data_root / "thumbs_alt_index.json", _thumbs_alt_index_json(variants=alt_variants, updated_at=updated_at))

    _write_text_atomic(ep_root / "styles.css", _styles_css())
    _write_text_atomic(ep_root / "app.js", _app_js())

    # /ep/
    _write_text_atomic(
        ep_root / "index.html",
        _ep_index_html(
            page_dir=ep_root,
            docs_root=docs_root,
            ep_root=ep_root,
            channels=channels,
            channel_meta=channel_meta_by_id,
            stats=stats,
            updated_at=updated_at,
        ),
    )

    for ch in channels:
        ch_dir = ep_root / ch
        ch_dir.mkdir(parents=True, exist_ok=True)
        ch_eps = by_channel.get(ch, [])

        # /ep/CHxx/
        _write_text_atomic(
            ch_dir / "index.html",
            _channel_index_html(
                page_dir=ch_dir,
                docs_root=docs_root,
                ep_root=ep_root,
                channel=ch,
                episodes=ch_eps,
                channel_meta=channel_meta_by_id.get(ch),
                video_images_count_by_video_id=video_images_count_by_video_id,
                updated_at=updated_at,
                variants=alt_variants.get(ch, {}),
            ),
        )

        # /ep/CHxx/thumb/<variant>/
        for variant, vids in sorted((alt_variants.get(ch) or {}).items(), key=lambda kv: kv[0]):
            gal_dir = ch_dir / "thumb" / variant
            vids_sorted = sorted(vids, key=lambda s: int(s))
            _write_text_atomic(
                gal_dir / "index.html",
                _variant_gallery_html(
                    page_dir=gal_dir,
                    docs_root=docs_root,
                    ep_root=ep_root,
                    channel_dir=ch_dir,
                    channel=ch,
                    variant=variant,
                    videos=vids_sorted,
                    titles_by_video=titles_by_channel_video.get(ch, {}),
                    updated_at=updated_at,
                ),
            )

        for it in ch_eps:
            ep_base_dir = ch_dir / it.video
            ep_base_dir.mkdir(parents=True, exist_ok=True)

            episode_variants = [v for v, vids in sorted((alt_variants.get(ch) or {}).items()) if it.video in vids]

            # script: iframe (copy-friendly)
            _write_text_atomic(
                ep_base_dir / "index.html",
                _episode_viewer_page_html(
                    page_dir=ep_base_dir,
                    docs_root=docs_root,
                    ep_root=ep_root,
                    channel_dir=ch_dir,
                    ep_base_dir=ep_base_dir,
                    channel=it.channel,
                    video=it.video,
                    title=it.title,
                    view="script",
                    updated_at=updated_at,
                    variants=episode_variants,
                ),
            )

            # audio: keep iframe (text artifacts live in Script Viewer)
            audio_dir = ep_base_dir / "audio"
            _write_text_atomic(
                audio_dir / "index.html",
                _episode_viewer_page_html(
                    page_dir=audio_dir,
                    docs_root=docs_root,
                    ep_root=ep_root,
                    channel_dir=ch_dir,
                    ep_base_dir=ep_base_dir,
                    channel=it.channel,
                    video=it.video,
                    title=it.title,
                    view="audio",
                    updated_at=updated_at,
                    variants=episode_variants,
                ),
            )

            # thumb/images: direct assets (downloadable)
            thumb_dir = ep_base_dir / "thumb"
            _write_text_atomic(
                thumb_dir / "index.html",
                _episode_thumb_page_html(
                    page_dir=thumb_dir,
                    docs_root=docs_root,
                    ep_root=ep_root,
                    channel_dir=ch_dir,
                    ep_base_dir=ep_base_dir,
                    channel=it.channel,
                    video=it.video,
                    title=it.title,
                    updated_at=updated_at,
                    variants=episode_variants,
                ),
            )
            images_dir = ep_base_dir / "images"
            _write_text_atomic(
                images_dir / "index.html",
                _episode_images_page_html(
                    page_dir=images_dir,
                    docs_root=docs_root,
                    ep_root=ep_root,
                    channel_dir=ch_dir,
                    ep_base_dir=ep_base_dir,
                    channel=it.channel,
                    video=it.video,
                    title=it.title,
                    updated_at=updated_at,
                    variants=episode_variants,
                    video_images_entry=video_images_by_video_id.get(it.video_id),
                ),
            )

            # thumb variants (alt)
            for variant in episode_variants:
                var_dir = ep_base_dir / "thumb" / variant
                _write_text_atomic(
                    var_dir / "index.html",
                    _episode_thumb_alt_page_html(
                        page_dir=var_dir,
                        docs_root=docs_root,
                        ep_root=ep_root,
                        channel_dir=ch_dir,
                        ep_base_dir=ep_base_dir,
                        channel=it.channel,
                        video=it.video,
                        title=it.title,
                        updated_at=updated_at,
                        variants=episode_variants,
                        variant=variant,
                    ),
                )

    print(f"[pages_episode_routes] wrote {ep_root} (episodes={len(episodes)} channels={len(channels)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
