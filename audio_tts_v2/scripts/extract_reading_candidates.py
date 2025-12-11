import json
import argparse
from pathlib import Path
import unicodedata
import difflib
import re

FORCE_SURFACES = {"NO", "SNS", "微調整", "肩甲骨"}
MAX_CANDIDATES = 200


def normalize_kana(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    # ひらがな→カタカナ
    buf = []
    for ch in s:
        cp = ord(ch)
        if 0x3041 <= cp <= 0x3096:
            buf.append(chr(cp + 0x60))
        else:
            buf.append(ch)
    s = "".join(buf)
    s = re.sub(r"[/'_ 　\-]", "", s)
    return s


def calc_suspicion_score(mecab: str, vv: str) -> float:
    mec = normalize_kana(mecab)
    vv_ = normalize_kana(vv)
    if not mec or not vv_:
        return 0.0
    similarity = difflib.SequenceMatcher(None, mec, vv_).ratio()
    mora_diff = abs(len(mec) - len(vv_))
    return (1.0 - similarity) + 0.5 * mora_diff


def looks_like_kanji(surface: str) -> bool:
    return bool(surface and re.search(r"[一-龯々〆ヵヶ]", surface))


def extract_candidates(log_path: Path, out_path: Path, max_candidates: int = MAX_CANDIDATES) -> None:
    data = json.loads(log_path.read_text(encoding="utf-8"))
    channel = data.get("channel")
    video = data.get("video")
    sections_out = []
    all_candidates = []

    for seg in data.get("segments", []):
        section_id = seg.get("section_id")
        text = seg.get("text") or ""
        tokens = seg.get("tokens") or []
        cand_list = []
        for tok in tokens:
            surface = tok.get("surface") or ""
            if not surface:
                continue
            if len(surface) < 2 and surface not in FORCE_SURFACES:
                continue
            if surface not in FORCE_SURFACES and not looks_like_kanji(surface):
                continue
            pos = tok.get("pos") or ""
            # 内容語のみ（助詞・助動詞などは除外）
            if pos and any(x in pos for x in ["助詞", "助動詞", "記号", "フィラー", "感動詞"]):
                continue
            mecab_kana = tok.get("mecab_kana") or tok.get("reading_hira") or surface
            vv_norm = tok.get("voicevox_kana_norm") or tok.get("voicevox_kana") or ""
            suspicion = calc_suspicion_score(mecab_kana, vv_norm)
            cand = {
                "token_index": tok.get("token_index"),
                "surface": surface,
                "pos": pos,
                "mecab_kana": mecab_kana,
                "voicevox_kana_norm": vv_norm,
                "suspicion_score": suspicion,
            }
            cand_list.append(cand)
            all_candidates.append((suspicion, section_id, cand))
        # 後でセクション単位に再構成するので、ここでは candidates を空にしておく
        sections_out.append({"section_id": section_id, "text": text, "candidates": cand_list})

    # スコアで絞る
    all_candidates.sort(key=lambda x: x[0], reverse=True)
    selected_keys = set()
    selected = []
    for score, sid, cand in all_candidates:
        key = (sid, cand.get("token_index"))
        # 強制監視語は無条件採用
        if cand.get("surface") in FORCE_SURFACES:
            pass
        else:
            if len(selected) >= max_candidates:
                break
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected.append((sid, cand))

    # セクションごとに candidates を再構成
    section_map = {s["section_id"]: {"section_id": s["section_id"], "text": s["text"], "candidates": []} for s in sections_out}
    for sid, cand in selected:
        if sid in section_map:
            section_map[sid]["candidates"].append(cand)
    sections_final = [v for v in section_map.values() if v["candidates"]]

    out_data = {"channel": channel, "video": video, "sections": sections_final}
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CANDIDATES] channel={channel} video={video} sections={len(sections_final)} candidates={len(selected)} -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="Path to prepass log.json")
    ap.add_argument("--out", required=False, help="Output path (default: same dir / reading_candidates.json)")
    ap.add_argument("--max", type=int, default=MAX_CANDIDATES, help="Max candidates per video")
    args = ap.parse_args()

    log_path = Path(args.log)
    out_path = Path(args.out) if args.out else log_path.parent / "reading_candidates.json"
    extract_candidates(log_path, out_path, max_candidates=args.max)


if __name__ == "__main__":
    main()
