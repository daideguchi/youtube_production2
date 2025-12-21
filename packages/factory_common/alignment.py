from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


ALIGNMENT_SCHEMA = "ytm.alignment.v1"

_THUMB_CATCH_RE = re.compile(r"『([^』]+)』")
_TOKEN_RE = re.compile(r"[一-龯]{2,}|[ぁ-ん]{2,}|[ァ-ヴー]{2,}|[A-Za-z0-9]{2,}")

# Conservative stopwords to avoid over-reporting mismatches.
_STOPWORDS = {
    "あなた",
    "人生",
    "本当",
    "方法",
    "理由",
    "今",
    "今日",
    "すぐ",
    "なぜ",
    "どう",
    "それ",
    "これ",
    "そして",
    "しかし",
    "ため",
    "人",
    "人間",
    "心",
    "世界",
    "自分",
    "自分自身",
    "私",
    "僕",
    "私たち",
    "結局",
    "大事",
    "重要",
    "知る",
    "知ら",
    "できる",
    "して",
    "いる",
    "ある",
    "ない",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha1_text(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def sha1_json(payload: Dict[str, Any]) -> str:
    return sha1_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_thumbnail_catch(prompt: str | None) -> Optional[str]:
    text = (prompt or "").strip()
    if not text:
        return None
    first = text.splitlines()[0].strip()
    if not first:
        return None
    m = _THUMB_CATCH_RE.search(first)
    if not m:
        return None
    catch = (m.group(1) or "").strip()
    return catch or None


def iter_thumbnail_catches_from_row(row: Dict[str, Any]) -> Iterable[str]:
    cols = (
        "サムネ画像プロンプト（URL・テキスト指示込み）",
        "DALL-Eプロンプト（URL・テキスト指示込み）",
        "サムネ用DALL-Eプロンプト（URL・テキスト指示込み）",
    )
    for col in cols:
        catch = extract_thumbnail_catch(str(row.get(col) or ""))
        if catch:
            yield catch


def select_thumbnail_catch(row: Dict[str, Any]) -> Optional[str]:
    catches = list(iter_thumbnail_catches_from_row(row))
    if not catches:
        return None
    # Prefer the most common catch across prompt columns.
    freq: Dict[str, int] = {}
    for c in catches:
        freq[c] = freq.get(c, 0) + 1
    best = max(freq.items(), key=lambda kv: (kv[1], len(kv[0])))
    return best[0] or None


def planning_signature_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    title = str(row.get("タイトル") or row.get("title") or "").strip()
    catch = select_thumbnail_catch(row)
    return {"title": title, "thumbnail_catch": catch or ""}


def planning_hash_from_row(row: Dict[str, Any]) -> str:
    return sha1_json(planning_signature_from_row(row))


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        tok = raw.lower() if raw.isascii() else raw
        if tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def title_script_token_overlap_ratio(title: str | None, script_text: str | None) -> float:
    title_tokens = _tokenize(title or "")
    if not title_tokens:
        return 1.0
    script_tokens = _tokenize((script_text or "")[:6000])
    if not script_tokens:
        return 0.0
    overlap = title_tokens & script_tokens
    return len(overlap) / max(len(title_tokens), 1)


def alignment_suspect_reason(planning_row: Dict[str, Any], script_text_preview: str | None) -> Optional[str]:
    """
    Decide whether a Planning↔Script pair looks suspect (likely mismatch) and return a human-readable reason.

    Notes:
    - This is a heuristic "safety gate" to prevent stamping hashes for obviously mismatched pairs.
    - It should be cheap and deterministic (no network, no model calls).
    """
    try:
        catches = {c for c in iter_thumbnail_catches_from_row(planning_row)}
    except Exception:
        catches = set()
    if len(catches) > 1:
        return "サムネプロンプト先頭行が不一致"

    return None


@dataclass(frozen=True)
class AlignmentStamp:
    schema: str
    computed_at: str
    planning_hash: str
    script_hash: str
    planning: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "computed_at": self.computed_at,
            "planning_hash": self.planning_hash,
            "script_hash": self.script_hash,
            "planning": self.planning,
        }


def build_alignment_stamp(*, planning_row: Dict[str, Any], script_path: Path) -> AlignmentStamp:
    sig = planning_signature_from_row(planning_row)
    return AlignmentStamp(
        schema=ALIGNMENT_SCHEMA,
        computed_at=utc_now_iso(),
        planning_hash=sha1_json(sig),
        script_hash=sha1_file(script_path),
        planning=sig,
    )
