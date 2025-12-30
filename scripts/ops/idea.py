#!/usr/bin/env python3
"""
Idea card manager (pre-planning inventory).

SoT:
  workspaces/planning/ideas/CHxx.jsonl

Why:
  Make "企画を増やす=整理→評価→配置まで終わる" を機械操作にする。
  - add: INBOXへ追加
  - normalize/brushup: 必須4点や訴求の改善
  - dedup: 重複検知（完全同一=KILLへ。削除はしない）
  - triage/move/kill: ステータス移動（理由必須）
  - score: 4軸採点（任意で auto-triage）
  - select: 次のN本を READY に確定（偏り制御つき）
  - slot: READY企画を planning CSV に投入（patch生成→lint→任意でapply）
  - archive: KILL を物理退避（30日後など）

Usage examples:
  python3 scripts/ops/idea.py add --channel CH01 --working-title "..." --hook "..." --promise "..." --angle "..."
  python3 scripts/ops/idea.py list --channel CH01 --status INBOX
  python3 scripts/ops/idea.py triage --channel CH01 --idea-id CH01-IDEA-... --to BACKLOG --reason "素材よし"
  python3 scripts/ops/idea.py score --channel CH01 --idea-id ... --novelty 4 --retention 3 --feasibility 4 --brand-fit 4 --auto-status
  python3 scripts/ops/idea.py select --channel CH01 --n 10 --apply
  python3 scripts/ops/idea.py slot --channel CH01 --n 10 --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.idea_store import (  # noqa: E402
    ALLOWED_STATUSES,
    TRIAGE_STATUSES,
    archive_killed,
    ensure_card_defaults,
    ensure_mutation_allowed,
    find_exact_duplicates,
    find_near_duplicates,
    load_cards,
    new_card,
    next_idea_id,
    normalize_channel,
    normalize_score,
    normalize_status,
    parse_tags,
    pick_next_ready,
    save_cards,
    set_score,
    utc_now_compact,
    validate_required_fields,
)
from factory_common.paths import channels_csv_path, logs_root, planning_patches_root  # noqa: E402
from script_pipeline.tools import planning_requirements  # noqa: E402
from script_pipeline.tools.optional_fields_registry import FIELD_KEYS  # noqa: E402


def _die(msg: str, *, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _ensure_mutation(path: Path, *, ignore_locks: bool) -> None:
    if ignore_locks:
        return
    ensure_mutation_allowed(path)


def _write_regression_json(op: str, label: str, payload: dict[str, Any]) -> Path:
    out_dir = logs_root() / "regression" / "idea_manager" / op
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = utc_now_compact()
    out_path = out_dir / f"{op}_{label}__{ts}.json"
    latest = out_dir / f"{op}_{label}__latest.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _print_card_summary(card: dict[str, Any]) -> None:
    score = normalize_score(card.get("score"))
    total = int(score.get("total", 0))
    print(f"{card.get('idea_id')}\t{card.get('status')}\t{total}\t{card.get('working_title')}")


_THEME_SPLIT_RE = re.compile(r"[／/|｜・、,]+")


def _split_theme(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _THEME_SPLIT_RE.split(raw) if p.strip()]
    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _guess_life_scene(tags: list[str]) -> str:
    for t in tags:
        s = str(t or "").strip()
        if not s:
            continue
        if any(k in s for k in ("職場", "仕事", "会社", "上司", "部下", "同僚")):
            return "仕事"
        if any(k in s for k in ("家庭", "夫婦", "子育て", "親", "家族")):
            return "家庭"
        if any(k in s for k in ("恋愛", "結婚", "彼氏", "彼女", "婚活", "浮気")):
            return "恋愛"
        if any(k in s for k in ("友人", "友達", "ママ友")):
            return "友人"
    return "日常"


def _shorten_one_line(text: str, *, max_len: int) -> str:
    s = " ".join(str(text or "").strip().split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _safe_rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except Exception:
        return str(path)


def _read_planning_csv(channel: str) -> tuple[Path, list[str], list[dict[str, str]]]:
    csv_path = channels_csv_path(channel)
    if not csv_path.exists():
        _die(f"Planning CSV not found: {csv_path} (create it under workspaces/planning/channels/)")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return csv_path, headers, rows


def _max_planning_video_number(rows: list[dict[str, str]]) -> int:
    max_no = 0
    for row in rows:
        raw = ""
        for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
            v = (row.get(key) or "").strip()
            if v:
                raw = v
                break
        if not raw:
            continue
        try:
            max_no = max(max_no, int(raw))
        except Exception:
            continue
    return max_no


def _planning_column(field_key: str) -> str | None:
    return FIELD_KEYS.get(field_key)


def _build_add_row_values(
    *,
    channel: str,
    video_number: int,
    headers: list[str],
    card: dict[str, Any],
    allow_new_columns: bool,
) -> dict[str, str]:
    if "タイトル" not in headers and not allow_new_columns:
        _die("Planning CSV missing required column: タイトル")

    idea_id = str(card.get("idea_id") or "").strip()
    tags = card.get("tags") if isinstance(card.get("tags"), list) else []
    tags = [str(t) for t in tags if str(t or "").strip()]
    theme_parts = _split_theme(str(card.get("theme") or ""))
    series = str(card.get("series") or "").strip()
    theme = str(card.get("theme") or "").strip()
    working_title = str(card.get("working_title") or "").strip()
    hook = str(card.get("hook") or "").strip()
    promise = str(card.get("promise") or "").strip()
    angle = str(card.get("angle") or "").strip()
    fmt = str(card.get("format") or "").strip()

    script_id = f"{channel}-{video_number:03d}"

    def _put(col: str, value: str) -> None:
        if not (value or "").strip():
            return
        if col in headers or allow_new_columns:
            values[col] = value

    values: dict[str, str] = {}
    _put("チャンネル", channel)
    _put("No.", str(video_number))
    _put("動画番号", str(video_number))
    _put("動画ID", script_id)
    _put("台本番号", script_id)
    _put("タイトル", working_title)
    _put("進捗", "topic_research: pending")
    _put("品質チェック結果", "未完了")
    _put("更新日時", _current_timestamp())

    persona = planning_requirements.get_channel_persona(channel)
    if persona:
        col = _planning_column("target_audience") or "ターゲット層"
        _put(col, persona)

    # Optional: carry the idea source for traceability (avoid bullet opener).
    content_notes_col = _planning_column("content_notes") or "内容"
    if idea_id:
        parts = [f"idea_id={idea_id}"]
        if hook:
            parts.append(hook)
        if promise:
            parts.append(promise)
        _put(content_notes_col, "\n".join(parts))

    concept_intent_col = _planning_column("concept_intent") or "企画意図"
    if promise or angle:
        intent = promise
        if angle:
            intent = (intent + "\n" if intent else "") + f"切り口: {angle}"
        _put(concept_intent_col, intent)

    outline_notes_col = _planning_column("outline_notes") or "具体的な内容（話の構成案）"
    outline_parts: list[str] = []
    if fmt:
        outline_parts.append(f"形式: {fmt}")
    if theme:
        outline_parts.append(f"テーマ: {theme}")
    if angle:
        outline_parts.append(f"切り口: {angle}")
    if outline_parts:
        _put(outline_notes_col, " / ".join(outline_parts))

    # Required fields by planning_requirements (mirrors UI guard + planning_lint).
    required_cols = planning_requirements.resolve_required_columns(channel, video_number)
    missing_required_columns = [c for c in required_cols if c and c not in headers and not allow_new_columns]
    if missing_required_columns:
        _die("Planning CSV missing required columns: " + ", ".join(missing_required_columns))

    defaults = planning_requirements.get_description_defaults(channel)

    primary = (theme_parts[0] if len(theme_parts) >= 1 else "") or series or (tags[0] if tags else "") or "未分類"
    secondary = (theme_parts[1] if len(theme_parts) >= 2 else "") or (tags[1] if len(tags) >= 2 else "") or primary
    life_scene = _guess_life_scene(tags)
    key_concept = (theme_parts[0] if theme_parts else "") or angle or _shorten_one_line(working_title, max_len=20) or primary
    benefit_blurb = _shorten_one_line(promise or working_title, max_len=60)
    analogy_image = (theme_parts[-1] if theme_parts else "") or angle or primary

    inferred_by_key: dict[str, str] = {
        "primary_pain_tag": primary,
        "secondary_pain_tag": secondary,
        "life_scene": life_scene,
        "key_concept": key_concept,
        "benefit_blurb": benefit_blurb,
        "analogy_image": analogy_image,
        "description_lead": str(defaults.get("description_lead") or ""),
        "description_takeaways": str(defaults.get("description_takeaways") or ""),
    }

    # Allow explicit overrides from idea card (planning_ref.planning_fields).
    planning_ref = card.get("planning_ref") if isinstance(card.get("planning_ref"), dict) else {}
    overrides = planning_ref.get("planning_fields") if isinstance(planning_ref.get("planning_fields"), dict) else {}
    for k, v in overrides.items():
        if isinstance(k, str):
            inferred_by_key[k] = str(v or "")

    for field_key in planning_requirements.resolve_required_field_keys(channel, video_number):
        col = _planning_column(field_key) or FIELD_KEYS.get(field_key)
        if not col:
            continue
        _put(col, inferred_by_key.get(field_key, ""))

    return values


def _run_planning_apply_patch(
    patch_path: Path,
    *,
    apply: bool,
    allow_new_columns: bool,
    ignore_locks: bool,
) -> int:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "ops" / "planning_apply_patch.py"),
        "--patch",
        str(patch_path),
    ]
    if apply:
        cmd.append("--apply")
    if allow_new_columns:
        cmd.append("--allow-new-columns")
    if ignore_locks:
        cmd.append("--ignore-locks")
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode


def cmd_slot(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    store_path, cards = load_cards(channel)

    planning_csv_path, headers, rows = _read_planning_csv(channel)
    next_video = args.start_video if args.start_video is not None else (_max_planning_video_number(rows) + 1)

    picked_cards: list[dict[str, Any]] = []
    if args.idea_ids:
        wanted = set(args.idea_ids)
        for c in cards:
            if str(c.get("idea_id") or "") in wanted:
                picked_cards.append(ensure_card_defaults(c))
        missing = wanted - {str(c.get("idea_id") or "") for c in picked_cards}
        if missing:
            _die(f"Idea not found: {', '.join(sorted(missing))}", code=1)
    else:
        from_status = normalize_status(args.from_status)
        pool = [ensure_card_defaults(c) for c in cards if ensure_card_defaults(c).get("status") == from_status]
        pool.sort(key=lambda c: (str(c.get("status_at") or ""), str(c.get("created_at") or "")))
        picked_cards = pool[: args.n]

    if not picked_cards:
        _die("No ideas to slot.")

    _ensure_mutation(store_path, ignore_locks=args.ignore_locks)
    _ensure_mutation(planning_csv_path, ignore_locks=args.ignore_locks)

    patches_dir = planning_patches_root()
    patches_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = logs_root() / "regression" / "idea_manager" / "slot_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    planned: list[dict[str, Any]] = []
    patch_paths: list[Path] = []

    for idx, card in enumerate(picked_cards):
        video_no = next_video + idx
        video_token = f"{video_no:03d}"

        idea_id = str(card.get("idea_id") or "").strip()
        idea_suffix = idea_id.replace(f"{channel}-", "") if idea_id.startswith(f"{channel}-") else idea_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = f"{channel}-{video_token}__add_from_idea_{idea_suffix}_{stamp}.yaml".replace("/", "_")
        patch_path = patches_dir / filename
        tmp_patch_path = tmp_dir / filename

        if patch_path.exists():
            _die(f"Patch already exists: {patch_path} (refuse to overwrite)")

        _ensure_mutation(patch_path, ignore_locks=args.ignore_locks)
        _ensure_mutation(tmp_patch_path, ignore_locks=True)

        add_row = _build_add_row_values(
            channel=channel,
            video_number=video_no,
            headers=headers,
            card=card,
            allow_new_columns=bool(args.allow_new_columns),
        )

        patch = {
            "schema": "ytm.planning_patch.v1",
            "patch_id": f"{channel}-{video_token}__add_from_idea_{idea_id}_{stamp}",
            "target": {"channel": channel, "video": video_token},
            "apply": {"add_row": add_row},
            "notes": (args.notes or "").strip() or f"Added from idea card {idea_id}",
        }

        import yaml  # local import (ops-only)

        tmp_patch_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")

        dry_code = _run_planning_apply_patch(
            tmp_patch_path,
            apply=False,
            allow_new_columns=bool(args.allow_new_columns),
            ignore_locks=bool(args.ignore_locks),
        )
        if dry_code != 0:
            _die(f"planning_apply_patch dry-run failed for {tmp_patch_path} (exit={dry_code})", code=dry_code)

        patch_path.write_text(tmp_patch_path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            tmp_patch_path.unlink()
        except Exception:
            pass
        patch_paths.append(patch_path)

        planned.append(
            {
                "idea_id": idea_id,
                "video_number": video_token,
                "script_id": f"{channel}-{video_token}",
                "patch_path": _safe_rel(patch_path),
            }
        )

    report = {
        "schema": "ytm.idea_manager.slot_report.v1",
        "channel": channel,
        "store_path": str(store_path),
        "planning_csv": str(planning_csv_path),
        "from_status": args.from_status if args.idea_ids is None else "(explicit_ids)",
        "count": len(planned),
        "planned": planned,
        "apply": bool(args.apply),
        "allow_new_columns": bool(args.allow_new_columns),
    }
    report_path = _write_regression_json("slot", f"{channel}_{len(planned)}", report)
    print(f"wrote_report\t{report_path}")
    for it in planned:
        print(f"patch\t{it['patch_path']}\t{it['script_id']}")

    if not args.apply:
        print(
            "next_apply_command\tpython3 scripts/ops/planning_apply_patch.py "
            + " ".join(f"--patch {_safe_rel(p)}" for p in patch_paths)
            + " --apply"
        )
        return

    from factory_common.idea_store import update_card_fields

    for it, patch_path in zip(planned, patch_paths):
        code = _run_planning_apply_patch(
            patch_path,
            apply=True,
            allow_new_columns=bool(args.allow_new_columns),
            ignore_locks=bool(args.ignore_locks),
        )
        if code not in (0, 1):
            _die(f"planning_apply_patch apply failed for {patch_path} (exit={code})", code=code)

        idea_id = str(it["idea_id"])
        patch = {
            "status": "PRODUCING",
            "planning_ref": {
                "channel": channel,
                "video": str(it["video_number"]),
                "script_id": str(it["script_id"]),
                "patch_path": _safe_rel(patch_path),
                "pushed_at": _current_timestamp(),
            },
        }
        update_card_fields(
            cards,
            idea_id,
            patch=patch,
            action="SLOT_TO_PLANNING",
            reason=f"planning_add_row script_id={it['script_id']}",
        )

    save_cards(store_path, cards)
    print(f"ideas_marked_PRODUCING\t{len(planned)}")


def cmd_add(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    path, cards = load_cards(channel)
    _ensure_mutation(path, ignore_locks=args.ignore_locks)

    existing = [str(c.get("idea_id") or "") for c in cards]
    card = new_card(
        channel=channel,
        series=args.series or "",
        theme=args.theme or "",
        working_title=args.working_title or "",
        hook=args.hook or "",
        promise=args.promise or "",
        angle=args.angle or "",
        length_target=args.length_target or "",
        format=args.format or "",
        status=args.status or "INBOX",
        tags=parse_tags(args.tags),
        source_memo=args.source_memo or "",
    )
    card["idea_id"] = next_idea_id(channel, existing)
    cards.append(card)
    save_cards(path, cards)

    missing = validate_required_fields(card)
    _print_card_summary(card)
    if missing:
        print(f"(missing_required={','.join(missing)})", file=sys.stderr)


def cmd_list(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    _, cards = load_cards(channel)

    want_status = normalize_status(args.status) if args.status else None
    out: list[dict[str, Any]] = []
    for c in cards:
        c = ensure_card_defaults(c)
        if want_status and c.get("status") != want_status:
            continue
        out.append(c)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print("idea_id\tstatus\tscore_total\tworking_title")
    for c in out[: args.limit]:
        _print_card_summary(c)


def cmd_show(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    _, cards = load_cards(channel)
    for c in cards:
        if str(c.get("idea_id") or "") == args.idea_id:
            print(json.dumps(ensure_card_defaults(c), ensure_ascii=False, indent=2, sort_keys=True))
            return
    _die(f"Idea not found: {args.idea_id}", code=1)


def _apply_card_patch(
    channel: str,
    idea_id: str,
    *,
    patch: dict[str, Any],
    action: str,
    reason: str,
    ignore_locks: bool,
) -> dict[str, Any]:
    path, cards = load_cards(channel)
    _ensure_mutation(path, ignore_locks=ignore_locks)
    from factory_common.idea_store import update_card_fields  # local import to keep CLI small

    updated = update_card_fields(cards, idea_id, patch=patch, action=action, reason=reason)
    save_cards(path, cards)
    return updated


def cmd_normalize(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    patch: dict[str, Any] = {}
    for key in ("working_title", "hook", "promise", "angle", "series", "theme", "format", "length_target", "source_memo"):
        v = getattr(args, key, None)
        if v is not None:
            patch[key] = v
    if args.tags is not None:
        patch["tags"] = parse_tags(args.tags)
    if not patch:
        _die("No fields to update. Pass at least one of --working-title/--hook/--promise/--angle ...")

    updated = _apply_card_patch(
        channel,
        args.idea_id,
        patch=patch,
        action="NORMALIZE",
        reason=args.reason or "",
        ignore_locks=args.ignore_locks,
    )
    missing = validate_required_fields(updated)
    _print_card_summary(updated)
    if missing:
        print(f"(missing_required={','.join(missing)})", file=sys.stderr)


def cmd_brushup(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    if not (args.reason or "").strip():
        _die("--reason is required for BRUSHUP")

    patch: dict[str, Any] = {}
    for key in ("working_title", "hook", "promise", "angle", "format"):
        v = getattr(args, key, None)
        if v is not None:
            patch[key] = v
    if not patch:
        _die("No fields to update. Pass at least one of --hook/--promise/--angle/--format/--working-title")

    updated = _apply_card_patch(
        channel,
        args.idea_id,
        patch=patch,
        action="BRUSHUP",
        reason=args.reason,
        ignore_locks=args.ignore_locks,
    )
    _print_card_summary(updated)


def cmd_move(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    to_status = normalize_status(args.to)
    if args.require_reason and not (args.reason or "").strip():
        _die("--reason is required")

    patch = {"status": to_status}
    updated = _apply_card_patch(
        channel,
        args.idea_id,
        patch=patch,
        action="MOVE",
        reason=args.reason or "",
        ignore_locks=args.ignore_locks,
    )
    _print_card_summary(updated)


def cmd_score(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    path, cards = load_cards(channel)
    _ensure_mutation(path, ignore_locks=args.ignore_locks)

    updated = set_score(
        cards,
        args.idea_id,
        novelty=args.novelty,
        retention=args.retention,
        feasibility=args.feasibility,
        brand_fit=args.brand_fit,
        reason=args.reason or "",
        auto_status=args.auto_status,
        low_score_policy=args.low_score_policy,
    )
    save_cards(path, cards)
    _print_card_summary(updated)


def cmd_dedup(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    path, cards = load_cards(channel)

    active = [ensure_card_defaults(c) for c in cards if ensure_card_defaults(c).get("status") != "KILL"]
    exact = find_exact_duplicates(active)
    near = find_near_duplicates(active, threshold=args.threshold, max_pairs=args.max_pairs)

    report = {
        "schema": "ytm.idea_manager.dedup_report.v1",
        "channel": channel,
        "store_path": str(path),
        "threshold": args.threshold,
        "exact_duplicates": exact,
        "near_duplicates": [asdict(x) for x in near],
        "counts": {"cards": len(cards), "active": len(active), "exact_groups": len(exact), "near_pairs": len(near)},
    }
    report_path = _write_regression_json("dedup", channel, report)
    print(f"wrote_report\t{report_path}")
    print(f"exact_groups\t{len(exact)}")
    print(f"near_pairs\t{len(near)}")

    if not args.apply:
        return

    _ensure_mutation(path, ignore_locks=args.ignore_locks)

    def _card_by_id(idea_id: str) -> dict[str, Any] | None:
        for c in cards:
            if str(c.get("idea_id") or "") == idea_id:
                return c
        return None

    from factory_common.idea_store import update_card_fields

    killed = 0
    for key, ids in exact.items():
        if len(ids) < 2:
            continue
        # Keep the "best" by (score_total desc, created_at asc).
        def _keep_sort(idea_id: str) -> tuple[int, str]:
            c = _card_by_id(idea_id) or {}
            total = int(normalize_score(c.get("score")).get("total", 0))
            return (-total, str(c.get("created_at") or ""))

        ordered = sorted(ids, key=_keep_sort)
        keep_id = ordered[0]
        for dup_id in ordered[1:]:
            c = _card_by_id(dup_id)
            if not c:
                continue
            tags = list(c.get("tags") or [])
            if "duplicate" not in tags:
                tags.append("duplicate")
            update_card_fields(
                cards,
                dup_id,
                patch={"status": "KILL", "tags": tags},
                action="DEDUP",
                reason=f"duplicate_of={keep_id} key={key}",
            )
            killed += 1

    if killed:
        save_cards(path, cards)
    print(f"dedup_killed\t{killed}")


def cmd_select(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    path, cards = load_cards(channel)

    picked = pick_next_ready(
        cards,
        n=args.n,
        from_status=normalize_status(args.from_status),
        max_same_theme_in_row=args.max_same_theme_in_row,
        max_same_format_in_row=args.max_same_format_in_row,
    )

    report = {
        "schema": "ytm.idea_manager.select_report.v1",
        "channel": channel,
        "store_path": str(path),
        "picked": picked,
        "n": args.n,
        "from_status": args.from_status,
        "constraints": {
            "max_same_theme_in_row": args.max_same_theme_in_row,
            "max_same_format_in_row": args.max_same_format_in_row,
        },
        "counts": {"cards": len(cards), "picked": len(picked)},
    }
    report_path = _write_regression_json("select", f"{channel}_{args.from_status}_{args.n}", report)
    print(f"wrote_report\t{report_path}")
    for idea_id in picked:
        print(f"pick\t{idea_id}")

    if not args.apply:
        return

    _ensure_mutation(path, ignore_locks=args.ignore_locks)
    from factory_common.idea_store import update_card_fields

    moved = 0
    for idea_id in picked:
        update_card_fields(
            cards,
            idea_id,
            patch={"status": "READY"},
            action="SELECT",
            reason=f"select_next_n={args.n} from={args.from_status}",
        )
        moved += 1
    save_cards(path, cards)
    print(f"moved_to_READY\t{moved}")


def cmd_kill(args: argparse.Namespace) -> None:
    args.to = "KILL"
    args.require_reason = True
    cmd_move(args)


def cmd_archive(args: argparse.Namespace) -> None:
    channel = normalize_channel(args.channel)
    path, cards = load_cards(channel)

    archive_path, remaining, archived = archive_killed(path, cards, older_than_days=args.older_than_days)
    report = {
        "schema": "ytm.idea_manager.archive_report.v1",
        "channel": channel,
        "store_path": str(path),
        "archive_path": str(archive_path),
        "older_than_days": args.older_than_days,
        "counts": {"cards": len(cards), "archived": len(archived), "remaining": len(remaining)},
        "archived_ids": [str(c.get("idea_id") or "") for c in archived],
    }
    report_path = _write_regression_json("archive", f"{channel}_{args.older_than_days}d", report)
    print(f"wrote_report\t{report_path}")
    print(f"archived_count\t{len(archived)}")

    if not args.apply:
        return

    _ensure_mutation(path, ignore_locks=args.ignore_locks)
    _ensure_mutation(archive_path, ignore_locks=args.ignore_locks)

    if archived:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        # Append-only archive: keep multiple runs in one file if desired.
        with archive_path.open("a", encoding="utf-8") as f:
            for c in archived:
                f.write(json.dumps(ensure_card_defaults(c), ensure_ascii=False, sort_keys=True))
                f.write("\n")

    save_cards(path, remaining)
    print(f"archived_written\t{archive_path}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Idea card manager (pre-planning inventory)")
    ap.add_argument("--ignore-locks", action="store_true", help="Ignore coordination locks (DANGEROUS).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_channel(p: argparse.ArgumentParser) -> None:
        p.add_argument("--channel", required=True, help="Channel code (e.g. CH01)")

    p = sub.add_parser("add", help="Add a new idea card into INBOX")
    _add_channel(p)
    p.add_argument("--series", default="")
    p.add_argument("--theme", default="")
    p.add_argument("--working-title", default="", dest="working_title")
    p.add_argument("--hook", default="")
    p.add_argument("--promise", default="")
    p.add_argument("--angle", default="")
    p.add_argument("--length-target", default="", dest="length_target")
    p.add_argument("--format", default="")
    p.add_argument("--status", default="INBOX", choices=sorted(ALLOWED_STATUSES))
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--source-memo", default="", dest="source_memo")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="List idea cards")
    _add_channel(p)
    p.add_argument("--status", default=None, choices=sorted(ALLOWED_STATUSES))
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="Show a single card as JSON")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("normalize", help="Fill required fields (or patch card fields)")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--working-title", dest="working_title")
    p.add_argument("--hook")
    p.add_argument("--promise")
    p.add_argument("--angle")
    p.add_argument("--series")
    p.add_argument("--theme")
    p.add_argument("--format")
    p.add_argument("--length-target", dest="length_target")
    p.add_argument("--tags")
    p.add_argument("--source-memo", dest="source_memo")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("brushup", help="Apply BRUSHUP updates (hook/promise/angle/format) with a reason")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--working-title", dest="working_title")
    p.add_argument("--hook")
    p.add_argument("--promise")
    p.add_argument("--angle")
    p.add_argument("--format")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_brushup)

    p = sub.add_parser("move", help="Move an idea card to another status (reason recommended)")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--to", required=True, choices=sorted(ALLOWED_STATUSES))
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_move, require_reason=False)

    p = sub.add_parser("triage", help="Triage an idea into ICEBOX/BACKLOG/BRUSHUP/KILL (reason required)")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--to", required=True, choices=sorted(TRIAGE_STATUSES))
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_move, require_reason=True)

    p = sub.add_parser("kill", help="Move an idea to KILL (reason required)")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_kill, require_reason=True)

    p = sub.add_parser("score", help="Set 4-axis score (0..5) and compute total")
    _add_channel(p)
    p.add_argument("--idea-id", required=True, dest="idea_id")
    p.add_argument("--novelty", type=int, required=True)
    p.add_argument("--retention", type=int, required=True)
    p.add_argument("--feasibility", type=int, required=True)
    p.add_argument("--brand-fit", type=int, required=True, dest="brand_fit")
    p.add_argument("--reason", default="")
    p.add_argument("--auto-status", action="store_true", help="Auto-move to READY/BRUSHUP/ICEBOX based on total")
    p.add_argument("--low-score-policy", default="ICEBOX", choices=["ICEBOX", "KILL"])
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("dedup", help="Detect duplicates and write a report (apply: exact dups -> KILL)")
    _add_channel(p)
    p.add_argument("--threshold", type=float, default=0.9)
    p.add_argument("--max-pairs", type=int, default=200, dest="max_pairs")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("select", help="Pick next N ideas and (apply) move them to READY")
    _add_channel(p)
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--from-status", default="BACKLOG", choices=sorted(ALLOWED_STATUSES), dest="from_status")
    p.add_argument("--max-same-theme-in-row", type=int, default=2, dest="max_same_theme_in_row")
    p.add_argument("--max-same-format-in-row", type=int, default=2, dest="max_same_format_in_row")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("slot", help="Slot ideas into Planning CSV via planning patches (add_row)")
    _add_channel(p)
    p.add_argument("--n", type=int, default=10, help="How many cards to slot (when --idea-id not used)")
    p.add_argument("--from-status", default="READY", choices=sorted(ALLOWED_STATUSES), dest="from_status")
    p.add_argument("--idea-id", action="append", default=None, dest="idea_ids", help="Explicit idea_id (repeatable)")
    p.add_argument("--start-video", type=int, default=None, dest="start_video", help="Override starting video number")
    p.add_argument("--apply", action="store_true", help="Apply the generated planning patches (writes CSV)")
    p.add_argument("--allow-new-columns", action="store_true", help="Allow patch to append new CSV columns (DANGEROUS)")
    p.add_argument("--notes", default="", help="Patch notes (optional)")
    p.set_defaults(func=cmd_slot)

    p = sub.add_parser("archive", help="Archive KILL cards older than N days (apply moves them out of store)")
    _add_channel(p)
    p.add_argument("--older-than-days", type=int, default=30, dest="older_than_days")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_archive)

    return ap


def main(argv: Optional[list[str]] = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
