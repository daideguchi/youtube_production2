import json
import argparse
from pathlib import Path
from typing import List, Dict, Any
import os
from factory_common.llm_router import get_router
from factory_common.paths import logs_root
from datetime import datetime
from datetime import timezone

# Config
BATCH_SECTIONS = 20  # number of sections per call (tune as needed)
CONF_THRESHOLD = 0.9
FORCE_SURFACES = {"NO", "SNS", "微調整", "肩甲骨"}


def chunk_sections(sections: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [sections[i : i + size] for i in range(0, len(sections), size)]

def _is_think_or_agent_mode() -> bool:
    return (os.getenv("LLM_MODE") or "").strip().lower() in ("think", "agent")


def _parse_router_response(content: Any) -> Dict[str, Any]:
    """
    Router からの返却が文字列の場合も dict の場合も吸収して decisions を含む dict にそろえる。
    """
    # Already a dict with 'decisions'
    if isinstance(content, dict):
        if "decisions" in content:
            return content
        # OpenAI-like chat structure
        try:
            choices = content.get("choices")
            if choices:
                msg = choices[0]["message"]["content"]
                return json.loads(msg)
        except Exception:
            pass
    # String
    if isinstance(content, str):
        text = content.strip()
        if not text:
            raise ValueError("empty response text")
        # If it looks like JSON, parse directly
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        # Otherwise try to load after forcing JSON
        return json.loads(text)
    raise ValueError(f"unsupported response type: {type(content)}")


def run_batch(channel: str, video: str, sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    router = get_router()
    payload = {
        "channel": channel,
        "video": video,
        "sections": sections,
    }
    sys_prompt = (
        "あなたは日本語TTS用の読みチェック専用アシスタントです。"
        "各セクションの text と、その中の読みが怪しい候補トークンが渡されます。"
        "各候補について、文脈を読んで action を決めてください。\n"
        "action: fix|keep|ambiguous。fix のときのみ reading をカタカナで入れる。\n"
        "ルール: 明らかな誤読のみ fix。アクセント/長音の揺れや微妙な差は keep。文脈で揺れるなら ambiguous。迷ったら ambiguous。"
        "出力は JSON オブジェクトのみで、必ず {\"channel\":...,\"video\":...,\"decisions\":[...]} の形式にすること。"
    )
    user_prompt = json.dumps(payload, ensure_ascii=False)
    call_with_raw = getattr(router, "call_with_raw", None)
    if callable(call_with_raw):
        resp = call_with_raw(
            task="tts_reading",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=6000,
            timeout=120,
            response_format=None,  # raw text
        )
        content = resp.get("content")
        meta = {k: resp.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")}
        return {"decisions": _parse_router_response(content).get("decisions", []), "llm_meta": meta}
    else:
        content = router.call(
            task="tts_reading",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=6000,
            timeout=120,
            response_format=None,  # raw text
        )
        return {"decisions": _parse_router_response(content).get("decisions", [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="Path to reading_candidates.json")
    ap.add_argument("--out", required=False, help="Path to decisions output (default: same dir / contextual_decisions.json)")
    ap.add_argument("--log-jsonl", required=False, help="Path to append JSONL log (default: workspaces/logs/tts_voicevox_reading.jsonl)")
    args = ap.parse_args()

    cand_path = Path(args.candidates)
    data = json.loads(cand_path.read_text(encoding="utf-8"))
    channel = data.get("channel")
    video = data.get("video")
    sections = data.get("sections", [])

    # THINK/AGENT mode: avoid multiple stop/resume loops by issuing a single task.
    # In API mode, keep chunking to avoid context/token blowups.
    batches = [sections] if _is_think_or_agent_mode() else chunk_sections(sections, BATCH_SECTIONS)
    decisions: List[Dict[str, Any]] = []
    llm_meta_logs: List[Dict[str, Any]] = []
    for batch in batches:
        # 最大3回までリトライして、ダメならそのバッチをスキップ
        ok = False
        for _ in range(3):
            try:
                res = run_batch(channel, video, batch)
                decisions.extend(res.get("decisions", []))
                meta = res.get("llm_meta")
                if meta:
                    llm_meta_logs.append(meta)
                ok = True
                break
            except Exception as e:
                err_msg = str(e)
                continue
        if not ok:
            print(f"[WARN] batch skipped after retries")

    out_path = Path(args.out) if args.out else cand_path.parent / "contextual_decisions.json"
    out_path.write_text(
        json.dumps(
            {
                "schema": "ytm.contextual_reading_decisions.v1",
                "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "source_candidates": {"path": str(cand_path)},
                "channel": channel,
                "video": video,
                "decisions": decisions,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[LLM] decisions={len(decisions)} -> {out_path}")
    # log meta if any
    if llm_meta_logs:
        try:
            log_path = logs_root() / "tts_voicevox_reading.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                for m in llm_meta_logs:
                    f.write(json.dumps({"task": "tts_reading", **m}, ensure_ascii=False) + "\n")
            print(f"[LLM] meta logged: {len(llm_meta_logs)} entries")
        except Exception:
            pass

    # Build local_token_overrides.json
    overrides: List[Dict[str, Any]] = []
    for d in decisions:
        if (d.get("action") or "").lower() != "fix":
            continue
        conf = float(d.get("confidence", 1.0) or 1.0)
        if conf < CONF_THRESHOLD:
            continue
        reading = d.get("reading") or ""
        if not reading:
            continue
        overrides.append(
            {
                "section_id": d.get("section_id"),
                "token_index": d.get("token_index"),
                "surface": d.get("surface", ""),
                "reading": reading,
                "reason": d.get("reason", ""),
                "confidence": conf,
            }
        )

    override_path = cand_path.parent / "local_token_overrides.json"
    override_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PATCH] local_token_overrides.json written ({len(overrides)} entries)")

    # write JSONL log
    log_jsonl_path = Path(args.log_jsonl) if args.log_jsonl else (logs_root() / "tts_voicevox_reading.jsonl")
    ts = datetime.now().isoformat()
    records = []
    for d in decisions:
        records.append(
            {
                "timestamp": ts,
                "channel": channel,
                "video": video,
                "section_id": d.get("section_id"),
                "token_index": d.get("token_index"),
                "surface": d.get("surface", ""),
                "mecab_kana": "",  # not provided here
                "voicevox_kana": "",
                "ruby_kana": d.get("reading", ""),
                "after_kana": d.get("reading", ""),
                "source": "context_llm",
                "reason": f"action={d.get('action')}|conf={d.get('confidence',0)}|{d.get('reason','')}",
            }
        )
    if records:
        log_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with log_jsonl_path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[LOG] appended {len(records)} records to {log_jsonl_path}")


if __name__ == "__main__":
    main()
