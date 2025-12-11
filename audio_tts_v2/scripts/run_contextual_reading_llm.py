import json
import argparse
from pathlib import Path
from typing import List, Dict, Any
from factory_common.llm_router import get_router
from datetime import datetime

# Config
BATCH_SECTIONS = 80  # number of sections per call (tune as needed)
CONF_THRESHOLD = 0.9
FORCE_SURFACES = {"NO", "SNS", "微調整", "肩甲骨"}


def chunk_sections(sections: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [sections[i : i + size] for i in range(0, len(sections), size)]


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
    content = router.call(
        task="tts_reading",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=6000,
        timeout=120,
        response_format="json_object",
    )
    return json.loads(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="Path to reading_candidates.json")
    ap.add_argument("--out", required=False, help="Path to decisions output (default: same dir / contextual_decisions.json)")
    ap.add_argument("--log-jsonl", required=False, help="Path to append JSONL log (default: logs/tts_voicevox_reading.jsonl)")
    args = ap.parse_args()

    cand_path = Path(args.candidates)
    data = json.loads(cand_path.read_text(encoding="utf-8"))
    channel = data.get("channel")
    video = data.get("video")
    sections = data.get("sections", [])

    batches = chunk_sections(sections, BATCH_SECTIONS)
    decisions: List[Dict[str, Any]] = []
    for batch in batches:
        res = run_batch(channel, video, batch)
        decisions.extend(res.get("decisions", []))

    out_path = Path(args.out) if args.out else cand_path.parent / "contextual_decisions.json"
    out_path.write_text(json.dumps({"channel": channel, "video": video, "decisions": decisions}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[LLM] decisions={len(decisions)} -> {out_path}")

    # Build local_token_overrides.json
    overrides: List[Dict[str, Any]] = []
    for d in decisions:
        if d.get("action") != "fix":
            continue
        if float(d.get("confidence", 0.0)) < CONF_THRESHOLD:
            continue
        overrides.append(
            {
                "section_id": d.get("section_id"),
                "token_index": d.get("token_index"),
                "surface": d.get("surface"),
                "reading": d.get("reading"),
                "reason": d.get("reason", ""),
                "confidence": d.get("confidence", 0.0),
            }
        )

    override_path = cand_path.parent / "local_token_overrides.json"
    override_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PATCH] local_token_overrides.json written ({len(overrides)} entries)")

    # write JSONL log
    log_jsonl_path = Path(args.log_jsonl) if args.log_jsonl else Path("logs/tts_voicevox_reading.jsonl")
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
