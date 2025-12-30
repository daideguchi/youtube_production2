from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

from factory_common.locks import CoordinationLock, default_active_locks_for_mutation, find_blocking_lock
from factory_common.paths import ideas_archive_root, ideas_store_path, repo_root


IDEA_CARD_SCHEMA = "ytm.idea_card.v1"

ALLOWED_STATUSES: set[str] = {
    "INBOX",
    "BACKLOG",
    "BRUSHUP",
    "READY",
    "PRODUCING",
    "DONE",
    "ICEBOX",
    "KILL",
}

TRIAGE_STATUSES: set[str] = {"ICEBOX", "BACKLOG", "BRUSHUP", "KILL"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def utc_now_compact() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def normalize_status(status: str) -> str:
    s = (status or "").strip().upper()
    if s in ALLOWED_STATUSES:
        return s
    raise ValueError(f"Invalid status: {status!r} (allowed: {sorted(ALLOWED_STATUSES)})")


def parse_tags(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in re.split(r"[,\n]", str(raw)):
        s = part.strip()
        if s:
            out.append(s)
    # de-dup while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


def _norm_text_for_dedup(value: str) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    s = re.sub(r"[\s\u3000]+", " ", s)
    s = re.sub(r"[‐‑‒–—―ー〜~]", "-", s)
    s = re.sub(r"[()\[\]【】『』「」“”\"'’‘]", "", s)
    s = re.sub(r"[、,。\.！!？?：:；;・･·/\\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def dedup_key(card: dict[str, Any]) -> str:
    theme = _norm_text_for_dedup(str(card.get("theme") or ""))
    angle = _norm_text_for_dedup(str(card.get("angle") or ""))
    promise = _norm_text_for_dedup(str(card.get("promise") or ""))
    return f"{theme}|{angle}|{promise}".strip("|")


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_heavy(card: dict[str, Any]) -> bool:
    tags = card.get("tags") or []
    if not isinstance(tags, list):
        return False
    keys = {"heavy", "重い", "重め", "鬱", "不幸", "絶望"}
    for t in tags:
        s = str(t or "").strip().lower()
        if not s:
            continue
        if s in keys:
            return True
    return False


def score_total(score: dict[str, Any]) -> int:
    total = 0
    for k in ("novelty", "retention", "feasibility", "brand_fit"):
        try:
            total += int(score.get(k, 0) or 0)
        except Exception:
            total += 0
    return total


def normalize_score(score: Any) -> dict[str, int]:
    if not isinstance(score, dict):
        score = {}
    out = {
        "novelty": int(score.get("novelty", 0) or 0),
        "retention": int(score.get("retention", 0) or 0),
        "feasibility": int(score.get("feasibility", 0) or 0),
        "brand_fit": int(score.get("brand_fit", 0) or 0),
        "total": 0,
    }
    out["total"] = score_total(out)
    return out


def ensure_card_defaults(card: dict[str, Any]) -> dict[str, Any]:
    out = dict(card or {})
    out.setdefault("schema", IDEA_CARD_SCHEMA)
    out.setdefault("idea_id", "")
    out["channel"] = normalize_channel(str(out.get("channel") or ""))
    out.setdefault("series", "")
    out.setdefault("theme", "")
    out.setdefault("working_title", "")
    out.setdefault("hook", "")
    out.setdefault("promise", "")
    out.setdefault("angle", "")
    out.setdefault("length_target", "")
    out.setdefault("format", "")
    out["status"] = normalize_status(str(out.get("status") or "INBOX"))
    out["score"] = normalize_score(out.get("score"))
    if not isinstance(out.get("tags"), list):
        out["tags"] = []
    out.setdefault("source_memo", "")
    if not isinstance(out.get("planning_ref"), dict):
        out["planning_ref"] = {}
    out.setdefault("created_at", out.get("created_at") or utc_now_iso())
    out.setdefault("updated_at", out.get("updated_at") or out["created_at"])
    out.setdefault("status_at", out.get("status_at") or out["created_at"])
    if not isinstance(out.get("history"), list):
        out["history"] = []
    return out


def validate_required_fields(card: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for k in ("working_title", "hook", "promise", "angle"):
        if not str(card.get(k) or "").strip():
            missing.append(k)
    return missing


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cards: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise ValueError(f"Invalid JSONL at {path}:{i}: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"Invalid JSONL at {path}:{i}: expected object, got {type(obj).__name__}")
        cards.append(obj)
    return cards


def _write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    tmp.replace(path)


def load_cards(channel: str) -> tuple[Path, list[dict[str, Any]]]:
    ch = normalize_channel(channel)
    path = ideas_store_path(ch)
    cards = [_ensure_unique_id(ensure_card_defaults(c)) for c in _read_jsonl(path)]
    return path, cards


def save_cards(path: Path, cards: list[dict[str, Any]]) -> None:
    _write_jsonl_atomic(path, cards)


def ensure_mutation_allowed(path: Path, *, locks: Optional[list[CoordinationLock]] = None) -> None:
    locks = default_active_locks_for_mutation() if locks is None else locks
    blocking = find_blocking_lock(path, locks)
    if blocking:
        raise RuntimeError(
            "Blocked by coordination lock: "
            f"id={blocking.lock_id} created_by={blocking.created_by} mode={blocking.mode} scopes={blocking.scopes}"
        )


def _ensure_unique_id(card: dict[str, Any]) -> dict[str, Any]:
    # best-effort; used when loading older files missing ids.
    if str(card.get("idea_id") or "").strip():
        return card
    ch = normalize_channel(str(card.get("channel") or ""))
    fallback = f"{ch}-IDEA-{utc_now().strftime('%Y%m%d')}-MISSING"
    card["idea_id"] = fallback
    return card


def next_idea_id(channel: str, existing_ids: Iterable[str], *, at: Optional[datetime] = None) -> str:
    ch = normalize_channel(channel)
    at = utc_now() if at is None else at
    date_str = at.strftime("%Y%m%d")
    prefix = f"{ch}-IDEA-{date_str}-"
    max_seq = 0
    for idea_id in existing_ids:
        s = str(idea_id or "")
        if not s.startswith(prefix):
            continue
        tail = s[len(prefix) :]
        m = re.fullmatch(r"(\d{4})", tail)
        if not m:
            continue
        try:
            max_seq = max(max_seq, int(m.group(1)))
        except Exception:
            continue
    return f"{prefix}{max_seq + 1:04d}"


def new_card(
    *,
    channel: str,
    series: str = "",
    theme: str = "",
    working_title: str = "",
    hook: str = "",
    promise: str = "",
    angle: str = "",
    length_target: str = "",
    format: str = "",
    status: str = "INBOX",
    tags: Optional[list[str]] = None,
    source_memo: str = "",
) -> dict[str, Any]:
    ch = normalize_channel(channel)
    now = utc_now_iso()
    card = {
        "schema": IDEA_CARD_SCHEMA,
        "idea_id": "",
        "channel": ch,
        "series": series or "",
        "theme": theme or "",
        "working_title": working_title or "",
        "hook": hook or "",
        "promise": promise or "",
        "angle": angle or "",
        "length_target": length_target or "",
        "format": format or "",
        "status": normalize_status(status),
        "score": {"novelty": 0, "retention": 0, "feasibility": 0, "brand_fit": 0, "total": 0},
        "tags": tags or [],
        "source_memo": source_memo or "",
        "created_at": now,
        "updated_at": now,
        "status_at": now,
        "history": [],
    }
    return ensure_card_defaults(card)


def _append_history(card: dict[str, Any], *, action: str, changes: dict[str, Any], reason: str = "") -> None:
    history = card.get("history")
    if not isinstance(history, list):
        history = []
        card["history"] = history
    history.append(
        {
            "at": utc_now_iso(),
            "action": str(action),
            "changes": changes,
            "reason": str(reason or ""),
        }
    )


def _apply_patch(card: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(card)
    for k, v in patch.items():
        out[k] = v
    out["updated_at"] = utc_now_iso()
    return ensure_card_defaults(out)


def update_card_fields(
    cards: list[dict[str, Any]],
    idea_id: str,
    *,
    patch: dict[str, Any],
    action: str,
    reason: str = "",
) -> dict[str, Any]:
    target = None
    for c in cards:
        if str(c.get("idea_id") or "") == idea_id:
            target = c
            break
    if target is None:
        raise KeyError(f"Idea not found: {idea_id}")

    changes: dict[str, Any] = {}
    for k, v in patch.items():
        before = target.get(k)
        if before != v:
            changes[k] = [before, v]

    updated = _apply_patch(target, patch)

    if "status" in patch and patch.get("status") != target.get("status"):
        updated["status_at"] = updated["updated_at"]

    if changes:
        _append_history(updated, action=action, changes=changes, reason=reason)

    target.clear()
    target.update(updated)
    return target


def set_score(
    cards: list[dict[str, Any]],
    idea_id: str,
    *,
    novelty: int,
    retention: int,
    feasibility: int,
    brand_fit: int,
    action: str = "SCORE",
    reason: str = "",
    auto_status: bool = False,
    low_score_policy: str = "ICEBOX",
) -> dict[str, Any]:
    low_score_policy = normalize_status(low_score_policy)
    if low_score_policy not in {"ICEBOX", "KILL"}:
        raise ValueError("low_score_policy must be ICEBOX or KILL")

    for name, v in (
        ("novelty", novelty),
        ("retention", retention),
        ("feasibility", feasibility),
        ("brand_fit", brand_fit),
    ):
        if int(v) < 0 or int(v) > 5:
            raise ValueError(f"{name} must be 0..5 (got {v})")

    target = None
    for c in cards:
        if str(c.get("idea_id") or "") == idea_id:
            target = c
            break
    if target is None:
        raise KeyError(f"Idea not found: {idea_id}")

    old_score = normalize_score(target.get("score"))
    new_score = {
        "novelty": int(novelty),
        "retention": int(retention),
        "feasibility": int(feasibility),
        "brand_fit": int(brand_fit),
        "total": 0,
    }
    new_score["total"] = score_total(new_score)

    patch: dict[str, Any] = {"score": new_score}
    changes: dict[str, Any] = {"score": [old_score, new_score]}

    if auto_status:
        total = int(new_score["total"])
        if total >= 14:
            patch["status"] = "READY"
        elif total >= 10:
            patch["status"] = "BRUSHUP"
        else:
            patch["status"] = low_score_policy
        if patch.get("status") != target.get("status"):
            changes["status"] = [target.get("status"), patch.get("status")]

    updated = _apply_patch(target, patch)
    if "status" in patch and patch.get("status") != target.get("status"):
        updated["status_at"] = updated["updated_at"]

    _append_history(updated, action=action, changes=changes, reason=reason)
    target.clear()
    target.update(updated)
    return target


def find_exact_duplicates(cards: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Return {dedup_key: [idea_id, ...]} for keys that have 2+ cards.
    Only includes cards whose key is non-empty.
    """
    groups: dict[str, list[str]] = {}
    for c in cards:
        key = dedup_key(c)
        if not key.strip("|"):
            continue
        groups.setdefault(key, []).append(str(c.get("idea_id") or ""))
    return {k: v for k, v in groups.items() if len(v) >= 2}


@dataclass(frozen=True)
class NearDup:
    a: str
    b: str
    score: float


def find_near_duplicates(
    cards: list[dict[str, Any]],
    *,
    threshold: float = 0.9,
    max_pairs: int = 200,
) -> list[NearDup]:
    items: list[tuple[str, str]] = []
    for c in cards:
        idea_id = str(c.get("idea_id") or "")
        key = dedup_key(c)
        if not idea_id or not key.strip("|"):
            continue
        items.append((idea_id, key))

    out: list[NearDup] = []
    for i in range(len(items)):
        a_id, a_key = items[i]
        for j in range(i + 1, len(items)):
            b_id, b_key = items[j]
            s = similarity(a_key, b_key)
            if s >= threshold:
                out.append(NearDup(a=a_id, b=b_id, score=s))
                if len(out) >= max_pairs:
                    return sorted(out, key=lambda x: x.score, reverse=True)
    return sorted(out, key=lambda x: x.score, reverse=True)


def pick_next_ready(
    cards: list[dict[str, Any]],
    *,
    n: int,
    from_status: str = "BACKLOG",
    max_same_theme_in_row: int = 2,
    max_same_format_in_row: int = 2,
) -> list[str]:
    from_status = normalize_status(from_status)
    if n <= 0:
        return []

    candidates: list[dict[str, Any]] = []
    for c in cards:
        c = ensure_card_defaults(c)
        if c.get("status") != from_status:
            continue
        candidates.append(c)

    def _sort_key(card: dict[str, Any]) -> tuple[int, str]:
        total = int(normalize_score(card.get("score")).get("total", 0))
        created_at = str(card.get("created_at") or "")
        return (-total, created_at)

    candidates.sort(key=_sort_key)

    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    def _norm_field(v: Any) -> str:
        return str(v or "").strip().lower()

    def _same_run_len(field: str, next_val: str) -> int:
        run = 0
        for prev in reversed(selected):
            if _norm_field(prev.get(field)) != next_val:
                break
            run += 1
        return run

    for cand in candidates:
        if len(selected) >= n:
            break
        theme = _norm_field(cand.get("theme"))
        fmt = _norm_field(cand.get("format"))

        if theme and _same_run_len("theme", theme) >= max_same_theme_in_row:
            deferred.append(cand)
            continue
        if fmt and _same_run_len("format", fmt) >= max_same_format_in_row:
            deferred.append(cand)
            continue

        if len(selected) >= 2 and is_heavy(selected[-1]) and is_heavy(selected[-2]) and is_heavy(cand):
            deferred.append(cand)
            continue

        selected.append(cand)

    # Second pass: relax heavy rule (but keep theme/format constraints).
    if len(selected) < n and deferred:
        for cand in deferred:
            if len(selected) >= n:
                break
            theme = _norm_field(cand.get("theme"))
            fmt = _norm_field(cand.get("format"))
            if theme and _same_run_len("theme", theme) >= max_same_theme_in_row:
                continue
            if fmt and _same_run_len("format", fmt) >= max_same_format_in_row:
                continue
            selected.append(cand)

    return [str(c.get("idea_id") or "") for c in selected if str(c.get("idea_id") or "").strip()]


def archive_killed(
    path: Path,
    cards: list[dict[str, Any]],
    *,
    older_than_days: int = 30,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (archive_path, remaining_cards, archived_cards).
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")

    cutoff = utc_now() - timedelta(days=older_than_days)
    remaining: list[dict[str, Any]] = []
    archived: list[dict[str, Any]] = []

    for c in cards:
        c = ensure_card_defaults(c)
        if c.get("status") != "KILL":
            remaining.append(c)
            continue
        try:
            status_at = datetime.fromisoformat(str(c.get("status_at") or "").replace("Z", "+00:00"))
        except Exception:
            status_at = utc_now()
        if status_at.tzinfo is None:
            status_at = status_at.replace(tzinfo=timezone.utc)
        if status_at <= cutoff:
            archived.append(c)
        else:
            remaining.append(c)

    archive_dir = ideas_archive_root()
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{path.stem}__killed__{utc_now_compact()}.jsonl"
    return archive_path, remaining, archived


def assert_path_in_repo(path: Path) -> None:
    rr = repo_root()
    try:
        path.resolve().relative_to(rr)
    except Exception as e:
        raise ValueError(f"Expected repo-relative path (got {path})") from e
