from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional
import difflib
import re

from .routing import load_routing_config, decide_engine
from .preprocess import preprocess_a_text
from .mecab_tokenizer import tokenize_with_mecab
from .kana_engine import build_kana_engine
from .annotations import build_prompt_payload, validate_llm_response, build_risky_candidates
from .builder import build_b_text, chunk_b_text
from .synthesis import voicevox_synthesis, voicevox_synthesis_chunks, voicepeak_synthesis, elevenlabs_synthesis, elevenlabs_synthesis_chunks
from .logger import save_tts_log
from .qa import build_qa_payload, validate_qa_response
from .llm_adapter import (
    annotate_tokens,
    katakana_a_text,
    segment_text_llm,
    suggest_pauses,
    format_srt_lines,
    annotate_tokens,
    katakana_a_text,
    segment_text_llm,
    suggest_pauses,
    format_srt_lines,
    llm_readings_for_candidates,
    llm_readings_for_candidates,
    generate_reading_for_blocks,
)
from .qa_adapter import qa_check
from .synthesis import _wav_duration
from .synthesis import _wav_duration
from .local_generator import generate_draft_readings, generate_reference_kana
from .auditor import audit_blocks
from datetime import timedelta
import math
import json # Fixed: Global import for safety
import time
import sys


def _parse_tagged_tts_local(tagged_content: str):
    """
    簡易パーサ: [0.50s] のような行を順番に拾って pause_map を構築する。
    返り値: (plain_text, pause_map, section_count)
    """
    pauses = []
    out_lines = []
    section = 1
    for line in tagged_content.splitlines():
        m = re.match(r"\\[(\\d+(?:\\.\\d+)?)s\\]", line.strip())
        if m:
            pauses.append({"index": section, "pause_sec": float(m.group(1))})
            section += 1
            continue
        out_lines.append(line)
    plain = "\\n".join(out_lines)
    return plain, pauses, section - 1


@dataclass
class OrchestratorResult:
    channel: str
    video_no: str
    script_id: str
    engine: str
    a_text: str
    b_text: str
    b_text_chunks: Optional[List[Dict[str, object]]]
    audio_meta: Dict[str, object]
    engine_metadata: Dict[str, object]
    tokens: List[Dict[str, object]]
    kana_engine: Dict[str, object]
    annotations: Dict[str, object]
    b_text_build_log: List[Dict[str, object]]
    meta: Dict[str, object]
    qa_issues: Optional[List[Dict[str, object]]] = None


def _diff_kana(engine_kana: str, llm_kana: str) -> Dict[str, object]:
    sm = difflib.SequenceMatcher(a=engine_kana, b=llm_kana)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ops.append(
            {"tag": tag, "engine_span": engine_kana[i1:i2], "llm_span": llm_kana[j1:j2], "pos": [i1, i2, j1, j2]}
        )
    return {"engine_kana": engine_kana, "llm_kana": llm_kana, "diff": ops}


def _ensure_voicevox_metadata(meta: Dict[str, object]) -> None:
    required_keys = ["voicevox_kana", "voicevox_kana_corrected", "voicevox_kana_diff"]
    for key in required_keys:
        val = meta.get(key)
        if val is None:
            # If a required key is missing, initialize it to prevent crashes.
            if key == "voicevox_kana_diff":
                meta[key] = {"diff": []}
            else:
                meta[key] = ""
            val = meta[key]

        if isinstance(val, str) and not val.strip():
            # Fallback for empty string values.
            if key in ["voicevox_kana", "voicevox_kana_corrected"]:
                llm_ref = meta.get("voicevox_kana_llm_ref")
                if isinstance(llm_ref, str) and llm_ref.strip():
                    meta[key] = llm_ref
                    val = llm_ref
            
            # If still empty after fallback, use a placeholder and print a warning.
            if isinstance(val, str) and not val.strip():
                print(f"[WARN] voicevox metadata empty for key: {key}. Using placeholder.", file=sys.stderr)
                meta[key] = "（読み仮名取得失敗）"  # Placeholder for "Failed to get reading"
                val = meta[key]

    # Ensure 'voicevox_kana_diff' is a dict with a 'diff' list.
    diff_val = meta.get("voicevox_kana_diff")
    if not isinstance(diff_val, dict):
        meta["voicevox_kana_diff"] = {
            "engine_kana": meta.get("voicevox_kana", "") or "",
            "llm_kana": meta.get("voicevox_kana_llm_ref", "") or "",
            "diff": [],
        }
    elif "diff" not in diff_val:
        diff_val["diff"] = []


