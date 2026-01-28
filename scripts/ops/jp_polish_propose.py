#!/usr/bin/env python3
from __future__ import annotations

"""
jp_polish_propose.py — JP Polish (proposal-only) using Ollama

Policy (SSOT):
- Never overwrite A-text SoT automatically.
- Produce: proposed text + unified diff + validate report + logs.
- Preserve pause markers (`---`) count/order (kept verbatim).
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap


_RE_URL = re.compile(r"https?://|\bwww\.", flags=re.IGNORECASE)
_RE_MD = re.compile(r"^(\s*#{1,6}\s+|\s*[-*•]\s+|\s*\d+[.)]\s+|\s*```)", flags=re.MULTILINE)
_RE_NUM = re.compile(r"\d+")
_NEG_MARKERS = ("ない", "ません", "ず", "ぬ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _z3(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        raise SystemExit(f"Invalid --video: {value!r}")
    return f"{int(digits):03d}"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _split_keep_pause_lines(lines: List[str]) -> List[Tuple[str, List[str]]]:
    """
    Return a sequence of ("text"|"pause", lines).
    Pause lines are those where strip() == '---' and are kept verbatim.
    """
    out: List[Tuple[str, List[str]]] = []
    buf: List[str] = []
    for ln in lines:
        if ln.strip() == "---":
            if buf:
                out.append(("text", buf))
                buf = []
            out.append(("pause", ["---"]))
            continue
        buf.append(ln)
    if buf:
        out.append(("text", buf))
    return out


def _numbers(text: str) -> List[str]:
    return _RE_NUM.findall(text or "")


def _has_negation(text: str) -> bool:
    t = text or ""
    return any(m in t for m in _NEG_MARKERS)


def _looks_like_a_text(text: str) -> Tuple[bool, List[str]]:
    """
    Cheap local guard (validator does the heavy lifting, but we want quick reject).
    """
    reasons: List[str] = []
    if not (text or "").strip():
        reasons.append("empty_output")
    if _RE_URL.search(text or ""):
        reasons.append("url_detected")
    if _RE_MD.search(text or ""):
        reasons.append("markdown_or_list_detected")
    # JP Polish must not inject pause markers.
    if re.search(r"^\s*---\s*$", text or "", flags=re.MULTILINE):
        reasons.append("pause_marker_injected")
    return (len(reasons) == 0), reasons


@dataclass
class SegmentResult:
    status: str  # ok|fallback_ok|kept_original|error
    model: str
    duration_s: float
    len_ratio: float
    numbers_equal: bool
    negation_maybe_changed: bool
    reasons: List[str]
    original: str
    proposed: str


class OllamaClient:
    def __init__(self, base_url: str, *, min_interval_sec: float) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.min_interval_sec = float(min_interval_sec)
        self._last_req_ts = 0.0
        # Avoid macOS system proxies / corp proxy surprises.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _sleep_if_needed(self) -> None:
        if self.min_interval_sec <= 0:
            return
        now = time.time()
        dt = now - self._last_req_ts
        if dt < self.min_interval_sec:
            time.sleep(self.min_interval_sec - dt)

    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        self._sleep_if_needed()
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "temperature": float(temperature),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json; charset=utf-8"}
        )
        raw = ""
        try:
            with self._opener.open(req, timeout=float(timeout_sec)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            self._last_req_ts = time.time()
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            self._last_req_ts = time.time()
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
            raise RuntimeError(f"Ollama HTTP {e.code}: {raw[:200]}") from e
        except Exception as e:  # noqa: BLE001
            self._last_req_ts = time.time()
            raise RuntimeError(f"Ollama request failed: {e}") from e


def _compose_system_prompt() -> str:
    return "\n".join(
        [
            "あなたは日本語の読み上げ台本（Aテキスト）を整える校正者です。",
            "必ず守ること:",
            "- 要約しない（短くしない）",
            "- 事実/数値/固有名詞/否定（ない・ではない等）を変えない",
            "- 口調は本文のトーンを維持（過剰に丁寧語へ寄せない）",
            "- Markdown/箇条書き/見出し/解説文/前置きは禁止",
            "- 出力は『修正文』本文のみ（それ以外は出さない）",
        ]
    )


def _compose_prompt(text: str) -> str:
    return "\n".join(
        [
            "次の本文を、意味と事実を変えずに、読み上げとして自然な日本語に整えてください。",
            "【本文】",
            text.strip(),
        ]
    )


def _polish_segment(
    *,
    client: OllamaClient,
    text: str,
    model: str,
    fallback_model: str,
    temperature: float,
    timeout_sec: float,
    min_len_ratio: float,
    max_len_ratio: float,
) -> SegmentResult:
    orig = (text or "").strip("\n")
    if not orig.strip():
        return SegmentResult(
            status="kept_original",
            model="",
            duration_s=0.0,
            len_ratio=1.0,
            numbers_equal=True,
            negation_maybe_changed=False,
            reasons=["empty_segment"],
            original=orig,
            proposed=orig,
        )

    system = _compose_system_prompt()
    prompt = _compose_prompt(orig)

    orig_nums = _numbers(orig)
    orig_has_neg = _has_negation(orig)

    def _attempt(model_name: str) -> Tuple[str, float, str, List[str], bool, bool]:
        t0 = time.time()
        j = client.generate(
            model=model_name, system=system, prompt=prompt, temperature=temperature, timeout_sec=timeout_sec
        )
        out = (j.get("response") or "").strip()
        dt = time.time() - t0

        ok, reasons = _looks_like_a_text(out)
        nums_equal = _numbers(out) == orig_nums
        if not nums_equal:
            reasons.append("numbers_changed")
            ok = False

        ratio = (len(out) / max(1, len(orig))) if out else 0.0
        if ratio < min_len_ratio:
            reasons.append(f"too_short(len_ratio={ratio:.3f})")
            ok = False
        if ratio > max_len_ratio:
            reasons.append(f"too_long(len_ratio={ratio:.3f})")
            ok = False

        neg_changed = orig_has_neg and (not _has_negation(out))
        # Negation drift is a *warning* signal; keep as reason but do not hard-fail by itself.
        if neg_changed:
            reasons.append("negation_maybe_changed")

        if not ok:
            raise RuntimeError("; ".join(reasons) or "reject")
        return out, dt, "; ".join(reasons), reasons, nums_equal, neg_changed

    # Primary -> fallback -> keep original
    for idx, model_name in enumerate([model, fallback_model]):
        if not model_name:
            continue
        try:
            out, dt, _, reasons, nums_equal, neg_changed = _attempt(model_name)
            ratio = (len(out) / max(1, len(orig))) if out else 0.0
            return SegmentResult(
                status="ok" if idx == 0 else "fallback_ok",
                model=model_name,
                duration_s=dt,
                len_ratio=ratio,
                numbers_equal=nums_equal,
                negation_maybe_changed=neg_changed,
                reasons=reasons,
                original=orig,
                proposed=out,
            )
        except Exception as e:  # noqa: BLE001
            # Try next model.
            last_err = str(e)
            continue

    # If both failed, keep original.
    ratio = 1.0
    nums_equal = True
    neg_changed = False
    return SegmentResult(
        status="kept_original",
        model="",
        duration_s=0.0,
        len_ratio=ratio,
        numbers_equal=nums_equal,
        negation_maybe_changed=neg_changed,
        reasons=[f"all_models_failed: {last_err[:120]}"] if "last_err" in locals() else ["all_models_failed"],
        original=orig,
        proposed=orig,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="JP Polish proposal-only runner (Ollama).")
    parser.add_argument("--channel", required=True, help="Channel like CH06")
    parser.add_argument("--video", required=True, help="Video number like 034")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--fallback-model", default="qwen2.5:1.5b")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--min-interval-sec", type=float, default=0.2)
    parser.add_argument("--warmup", action="store_true", help="Warm up primary model (long timeout).")
    parser.add_argument("--warmup-timeout-sec", type=float, default=180.0)
    parser.add_argument("--min-len-ratio", type=float, default=0.85)
    parser.add_argument("--max-len-ratio", type=float, default=1.20)
    parser.add_argument("--max-segments", type=int, default=0, help="Process only first N text segments (0=all).")
    args = parser.parse_args(argv)

    bootstrap(load_env=False)

    # IMPORTANT: Do not hardcode paths. Use repo paths helper.
    from factory_common import paths as repo_paths  # noqa: E402
    from script_pipeline.validator import validate_a_text  # noqa: E402

    ch = str(args.channel).strip().upper()
    vid = _z3(args.video)
    base = repo_paths.video_root(ch, vid)
    content_dir = base / "content"
    in_human = content_dir / "assembled_human.md"
    in_md = content_dir / "assembled.md"
    in_path = in_human if in_human.exists() else in_md
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_human} (or {in_md})")

    original = _read_text(in_path)
    original_norm = original.replace("\r\n", "\n").replace("\r", "\n")
    orig_lines = original_norm.split("\n")

    run_id = _utc_now_compact()
    out_dir = content_dir / "analysis" / "jp_polish"
    _ensure_dir(out_dir)

    client = OllamaClient(str(args.ollama_url).rstrip("/"), min_interval_sec=args.min_interval_sec)

    if args.warmup and args.model:
        try:
            _ = client.generate(
                model=args.model,
                system="",
                prompt="OKとだけ返して",
                temperature=0.0,
                timeout_sec=args.warmup_timeout_sec,
            )
        except Exception:
            # Warmup failure should not hard-stop; we still try main run (fallback may succeed).
            pass

    blocks = _split_keep_pause_lines(orig_lines)
    proposed_lines: List[str] = []
    seg_results: List[Dict[str, Any]] = []

    text_seg_count = 0
    for kind, lines in blocks:
        if kind == "pause":
            proposed_lines.append("---")
            continue

        text_seg_count += 1
        if args.max_segments and text_seg_count > args.max_segments:
            proposed_lines.extend(lines)
            continue

        seg_text = "\n".join(lines).strip("\n")
        res = _polish_segment(
            client=client,
            text=seg_text,
            model=args.model,
            fallback_model=args.fallback_model,
            temperature=args.temperature,
            timeout_sec=args.timeout_sec,
            min_len_ratio=args.min_len_ratio,
            max_len_ratio=args.max_len_ratio,
        )

        proposed_lines.extend((res.proposed or "").split("\n"))
        seg_results.append(
            {
                "segment_index": text_seg_count,
                "status": res.status,
                "model": res.model,
                "duration_s": res.duration_s,
                "len_ratio": res.len_ratio,
                "numbers_equal": res.numbers_equal,
                "negation_maybe_changed": res.negation_maybe_changed,
                "reasons": res.reasons,
                "orig_sha256": _sha256(res.original),
                "proposed_sha256": _sha256(res.proposed),
            }
        )

    proposed = "\n".join(proposed_lines).rstrip() + "\n"

    # Validate proposed A-text against global rules.
    issues, stats = validate_a_text(
        proposed,
        {
            "assembled_path": str(in_path),
        },
    )
    errors = [it for it in issues if str((it or {}).get("severity") or "error").lower() != "warning"]

    # Pause markers: count/order must match.
    orig_pause = [i for i, ln in enumerate(orig_lines) if ln.strip() == "---"]
    prop_pause = [i for i, ln in enumerate(proposed.split("\n")) if ln.strip() == "---"]
    pause_ok = len(orig_pause) == len(prop_pause)

    # Persist artifacts
    proposed_path = out_dir / f"proposed_a_text_{run_id}.md"
    diff_path = out_dir / f"proposed_a_text_{run_id}.diff"
    validate_path = out_dir / f"validate_{run_id}.md"
    summary_path = out_dir / f"change_summary_{run_id}.md"
    meta_path = out_dir / f"run_meta_{run_id}.json"
    log_path = out_dir / f"log_{run_id}.jsonl"

    latest_proposed = out_dir / "proposed_a_text_latest.md"
    latest_diff = out_dir / "proposed_a_text_latest.diff"
    latest_validate = out_dir / "validate_latest.md"
    latest_summary = out_dir / "change_summary_latest.md"
    latest_meta = out_dir / "run_meta_latest.json"
    latest_log = out_dir / "log_latest.jsonl"

    _write_text(proposed_path, proposed)
    _write_text(latest_proposed, proposed)

    diff = "\n".join(
        difflib.unified_diff(
            original_norm.splitlines(),
            proposed.splitlines(),
            fromfile=str(in_path),
            tofile=str(proposed_path),
            lineterm="",
        )
    )
    _write_text(diff_path, diff + "\n")
    _write_text(latest_diff, diff + "\n")

    validate_lines: List[str] = []
    validate_lines.append("# JP Polish validate")
    validate_lines.append("")
    validate_lines.append(f"- run_id: {run_id}")
    validate_lines.append(f"- utc: {_utc_now_iso()}")
    validate_lines.append(f"- input: {in_path}")
    validate_lines.append(f"- output: {proposed_path}")
    validate_lines.append("")
    validate_lines.append("## Checks")
    validate_lines.append("")
    validate_lines.append(f"- pause_markers_same_count: {pause_ok} (orig={len(orig_pause)}, proposed={len(prop_pause)})")
    validate_lines.append(f"- validator_issues: {len(issues)}")
    validate_lines.append(f"- validator_errors: {len(errors)}")
    validate_lines.append(f"- validator_stats: {json.dumps(stats, ensure_ascii=False)}")
    if issues:
        validate_lines.append("")
        validate_lines.append("## Issues")
        validate_lines.append("")
        for it in issues[:200]:
            validate_lines.append(f"- {it.get('severity')}: {it.get('code')}: {it.get('message')}")
    _write_text(validate_path, "\n".join(validate_lines).rstrip() + "\n")
    _write_text(latest_validate, "\n".join(validate_lines).rstrip() + "\n")

    changed = 0
    kept = 0
    fallback = 0
    neg_warn = 0
    for r in seg_results:
        if r["status"] == "ok":
            changed += 1
        elif r["status"] == "fallback_ok":
            changed += 1
            fallback += 1
        elif r["status"] == "kept_original":
            kept += 1
        if r.get("negation_maybe_changed"):
            neg_warn += 1

    summary_lines: List[str] = []
    summary_lines.append("# JP Polish change summary")
    summary_lines.append("")
    summary_lines.append(f"- run_id: {run_id}")
    summary_lines.append(f"- input_sha256: {_sha256(original_norm)}")
    summary_lines.append(f"- proposed_sha256: {_sha256(proposed)}")
    summary_lines.append(f"- model: {args.model} (fallback={args.fallback_model})")
    summary_lines.append(f"- segments_total: {len(seg_results)}")
    summary_lines.append(f"- segments_changed: {changed} (fallback_used={fallback})")
    summary_lines.append(f"- segments_kept_original: {kept}")
    summary_lines.append(f"- negation_warn_segments: {neg_warn}")
    summary_lines.append("")
    summary_lines.append("## Notes")
    summary_lines.append("")
    summary_lines.append("- 正本（Aテキスト）には未適用です（提案のみ）。diff を見て人間が採用してください。")
    summary_lines.append("- validate がNGの場合は、採用しない（または該当区間を手動修正）。")
    _write_text(summary_path, "\n".join(summary_lines).rstrip() + "\n")
    _write_text(latest_summary, "\n".join(summary_lines).rstrip() + "\n")

    run_meta = {
        "run_id": run_id,
        "utc": _utc_now_iso(),
        "channel": ch,
        "video": vid,
        "input_path": str(in_path),
        "output_dir": str(out_dir),
        "ollama_url": str(args.ollama_url).rstrip("/"),
        "model": args.model,
        "fallback_model": args.fallback_model,
        "temperature": args.temperature,
        "timeout_sec": args.timeout_sec,
        "min_len_ratio": args.min_len_ratio,
        "max_len_ratio": args.max_len_ratio,
        "pause_markers_same_count": pause_ok,
        "validator_issues": issues,
        "validator_stats": stats,
    }
    _write_text(meta_path, json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n")
    _write_text(latest_meta, json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n")

    with log_path.open("w", encoding="utf-8") as f:
        for r in seg_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    _write_text(latest_log, log_path.read_text(encoding="utf-8"))

    print(str(proposed_path))
    # Fail-fast policy (SSOT): treat validate NG as a failed proposal.
    # We still persist artifacts for debugging, but signal failure to callers/batch runners.
    if (not pause_ok) or errors:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