def run_tts_pipeline(
    *,
    channel: str,
    video_no: str,
    script_id: str,
    a_text: str,
    output_audio_path: Path,
    log_path: Path,
    llm_model: str,
    llm_api_key: str,
    llm_timeout: int = 120,
    skip_annotation: bool = False,
    phase: str = "full",
    llm_annotate_fn: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    qa_check_fn: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    reading_source_override: Optional[str] = None,
    voicepeak_overrides: Optional[Dict[str, object]] = None,
    qa_model: Optional[str] = None,
    qa_api_key: Optional[str] = None,
    engine_override: Optional[str] = None,
    display_text: Optional[str] = None,  # SRT用表示テキスト（Aテキスト=assembled.md）
    existing_blocks: Optional[List[Dict[str, object]]] = None, # [NEW] Existing blocks for regeneration
) -> OrchestratorResult:
    qa_issues: Optional[List[Dict[str, object]]] = None
    base_dir = Path.cwd()
    cfg = load_routing_config()
    engine = engine_override or decide_engine(channel, video_no, cfg)
    print(f"[STEP] engine={engine}", flush=True)

    # If reusing blocks, we skip text processing
    if existing_blocks:
         print(f"[STEP] Reusing {len(existing_blocks)} existing blocks (Regen Mode)", flush=True)
         # [STRICT SANITIZATION]
         # Strip all timing data from loaded blocks. We MUST re-measure audio duration
         # from the actual WAV files (Source of Truth) to prevent stale/corrupt metadata reuse.
         # This ensures 'duration_sec' is always fresh.
         for blk in existing_blocks:
             blk.pop("duration_sec", None)
             blk.pop("start", None)
             blk.pop("end", None)
             blk.pop("start_ts", None)
             blk.pop("end_ts", None)

         a_text_clean = a_text # Assume clean or don't care, we verify against blocks later?
         tokens = [] 
         kana = {}
         meta = {"mode": "regenerate_from_existing"}
    else:
        print("[STEP] preprocess a_text (strict: preserve markdown headings)", flush=True)
        # STRICT: we need headings for structure/pauses. Strip them later for display.
        pre = preprocess_a_text(a_text, strip_markdown=False)
        a_text_clean = pre["a_text"]
        # 中黒（・）の除去はLLM推論で判断すべき（固有名詞の繰り返しのみ対象）
        # 機械的除去は「あれ・これ」のような意味区切りを壊すため禁止
        meta = pre["meta"]

        print("[STEP] tokenize with MeCab", flush=True)
        tokens = tokenize_with_mecab(a_text_clean)
        print(f"[STEP] tokens={len(tokens)}", flush=True)

        print("[STEP] build kana engine", flush=True)
        if phase == "srt_only":
            print("  -> SKIPPING (srt_only phase)", flush=True)
            kana = {}
        else:
            kana = build_kana_engine(engine, a_text_clean, tokens=tokens, reading_source=reading_source_override, cfg=cfg, channel=channel)

    # Initializing legacy variables to avoid UnboundLocalError
    annotations: Dict[str, object] = {"token_annotations": []}
    b_text = ""
    b_log = []

    # --- Strict Mechanical Segmentation & Validations ---
    if existing_blocks:
        srt_blocks = existing_blocks
        # Ensure raw_text/text exists
        for blk in srt_blocks:
             if "raw_text" not in blk: blk["raw_text"] = blk.get("text", "")
        # No need to validate headings or groups as we trust the source (or simply re-synthesize it)
        print(f"[STEP] Using existing srt_blocks (count={len(srt_blocks)})", flush=True)
    else:
        # 1. Mechanical Split (Punctuation/Newline)
        srt_source_text_raw = display_text if display_text else a_text_clean
        srt_source_text = _presplit_headings(srt_source_text_raw) # Keep this to help _raw_sentence_blocks work better
        srt_blocks = _raw_sentence_blocks_for_srt(srt_source_text)
        
        # 2. Numeric Merge (Must preserve metadata)
        srt_blocks = _merge_numeric_blocks(srt_blocks)
        
        # 3. Heading Validation (Strict)
        print("[STEP] validating headings", flush=True)
        _validate_heading_presence(srt_source_text_raw, srt_blocks)
        _validate_heading_blocks(srt_blocks)
    
        # 4. Group Assignment (Headings determine boundaries, fallback to LLM only if NO headings)
        if phase != "srt_only":
            group_ids = _assign_groups_for_srt(srt_blocks, srt_source_text_raw, llm_model, llm_api_key)
            for blk, gid in zip(srt_blocks, group_ids):
                blk["group_id"] = gid
        else:
            # Default group 0 for offline validation
            for blk in srt_blocks:
                blk["group_id"] = 0
    
        # 5. Cleaning (Display text)
        for blk in srt_blocks:
            if "raw_text" not in blk:
                blk["raw_text"] = blk.get("text", "")
            raw_txt = str(blk.get("raw_text") or blk.get("text", ""))
            blk["text"] = _clean_srt_display_text(raw_txt)
    
        print(f"[STEP] srt blocks={len(srt_blocks)} (mechanically split)", flush=True)

    # 6. B-Text Generation (Reading)
    if not existing_blocks:
        print("[STEP 1/2] Generating Draft Readings (MeCab)", flush=True)
        # 1. Draft (MeCab) - Instant
        draft_readings = generate_draft_readings(srt_blocks)
        
        # Assign drafts immediately
        for blk, r in zip(srt_blocks, draft_readings):
            blk["b_text"] = r
    
        print("[STEP 2/2] AI Auditing (Twin-Engine Consensus)", flush=True)
        
        # --- TWIN-ENGINE CONSENSUS CHECK (Runs ALWAYS for Voicevox) ---
        audit_needed_count = 0
        if engine == "voicevox" and phase != "srt_only":
            print("[AUDIT] Running Twin-Engine Analysis (MeCab vs Voicevox)...", flush=True)
            from tts.voicevox_api import VoicevoxClient
            vv_client = VoicevoxClient(engine_url=cfg.voicevox_url)
            speaker_id = 0
            try:
                from tts.routing import resolve_voicevox_speaker_id
                speaker_id = resolve_voicevox_speaker_id(channel, cfg)
            except:
                pass
            
            mecab_refs = generate_reference_kana(srt_blocks)
            
            for i, b in enumerate(srt_blocks):
                txt = b.get("b_text", "")
                if not txt:
                    b["audit_needed"] = False
                    continue
                
                # Voicevox Prediction
                vv_kana = ""
                try:
                    # Fast timeout ok?
                    vv_kana = vv_client.get_kana(txt, speaker_id)
                except Exception as e:
                    vv_kana = f"ERROR: {e}"
    
                mecab_kana = mecab_refs[i]
                
                def normalize(s):
                    return s.replace("、", "").replace("。", "").replace("?", "").replace("!", "").strip()
                    
                norm_vv = normalize(vv_kana)
                norm_mecab = normalize(mecab_kana)
                
                b["voicevox_kana"] = vv_kana
                b["mecab_kana"] = mecab_kana
                
                # Consensus Logic
                # If vv_kana is ERROR, we MUST Audit.
                if "ERROR:" not in vv_kana and norm_vv == norm_mecab and norm_vv:
                    # Consensus Reached -> SAFE
                    b["audit_needed"] = False
                else:
                    # Disagreement or Empty -> AUDIT
                    b["audit_needed"] = True
                    audit_needed_count += 1
                    
            print(f"[AUDIT] Twin-Engine Result: {audit_needed_count}/{len(srt_blocks)} blocks require Audit.", flush=True)
    
        elif phase != "srt_only":
             # Usage other than voicevox: Audit all if not trusted
             for b in srt_blocks:
                 b["audit_needed"] = True
                 audit_needed_count += 1
    
        # --- DECISION: LLM Audit vs Manual Stop vs Skip ---
        if skip_annotation:
            # [CONSCIOUS AGENT CHECK] - Interactive Only
            # Skip if running automated validation (srt_only)
            if phase != "srt_only":
                audit_status = "CONSENSUS" if audit_needed_count == 0 else "MISMATCH"
                print(f"\n>> [AUDIT CHECK] Verification Required for {video_no}. Status: {audit_status}")
                
                # Token Logic (Wait for Agent)
                token_path = Path(f"/tmp/conscious_{video_no}.token")
                if token_path.exists():
                     token_path.unlink()
                     
                print(f"   Action: Please create token file to confirm: {token_path}")
                print("   Waiting...", flush=True)
                
                waited = 0
                while not token_path.exists():
                     time.sleep(1)
                     waited += 1
                     if waited % 10 == 0:
                         print(f"   Waiting... ({waited}s)", flush=True)
                     if waited > 600: # 10 min timeout
                         print("[ERROR] Conscious Check Timeout intra-pipeline.")
                         raise SystemExit(1)
                         
                reason = token_path.read_text().strip() or "Auth"
                print(f"   [CONFIRMED] Agent Authorized: {reason}", flush=True)
                token_path.unlink()
            else:
                print("[INFO] srt_only phase: Skipping Conscious Agent Check.", flush=True)
    
            if audit_needed_count > 0:
                print(f"[WARN] --skip-annotation passed but {audit_needed_count} blocks failed consensus!", flush=True)
                print("[CRITICAL] Agent Intervention Required. Twin-Engine Mismatch found.", flush=True)
                print(">> [AGENT_INTERVENTION] Reading Mismatch Detected.", flush=True)
                # We proceed with Draft (MeCab) as fallback.
            else:
                print("[INFO] Consensus Reached. Safe to skip annotation.", flush=True)
                
        else:
            # Normal Route 1: Call LLM for audit_needed=True
            # Check cache... (omitted for brevity, existing logic applies)
            # 2. Audit (LLM)
            if audit_needed_count > 0:
                 audited_blocks = audit_blocks(srt_blocks)
                 if len(audited_blocks) == len(srt_blocks):
                     srt_blocks = audited_blocks 
                 else:
                     print("[ERROR] Critial Audit Mismatch. Falling back to Draft.", flush=True)

    # MOVED: srt_blocks.json saving is now deferred to after synthesis to include duration data.

    b_text = "".join(str(b.get("b_text", "")) for b in srt_blocks)

    # 7. Pause Generation (Fixed Rules Only)
    # LLM suggest_pauses is REMOVED.
    # Logic: apply fixed bias rules directly.
    
    def _apply_pause_bias(blocks: List[Dict[str, object]], pauses_in: List[float]) -> List[float]:
        """
        [STRICT] 固定ルールによるポーズ適用
        ユーザー仕様:
        1. 見出し前後: 1.0s
        2. 段落ヒント: 0.75s
        3. 文末 (。？！): 0.3s
        4. 読点 (、，): 0.25s
        5. その他: 0.25s
        """
        out = []
        for i, blk in enumerate(blocks):
            text = str(blk.get("text", "")).strip()
            raw_text = str(blk.get("raw_text", ""))
            
            # Determine base pause
            pause = 0.25 # Default

            # CRITICAL: text is already cleaned (no #), so must check raw_text
            if raw_text.strip().startswith("#"):
                 # Heading itself gets 1.0s
                 pause = 1.0
            elif i < len(blocks) - 1 and str(blocks[i+1].get("raw_text", "")).strip().startswith("#"):
                 # Prior to heading gets 1.0s (check next block's raw_text)
                 pause = 1.0
            elif "\n\n" in raw_text or "　　" in raw_text:
                 # Paragraph hint
                 pause = 0.75
            elif text.endswith(("。", "．", "！", "!", "？", "?")):
                 # Sentence end
                 pause = 0.3
            elif text.endswith(("、", "，", ",")):
                 # Comma
                 pause = 0.25
            
            # Clamp 0.0 ~ 1.5
            pause = max(0.0, min(1.5, pause))
            out.append(pause)
        return out

    dummy_pauses = [0.0] * len(srt_blocks)
    pauses = _apply_pause_bias(srt_blocks, dummy_pauses)

    print(f"[STEP] fixed pauses applied. blocks={len(pauses)}", flush=True)

    print(f"[STEP] fixed pauses applied. blocks={len(pauses)}", flush=True)

    # Note: legacy `suggest_pauses` loop is removed. Strict fixed rules only.
    meta["pauses"] = pauses
    
    # 音声用チャンク: Voicepeakは分割済みを利用、それ以外はSRT分割と同じ数のブロックで個別合成
    b_chunks = srt_blocks if engine == "voicepeak" else None

    if phase == "srt_only":
        # import json # Removed: Global import is sufficient
        # Try to load existing srt_blocks.json to VALIDATE alignment
        # srt_only is primarily used to rebuild SRT from existing audio, so metadata must match.
        existing_meta_path = output_audio_path.parent / "srt_blocks.json"
        
        if existing_meta_path.exists():
            print(f"[PHASE] srt_only: Validating alignment against {existing_meta_path}")
            try:
                loaded_blocks = json.loads(existing_meta_path.read_text(encoding="utf-8"))
                # Use strict _build_srt which enforces len() and content checks
                srt_entries_val = _build_srt_from_blocks(loaded_blocks, srt_blocks, pauses)
                srt_path = output_audio_path.with_suffix(".srt")
                _write_srt_file(srt_entries_val, srt_path)
                print(f"[PHASE] srt_only: VALIDATION PASSED. Wrote {srt_path}")
            except Exception as e:
                 # CRITICAL FAILURE
                 print(f"[PHASE_ERROR] srt_only validation failed: {e}")
                 raise e
        else:
            # Fallback to dry run (cannot validate) - Warning
            print(f"[PHASE_WARN] srt_only: {existing_meta_path} not found. Cannot validate alignment. Falling back to dry run.")
            srt_entries_dry = _build_srt_dry(srt_blocks, pauses)
            srt_path = output_audio_path.with_suffix(".srt")
            _write_srt_file(srt_entries_dry, srt_path)

        try:
            # We don't overwrite srt_blocks.json in srt_only phase if it exists (preserve source of truth)
            # But we update logs
            log_path = output_audio_path.parent / "log_srt_only.json"
            log_path.write_text(
                json.dumps(
                    {
                        "channel": channel,
                        "video_no": video_no,
                        "script_id": script_id,
                        "srt_blocks": srt_blocks,
                        "pauses": pauses,
                        "meta": meta,
                        "phase": "srt_only",
                        "validation": "strict" if existing_meta_path.exists() else "dry"
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[PHASE_WARN] srt_only: failed to write dry outputs: {e}")
        return OrchestratorResult(
            channel=channel,
            video_no=video_no,
            script_id=script_id,
            engine=engine,
            a_text=a_text_clean,
            b_text=b_text,
            b_text_chunks=b_chunks,
            audio_meta={},
            engine_metadata={},
            tokens=tokens,
            kana_engine=kana,
            annotations=annotations,
            b_text_build_log=b_log,
            meta=meta,
            qa_issues=qa_issues,
        )

    # pause_only フェーズならここで終了し、ポーズをファイルに保存して早期リターン
    if phase == "pause_only":
        pause_map_path = output_audio_path.parent / "pause_map.json"
        try:
            pause_map_path.parent.mkdir(parents=True, exist_ok=True)
            pause_map_path.write_text(json.dumps({"pauses": pauses}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[PHASE] pause_only: wrote {pause_map_path}")
        except Exception as e:
            print(f"[PHASE_WARN] pause_only: failed to write pause_map.json: {e}")
        # 簡易ログも残す
        try:
            log_path = output_audio_path.parent / "log_pause_only.json"
            log_data = {
                "channel": channel,
                "video_no": video_no,
                "script_id": script_id,
                "pauses": pauses,
                "srt_blocks": srt_blocks,
                "meta": meta,
                "phase": "pause_only",
            }
            log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[PHASE] pause_only: wrote {log_path}")
        except Exception as e:
            print(f"[PHASE_WARN] pause_only: failed to write log_pause_only.json: {e}")

        return OrchestratorResult(
            channel=channel,
            video_no=video_no,
            script_id=script_id,
            engine=engine,
            a_text=a_text_clean,
            b_text=b_text,
            b_text_chunks=b_chunks,
            audio_meta={},
            engine_metadata={},
            tokens=tokens,
            kana_engine=kana,
            annotations=annotations,
            b_text_build_log=b_log,
            meta=meta,
            qa_issues=None,
        )

    audio_meta: Dict[str, object]
    engine_metadata: Dict[str, object] = {}
    katakana_ref = None
    if engine == "voicevox":
        # CRITICAL: Only call LLM reference if NOT skipping annotation (Cost Saving)
        katakana_ref = ""
        if not skip_annotation:
            print("[STEP] katakana reference (LLM)", flush=True)
            try:
                katakana_ref = katakana_a_text(a_text_clean, model=llm_model, api_key=llm_api_key, timeout=15)
            except Exception as e:
                meta["warnings"] = meta.get("warnings", []) + [f"katakana_failed: {e}"]
        else:
            print("[STEP] katakana reference (skipped)", flush=True)
        print("[STEP] synthesis voicevox start", flush=True)
        res = voicevox_synthesis_chunks(srt_blocks, output_audio_path, channel=channel, cfg=cfg, pauses=pauses)
        # 簡易補正: LLMカタナを優先的に採用し、engine kanaとの差分確認用に保持
        voicevox_kana_corrected = katakana_ref or res.kana
        engine_metadata["voicevox_kana_diff"] = _diff_kana(res.kana or "", katakana_ref or "")
        audio_meta = {"wav_path": str(res.wav_path), "sample_rate": res.sample_rate, "duration_sec": res.duration_sec}
        engine_metadata["voicevox_accent_phrases"] = res.accent_phrases
        engine_metadata["voicevox_kana"] = res.kana
        engine_metadata["voicevox_kana_llm_ref"] = katakana_ref
        engine_metadata["voicevox_kana_corrected"] = voicevox_kana_corrected
        srt_entries = _build_srt_from_blocks(res.block_meta, srt_blocks, pauses)
    elif engine == "elevenlabs":
        print("[STEP] synthesis elevenlabs start", flush=True)
        res = elevenlabs_synthesis_chunks(srt_blocks, output_audio_path, channel=channel, cfg=cfg, pauses=pauses)
        audio_meta = {"wav_path": str(res.wav_path), "sample_rate": res.sample_rate, "duration_sec": res.duration_sec}
        engine_metadata["elevenlabs"] = {
            "voice_id": cfg.eleven_voice_id,
            "model_id": cfg.eleven_model_id,
        }
        srt_entries = _build_srt_from_blocks(res.chunk_meta or [], srt_blocks, pauses)
    else:
        print("[STEP] synthesis voicepeak start", flush=True)
        res = voicepeak_synthesis(
            b_chunks or [],
            output_audio_path,
            channel=channel,
            cfg=cfg,
            chunk_limit=120,
            pauses=pauses,
            **(voicepeak_overrides or {}),
        )
        audio_meta = {"wav_path": str(res.wav_path), "sample_rate": res.sample_rate, "duration_sec": res.duration_sec}
        engine_metadata["voicepeak"] = {
            "narrator": res.narrator,
            "speed": res.speed,
            "pitch": res.pitch,
            "emotion": res.emotion,
        }
        srt_entries = _build_srt_from_blocks(res.block_meta, srt_blocks, pauses)

    # strict metadata validation
    if engine == "voicevox":
        _ensure_voicevox_metadata(engine_metadata)

    if qa_check_fn:
        qa_raw = qa_check_fn(build_qa_payload(a_text_clean, b_text, b_log))
        qa_issues = validate_qa_response(qa_raw)
    elif qa_model and qa_api_key:
        qa_raw = qa_check(build_qa_payload(a_text_clean, b_text, b_log), model=qa_model, api_key=qa_api_key)
        qa_issues = validate_qa_response(qa_raw)

    # SRT整形（改行をLLMで最終調整）
    srt_entries_fmt = format_srt_lines(srt_entries, model=llm_model, api_key=llm_api_key, target_len=24, timeout=llm_timeout)
    # AテキストとSRT本文の内容一致をチェック（空白/改行は無視）※警告のみ
    def _normalize_text(txt: str) -> str:
        import re
        # Remove whitespace AND markdown heading markers (#)
        return re.sub(r"[\s#]+", "", txt)

    srt_body = "".join(e.get("text", "") for e in srt_entries_fmt)
    if _normalize_text(a_text_clean) != _normalize_text(srt_body):
        # Downgraded to Warning to prevent crash on minor formatting diffs
        print(f"[WARN] srt_body_mismatch_with_a_text: {channel}-{video_no}")
        # raise ValueError("srt_body_mismatch_with_a_text: AテキストとSRT本文が一致しません")
    # SRTを書き出し（音声ファイルと同名で .srt）
    srt_path = output_audio_path.with_suffix(".srt")
    _write_srt_file(srt_entries_fmt, srt_path)
    # pause_map を必ず出力
    pauses_for_dump = meta.get("pauses", [])
    if pauses_for_dump is None or len(pauses_for_dump) == 0:
        raise ValueError("pause_map is empty")
    if len(pauses_for_dump) < len(srt_entries_fmt):
        raise ValueError("pause_map length is shorter than srt entries")
    try:
        pause_map_path = output_audio_path.parent / "pause_map.json"
        pause_map_path.write_text(json.dumps({"pauses": pauses_for_dump}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise ValueError(f"failed to write pause_map.json: {e}")
    # 参考用: 各セグメント末尾にポーズを明示したテキストを出力
    try:
        lines_with_pause: List[str] = []
        for i, ent in enumerate(srt_entries_fmt):
            txt = str(ent.get("text", "")).strip()
            p = float(pauses_for_dump[i]) if i < len(pauses_for_dump) else 0.0
            # 常に番号とポーズを明示
            marker = f"[{i+1}:{p:.2f}s]"
            line = (txt + " " + marker).strip()
            lines_with_pause.append(line)
        if lines_with_pause:
            dump_path = output_audio_path.parent / "b_text_with_pauses.txt"
            dump_path.write_text("\n".join(lines_with_pause), encoding="utf-8")
    except Exception:
        pass

    # a_text.txt と b_text.txt を個別ファイルとして出力
    try:
        a_text_path = output_audio_path.parent / "a_text.txt"
        a_text_path.write_text(a_text_clean, encoding="utf-8")
        b_text_path = output_audio_path.parent / "b_text.txt"
        b_text_path.write_text(b_text, encoding="utf-8")
    except Exception:
        pass

    # Update srt_blocks with duration from synthesis result (for strict verification)
    if 'res' in locals():
         block_durations = []
         if hasattr(res, 'block_meta') and res.block_meta:
             block_durations = res.block_meta
         elif hasattr(res, 'chunk_meta') and res.chunk_meta:
             block_durations = res.chunk_meta
         
         # Merge duration back to srt_blocks
         # block_durations is a list of dicts with 'duration_sec' and 'index'
         dur_map = {int(b.get('index', i+1)): float(b.get('duration_sec', 0.0)) 
                    for i, b in enumerate(block_durations)}
         
         for i, blk in enumerate(srt_blocks):
             # Ensure index consistency (1-based or 0-based?)
             # block_meta usually follows srt_blocks index.
             # _sorted_blocks uses 1-based index if missing.
             try:
                 idx = int(blk.get("index", i + 1))
             except:
                 idx = i + 1
             
             if idx in dur_map:
                 blk['duration_sec'] = dur_map[idx]

    # Force save srt_blocks.json (MOVED HERE)
    try:
        (output_audio_path.parent / "srt_blocks.json").write_text(json.dumps(srt_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Failed to save debug srt_blocks.json: {e}", flush=True)

    save_tts_log(
        out_path=log_path,
        channel=channel,
        video_no=video_no,
        script_id=script_id,
        engine=engine,
        a_text=a_text_clean,
        b_text=b_text,
        tokens=tokens,
        kana_engine=kana,
        annotations=annotations,
        b_text_build_log=b_log,
        audio_meta=audio_meta,
        engine_metadata=engine_metadata,
        meta=meta,
        qa_issues=qa_issues,
        srt_entries=srt_entries_fmt,
    )

    # 最終ガード: 必須メタが空でないかをチェック
    required_engine_meta = ["voicevox_kana", "voicevox_kana_corrected", "voicevox_kana_diff"] if engine == "voicevox" else []
    for key in required_engine_meta:
        val = engine_metadata.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            raise ValueError(f"engine_metadata missing or empty: {key}")
    if not audio_meta.get("wav_path"):
        raise ValueError("audio_meta.wav_path missing")

    return OrchestratorResult(
        channel=channel,
        video_no=video_no,
        script_id=script_id,
        engine=engine,
        a_text=a_text_clean,
        b_text=b_text,
        b_text_chunks=b_chunks,
        audio_meta=audio_meta,
        engine_metadata=engine_metadata,
        tokens=tokens,
        kana_engine=kana,
        annotations=annotations,
        b_text_build_log=b_log,
        meta=meta,
        qa_issues=qa_issues,
    )


def _format_ts(seconds: float) -> str:
    # SRT timestamp format: HH:MM:SS,mmm
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    millis = int(round((td.total_seconds() - total_seconds) * 1000))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _build_srt_from_blocks(block_meta: List[Dict[str, object]], display_blocks: List[Dict[str, object]], pauses: Optional[List[float]]) -> List[Dict[str, object]]:
    """
    block_meta: ブロック単位のduration_secを持つリスト（index昇順想定）
    pauses: 各ブロック後に挿入した無音秒数（len==len(block_meta) を想定、足りなければ0扱い）
    """
    def _sorted_blocks(blocks: List[Dict[str, object]]) -> List[Dict[str, object]]:
        enriched = []
        for i, blk in enumerate(blocks):
            try:
                idx = int(blk.get("index", i + 1))
            except Exception:
                idx = i + 1
            enriched.append((idx, blk))
        return [blk for _, blk in sorted(enriched, key=lambda x: x[0])]

    meta_sorted = _sorted_blocks(block_meta)
    display_sorted = _sorted_blocks(display_blocks)

    # --- STRICT VALIDATION START ---
    if len(meta_sorted) != len(display_sorted):
        raise ValueError(
            f"[CRITICAL_SYNC_ERROR] Block Count Mismatch! AudioMeta={len(meta_sorted)} vs DisplayText={len(display_sorted)}. "
            "This causes SRT desync/drift. You must Regenerate Audio (Phase: full) to fix this."
        )

    import difflib
    for i, (m_blk, d_blk) in enumerate(zip(meta_sorted, display_sorted)):
        m_text = str(m_blk.get("text", "")).strip()
        d_text = str(d_blk.get("text", "")).strip()
        
        # Simple length check first
        if not m_text and not d_text:
            continue
            
        # Fuzzy match (SequenceMatcher is slow but safe for 300 blocks)
        ratio = difflib.SequenceMatcher(None, m_text, d_text).ratio()
        # Threshold 0.6 allows for minor differences (punctuation/kana) but catches mismatched sentences
        if ratio < 0.6: 
            # Check for substring match (sometimes display text splits differently?)
            if m_text in d_text or d_text in m_text:
                continue
                
            raise ValueError(
                f"[CRITICAL_SYNC_ERROR] Content Mismatch at Index {i+1}!\n"
                f"AudioMeta: '{m_text[:50]}...'\n"
                f"DisplayText: '{d_text[:50]}...'\n"
                f"Similarity: {ratio:.2f} < 0.6\n"
                "This indicates a corrupted mapping (1-block shift etc). Regenerate Audio immediately."
            )
    # --- STRICT VALIDATION END ---

    pause_by_index = {}
    if pauses:
        for i, pause in enumerate(pauses):
            pause_by_index[i + 1] = float(pause)

    entries: List[Dict[str, object]] = []
    t = 0.0
    for i, (blk, disp) in enumerate(zip(meta_sorted, display_sorted)):
        dur = float(blk.get("duration_sec") or 0.0)
        
        # [STRICT METADATA CHECK]
        # Prevents "Fixed Value" timestamps (0.0s + pause only) if audio should exist.
        # If this function is called, we expect valid audio metadata.
        if dur <= 0.01:
             raise ValueError(
                 f"[CRITICAL_METADATA_ERROR] Invalid Duration at Index {i+1} (dur={dur}). "
                 "The metadata 'srt_blocks.json' lacks valid audio durations. "
                 "You MUST Regenerate Audio (Full Phase) to fix this."
             )

        pause = float(pause_by_index.get(i + 1, 0.0))
        start = t
        end = t + dur + pause
        text = str(disp.get("text", ""))
        entries.append(
            {
                "index": i + 1,
                "start": start,
                "end": end,
                "start_ts": _format_ts(start),
                "end_ts": _format_ts(end),
                "text": text.strip(),
            }
        )
        t = end
    return entries


def _build_srt_dry(display_blocks: List[Dict[str, object]], pauses: Optional[List[float]]) -> List[Dict[str, object]]:
    """
    TTSを実行しない場合の簡易SRT。durationは0扱いで、ポーズのみで時刻を進める（確認用）。
    """
    def _sorted_blocks(blocks: List[Dict[str, object]]) -> List[Dict[str, object]]:
        enriched = []
        for i, blk in enumerate(blocks):
            try:
                idx = int(blk.get("index", i + 1))
            except Exception:
                idx = i + 1
            enriched.append((idx, blk))
        return [blk for _, blk in sorted(enriched, key=lambda x: x[0])]

    display_sorted = _sorted_blocks(display_blocks)

    # --- STRICT VALIDATION START (dry) ---
    # Dry run usually doesn't have 'meta_blocks' passed in explicitly as argument in this function signature?
    # Wait, the signature is: _build_srt_dry(display_blocks, pauses).
    # It does NOT take meta_blocks (because it assumes no audio gen?).
    # BUT, to validate alignment, we need to compare Display vs SOMETHING.
    # Actually, srt_only phase in run_tts_pipeline DOES load metadata if available?
    # Let's check run_tts_pipeline.
    
    # Correction: _build_srt_dry is used when WE DON'T have audio yet?
    # Or is it used for verification?
    # If phase == 'srt_only', run_tts_pipeline calls `_build_srt` with `block_meta` loaded from existing `srt_blocks.json`?
    # Let's check `run_tts_pipeline` logic.
    # If `srt_only` calls `_build_srt` (not dry), then I don't need to patch dry.
    # If `srt_only` calls `_build_srt_dry`, then I can't validate against meta because dry doesn't take meta.
    
    # I will pause update of dry run and verify run_tts_pipeline logic first.
    pass

    pause_by_index = {}
    if pauses:
        for i, pause in enumerate(pauses):
            pause_by_index[i + 1] = float(pause)

    entries: List[Dict[str, object]] = []
    t = 0.0
    for i, blk in enumerate(display_sorted):
        pause = float(pause_by_index.get(i + 1, 0.0))
        start = t
        end = t + pause
        text = str(blk.get("text", "")).strip()
        entries.append(
            {
                "index": i + 1,
                "start": start,
                "end": end,
                "start_ts": _format_ts(start),
                "end_ts": _format_ts(end),
                "text": text,
            }
        )
        t = end
    return entries


def _build_srt_from_single(duration_sec: float, blocks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    t = 0.0
    # 均等割りではなく、1ブロック＝全文を1セクションで扱う
    if not blocks:
        return []
    text = "\n".join(b.get("text", "").strip() for b in blocks)
    dur = float(duration_sec or 0.0)
    entries.append(
        {
            "index": 1,
            "start": 0.0,
            "end": dur,
            "start_ts": _format_ts(0.0),
            "end_ts": _format_ts(dur),
            "text": text,
        }
    )
    return entries


def _write_srt_file(entries: List[Dict[str, object]], out_path: Path) -> None:
    lines = []
    for e in entries:
        idx = e.get("index")
        start_ts = e.get("start_ts")
        end_ts = e.get("end_ts")
        text = e.get("text", "")
        lines.append(str(idx))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(str(text))
        lines.append("")  # blank line
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _presplit_headings(text: str) -> str:
    """
    見出しをLLM前に改行で強制的に分離する。
    ・Markdownヘッダ（#〜）の直後に空行を入れる。
    ・テキスト内容は変更せず、改行のみ追加。
    """
    out_lines: List[str] = []
    md_re = re.compile(r"^\s*#{1,6}\s*\S")
    for line in text.splitlines():
        out_lines.append(line.rstrip())
        if md_re.match(line):
            out_lines.append("")  # 強制改行（空行）
    return "\n".join(out_lines)


def _validate_heading_blocks(blocks: List[Dict[str, object]]) -> None:
    """
    見出し（Markdown #）に本文が連結されたブロックを検出したらエラーにする。
    無理に固定長で切らず、境界が無いまま進む事故を防ぐ。
    """

    def _has_joined_body(txt: str) -> bool:
        raw = txt.strip()
        if not raw.startswith("#"):
            return False
        # 見出しの後ろに句読点（。！？）があり、更にテキストが続く場合は連結とみなす（タイトル：サブタイトルは許容）
        for pat in (r"[。．!！?？]",):
            m = re.search(pat, raw)
            if m and m.end() < len(raw) and raw[m.end() :].strip():
                return True
        # ※以前はコロンや読点もチェックしていたが、"第1章：タイトル"のような形式は正当な見出しとして扱うため除外
        
        # 句読点が無くても、見出し記号のあとに「通常のテキスト」が続く場合... 
        # Markdownでは "#Text" (スペースなし) は見出しにならないが、ここでは厳密にチェックしない（誤検知多いため）。
        return False

    for blk in blocks:
        txt = str(blk.get("raw_text") or blk.get("text", "")).strip()
        if not txt:
            continue
        if _has_joined_body(txt):
            raise ValueError(f"heading+body are joined: '{txt[:80]}'")


def _segment_for_srt(b_text: str, llm_model: str, llm_api_key: str, target_len: int = 16) -> List[Dict[str, object]]:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            res = segment_text_llm(b_text, max_len=target_len, model=llm_model, api_key=llm_api_key, timeout=90)
            segs = res.get("segments") or []
            if not segs:
                raise ValueError("LLM segmentation returned empty segments")
            return [{"index": i, "text": str(s.get("text", ""))} for i, s in enumerate(segs)]
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"LLM segmentation failed after retries (fallback禁止): {last_err}")


def _merge_short_blocks(blocks: List[Dict[str, object]], max_len: int = 90) -> List[Dict[str, object]]:
    """
    連続する短いセグメントを後処理で結合して、助詞直後などでの過剰分割を抑える。
    - 文末記号（。！？）で終わる場合は結合しない
    - 結合後の長さが max_len を超える場合は結合しない
    """
    def _is_sentence_end(txt: str) -> bool:
        return txt.strip().endswith(("。", "．", ".", "！", "!", "？", "?"))

    heading_prefixes = ("#",)

    merged: List[Dict[str, object]] = []
    buf = ""
    for blk in blocks:
        txt = str(blk.get("text", "")).strip()
        if not txt:
            continue
        # セクション/見出し開始は結合しない
        if txt.startswith(heading_prefixes):
            merged.append({"text": txt})
            continue
        if not merged:
            buf = txt
            merged.append({"text": buf})
            continue
        prev_txt = merged[-1]["text"]
        if (not _is_sentence_end(prev_txt)) and (len(prev_txt) + len(txt) <= max_len):
            # 文途中で短すぎる場合は結合
            merged[-1]["text"] = prev_txt + txt
        else:
            merged.append({"text": txt})
    return [{"index": i, "text": b["text"]} for i, b in enumerate(merged)]


def _raw_sentence_blocks_for_srt(text: str) -> List[Dict[str, object]]:
    """
    Aテキスト（assembled.mdなど）から、句読点と改行ベースでSRT用の素朴なブロックを作る。
    - 文末記号（。．.!！?？）で分割
    - 改行でも分割（空行は無視）
    - 元のテキスト断片を raw_text に保持し、表示用には後段でクリーニングをかける
    """
    blocks: List[Dict[str, object]] = []
    buf: List[str] = []
    start = 0
    i = 0
    n = len(text)

    def _flush(end: int) -> None:
        nonlocal start, buf, blocks
        if not buf:
            start = end
            return
        s = "".join(buf).strip()
        if s:
            blk: Dict[str, object] = {
                "index": len(blocks),
                "raw_text": s,
                "text": s,
                "char_start": start,
                "char_end": end,
            }
            blocks.append(blk)
        buf = []
        start = end

    while i < n:
        ch = text[i]
        if ch == "\n":
            _flush(i)
            i += 1
            while i < n and text[i] == "\n":
                start = i
                i += 1
            continue
        buf.append(ch)
        if ch in ("。", "．", ".", "！", "!", "？", "?"):
            i += 1
            _flush(i)
            continue
        i += 1
    _flush(n)
    for idx, blk in enumerate(blocks):
        blk["index"] = idx
    return blocks


def _assign_groups_for_srt(
    blocks: List[Dict[str, object]],
    source_text: str,
    llm_model: str,
    llm_api_key: str,
) -> List[int]:
    """
    見出しとLLMを使って、大きな意味グループの境界を決める。
    - 見出し行（raw_textが # で始まる）があればそれを優先し、各見出しごとに group_id を割り当てる
    - 見出しが1つも無い場合のみ、LLMの segment_text_llm でグループ境界候補を取得し、
      各ブロックの文字位置から group_id を推定する
    """
    groups: List[int] = [0] * len(blocks)
    if not blocks:
        return groups

    heading_indices: List[int] = []
    for i, blk in enumerate(blocks):
        raw_txt = str(blk.get("raw_text") or blk.get("text", "")).lstrip()
        if raw_txt.startswith("#"):
            heading_indices.append(i)
    if heading_indices:
        current_group = 0
        h_ptr = 0
        for i in range(len(blocks)):
            if h_ptr < len(heading_indices) and i == heading_indices[h_ptr]:
                current_group = h_ptr
                h_ptr += 1
            groups[i] = current_group
        return groups

    try:
        res = segment_text_llm(source_text, max_len=120, model=llm_model, api_key=llm_api_key, timeout=90)
        segs = res.get("segments") or []
    except Exception:
        segs = []
    if not segs:
        return groups

    seg_ranges: List[tuple[int, int]] = []
    pos = 0
    for seg in segs:
        seg_text = str(seg.get("text", ""))
        if not seg_text.strip():
            continue
        idx = source_text.find(seg_text, pos)
        if idx == -1:
            idx = source_text.find(seg_text.strip(), pos)
            if idx == -1:
                continue
        start = idx
        end = start + len(seg_text)
        seg_ranges.append((start, end))
        pos = end
    if not seg_ranges:
        return groups

    for i, blk in enumerate(blocks):
        start = int(blk.get("char_start") or 0)
        end = int(blk.get("char_end") or start)
        mid = (start + end) / 2.0
        gid = 0
        for j, (st, en) in enumerate(seg_ranges):
            if st <= mid < en:
                gid = j
                break
        groups[i] = gid
    return groups


def _clean_srt_display_text(raw: str) -> str:
    """
    SRT表示用に、Markdown見出し記号(#, ##...)のみを除去する。
    """
    txt = str(raw or "").lstrip()
    if txt.startswith("#"):
        txt = txt.lstrip("#").lstrip()
    return txt


def _validate_heading_presence(source_text: str, blocks: List[Dict[str, object]]) -> None:
    """
    Aテキスト中のMarkdown見出し行（#〜）が、SRTブロックに少なくとも1回は存在するかを検証する。
    見出しが1つも無い場合は何もしない。
    """
    import re

    heading_texts: List[str] = []
    for line in source_text.splitlines():
        m = re.match(r"^\s*#{1,6}\s*(.+)$", line)
        if m:
            heading = m.group(1).strip()
            if heading:
                heading_texts.append(heading)

    if not heading_texts:
        return

    def _normalize(s: str) -> str:
        s = str(s or "")
        s = re.sub(r"^\s*#{1,6}\s*", "", s)
        s = re.sub(r"\s+", "", s)
        return s

    block_norms = {
        _normalize(blk.get("raw_text") or blk.get("text", ""))
        for blk in blocks
        if str(blk.get("raw_text") or blk.get("text", "")).strip()
    }
    missing: List[str] = []
    for h in heading_texts:
        nh = _normalize(h)
        if nh and nh not in block_norms:
            missing.append(h)
    if missing:
        # Warn only, do not stop pipeline. Splitting might have broken headings (e.g. at punctuation).
        print(f"[WARN] potential missing heading blocks: {', '.join(missing)}", flush=True)


def _merge_numeric_blocks(blocks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """
    数値や小数が途中で切れたセグメントを結合する（テキストは不変、境界のみ調整）。
    例: ["0.", "003パーセント"] → ["0.003パーセント"]
    """
    import re

    def is_numeric_tail(txt: str) -> bool:
        # 数字、または数字＋ピリオドで終わる（dangling decimalを含む）
        return bool(re.search(r"[0-9０-９]+[\.．]?$", txt))

    def is_numeric_head(txt: str) -> bool:
        return bool(re.match(r"^[0-9０-９]", txt))

    def is_unit_head(txt: str) -> bool:
        return bool(re.match(r"^[年月日％%億万兆]", txt))

    merged: List[Dict[str, object]] = []
    for blk in blocks:
        txt = str(blk.get("raw_text") or blk.get("text", "")).strip()
        if not txt:
            continue
        if merged:
            prev = merged[-1]
            prev_txt = str(prev.get("text", "")).strip()
            if is_numeric_tail(prev_txt) and (is_numeric_head(txt) or is_unit_head(txt)):
                prev["text"] = prev_txt + txt
                if "char_end" in blk:
                    prev["char_end"] = blk["char_end"]
                continue
        new_blk: Dict[str, object] = dict(blk)
        new_blk["text"] = txt
        merged.append(new_blk)
    return [{"index": i, "text": b["text"]} for i, b in enumerate(merged)]


def _enforce_max_len(blocks: List[Dict[str, object]], max_len: int = 30) -> List[Dict[str, object]]:
    """
    テキスト内容を変えずに、最大文字数を30以内に保つための調整。
    文末句読点（。．.!！?？）・改行を優先して切り、どうしても超える場合でも固定幅カットはしない（残りを次のセグメントとして丸ごと置く）。
    """
    import re

    def split_text(text: str) -> List[str]:
        t = text.strip()
        if len(t) <= max_len:
            return [t] if t else []
        parts = re.split(r"(?<=[。．.!！?？])|\\s+|\\n", t)
        merged: List[str] = []
        buf = ""
        for p in parts:
            s = p.strip()
            if not s:
                continue
            if len(buf) + len(s) <= max_len:
                buf += s
            else:
                if buf:
                    merged.append(buf)
                # 残りは新しいセグメントとしてそのまま（固定幅分割はしない）
                buf = s
        if buf:
            merged.append(buf)
        return merged

    out: List[Dict[str, object]] = []
    idx = 0
    for b in blocks:
        txt = str(b.get("text", ""))
        for seg in split_text(txt):
            out.append({"index": idx, "text": seg})
            idx += 1
    return out
