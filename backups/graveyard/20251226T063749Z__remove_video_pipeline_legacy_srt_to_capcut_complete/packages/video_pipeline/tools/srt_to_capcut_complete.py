# -*- coding: utf-8 -*-
"""
完全版: SRT分割 + CapCutドラフト自動生成システム
先ほどの解析結果を活用した固定座標配置
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import argparse, re, unicodedata, json, uuid, time, subprocess, base64, shutil, os, copy

TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,\.]\d{3}")
RANGE_RE = re.compile(r"(?:^\s*\d+\s*\n)?\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})", re.M)

@dataclass
class SrtItem:
    idx: int
    start: float
    end: float
    text: str

def to_seconds(t: str) -> float:
    h, m, s_ms = t.split(":")
    if "," in s_ms: s, ms = s_ms.split(",")
    else: s, ms = s_ms.split(".")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0

def seconds_to_microseconds(sec: float) -> int:
    return int(sec * 1000000)

def generate_uuid() -> str:
    return str(uuid.uuid4()).upper()

def fmt_time(sec: float) -> str:
    if sec < 0: sec = 0.0
    ms = int(round((sec - int(sec))*1000))
    tot = int(sec)
    h = tot//3600; m = (tot%3600)//60; s = tot%60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(text: str):
    blocks = re.split(r"\n\s*\n", text.strip(), flags=re.MULTILINE)
    out = []
    for blk in blocks:
        lines = [ln.strip("\ufeff") for ln in blk.strip().splitlines() if ln.strip()!=""]
        if not lines: continue
        if TIME_RE.search(lines[0]):
            rng=lines[0]; texts=lines[1:]
        elif len(lines)>=2 and TIME_RE.search(lines[1]):
            rng=lines[1]; texts=lines[2:]
        else:
            continue
        m = RANGE_RE.search(rng)
        if not m: continue
        st = to_seconds(m.group(1)); en = to_seconds(m.group(2))
        out.append(SrtItem(idx=len(out)+1, start=st, end=en, text="\n".join(texts).strip()))
    out.sort(key=lambda x:(x.start,x.end))
    for i,it in enumerate(out,1): it.idx=i
    return out

def write_srt(items, path: Path):
    with path.open("w", encoding="utf-8") as f:
        for i,it in enumerate(items,1):
            f.write(f"{i}\n{fmt_time(it.start)} --> {fmt_time(it.end)}\n{it.text}\n\n")

FULLWIDTH_DIGITS = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9"
})
REVIEW_HEADER_RE = re.compile(r"口コミ\s*([0-9０-９\-ー－―〜~]+)")

def normalize_digits(text: str) -> str:
    return text.translate(FULLWIDTH_DIGITS)

def extract_review_number(text: str) -> Optional[int]:
    joined = text.replace("\n", "")
    m = REVIEW_HEADER_RE.search(joined)
    if not m:
        return None
    cleaned = re.sub(r"[^0-9０-９]", "", m.group(1))
    num_str = normalize_digits(cleaned)
    return int(num_str) if num_str.isdigit() else None

LINE_HEAD_FORBID = set(list("、。，．・：；！？)]｝〔〕〉》」』ーゝゞ々％"))
LINE_TAIL_FORBID = set(list("（(｛{［[〔〈《「『"))
SMALL_KANA = set(list("ぁぃぅぇぉァィゥェォっッゃゅょャュョゎヮゕゖゝゞ"))
PROLONG = "ー"
UNIT_KANJI = set(list("歳円回％人倍点日年分時間件位匹本枚個冊週ヶ月か月ヶ月目度万億兆％%円税込kgcm㎝mm℃"))

def is_digit(c: str) -> bool:
    return ("0" <= c <= "9") or (c in "０１２３４５６７８９")

def tokenise_jp(s: str):
    tokens = []
    i=0; n=len(s)
    while i<n:
        ch=s[i]
        if ch.isascii() and (ch.isalnum() or ch in ".,/%-+_:@#"):
            j=i+1
            while j<n and s[j].isascii() and (s[j].isalnum() or s[j] in ".,/%-+_:@#"): j+=1
            tokens.append(s[i:j]); i=j; continue
        if is_digit(ch):
            j=i+1
            while j<n and (is_digit(s[j]) or s[j] in ",．.."):
                j+=1
            k=j
            while k<n and (s[k] in UNIT_KANJI):
                k+=1
            tokens.append(s[i:k]); i=k; continue
        tokens.append(ch); i+=1
    return tokens

def char_width(c: str) -> int:
    eaw = unicodedata.east_asian_width(c)
    if eaw in ("F","W","A"): return 2
    return 1

def text_width(s: str) -> int:
    return sum(char_width(c) for c in s)

def _absorb_head_forbid(out_lines, right: str) -> str:
    while right and (right[0] in LINE_HEAD_FORBID or right[0] in SMALL_KANA or right[0]==PROLONG):
        if out_lines and out_lines[-1] and out_lines[-1][-1] not in LINE_TAIL_FORBID:
            out_lines[-1] += right[0]
            right = right[1:]
        else:
            break
    return right

def smart_wrap_jp(text: str, cols: int) -> str:
    if cols<=0: return text
    out_lines = []
    for src_line in text.split("\n"):
        if src_line=="" or text_width(src_line) <= cols:
            out_lines.append(src_line); continue
        toks = tokenise_jp(src_line)
        line=""; last_good_break = 0
        for t in toks:
            cand = line + t
            if text_width(cand) <= cols:
                if len(cand)>0 and cand[-1] not in LINE_TAIL_FORBID:
                    last_good_break = len(cand)
                line = cand
                continue
            break_pos = last_good_break
            for back in range(len(line)-1, -1, -1):
                ch=line[back]
                if ch in "、。！？：；":
                    if text_width(line[:back+1]) <= cols:
                        break_pos = back+1; break
            if break_pos == 0:
                cut = ""
                for i, ch in enumerate(line):
                    if text_width(cut+ch) > cols: break
                    cut += ch
                    if i+1 < len(line):
                        nxtc = line[i+1]
                        if nxtc in SMALL_KANA or nxtc in LINE_HEAD_FORBID or nxtc==PROLONG:
                            continue
                        if ch in LINE_TAIL_FORBID:
                            continue
                        break_pos = i+1
                if break_pos == 0:
                    break_pos = len(cut) if len(cut)>0 else 1
            left = line[:break_pos]; right = line[break_pos:]
            out_lines.append(left)
            right = _absorb_head_forbid(out_lines, right)
            line = right + t
            last_good_break = 0
            while text_width(line) > cols:
                cut = ""; break_pos2 = 0
                for i, ch in enumerate(line):
                    if text_width(cut+ch) > cols: break
                    cut += ch
                    if i+1 < len(line):
                        nxtc = line[i+1]
                        if nxtc in SMALL_KANA or nxtc in LINE_HEAD_FORBID or nxtc==PROLONG:
                            continue
                        if ch in LINE_TAIL_FORBID:
                            continue
                        break_pos2 = i+1
                if break_pos2==0: break_pos2=len(cut) if len(cut)>0 else 1
                out_lines.append(line[:break_pos2])
                line = line[break_pos2:]
                line = _absorb_head_forbid(out_lines, line)
        if line!="":
            out_lines.append(line)
    return "\n".join(out_lines)

def wrap_item(text: str, base_cols: int=48, max_cols: int=200, step: int=8, max_lines:int=8):
    cols = base_cols
    wrapped = smart_wrap_jp(text, cols)
    def count_lines(s): return len(s.split("\n"))
    while count_lines(wrapped) > max_lines and cols < max_cols:
        cols += step
        wrapped = smart_wrap_jp(text, cols)
    return wrapped, cols, count_lines(wrapped)

def build_reset_patterns(name: str, hello_words, audience_words):
    hello = "|".join([re.escape(w) for w in hello_words if w])
    aud = "|".join([re.escape(w) for w in audience_words if w])
    greet1 = re.compile(rf"^(?:{aud})(?:、)?(?:{hello})(?:。|！|!|です)?$")
    greet2 = re.compile(rf"^(?:{hello})(?:、)?(?:{aud})?(?:。|！|!|です)?$")
    name_esc = re.escape(name)
    intro = re.compile(rf"^(?:私は|わたしは)?{name_esc}(?:です|と申します)(?:。|！|!)?$")
    transitions = ["それでは","では","次は","次に","今回は","本日は","ここからは","まずは","続いて","最後に"]
    tran = "|".join([re.escape(w) for w in transitions])
    trans = re.compile(rf"^(?:{tran})(?:、|。|！|!)?$")
    return {"greet1":greet1, "greet2":greet2, "intro":intro, "trans":trans}

NEGATIVE_KEYWORDS = ["口コミ","レビュー","評価","感想","体験","商品","サービス"]

def normalize_line(s: str) -> str:
    return re.sub(r"\s+", "", s.strip())

def is_reset_line(line: str, pats) -> bool:
    t = normalize_line(line)
    if not t: return False
    for ng in NEGATIVE_KEYWORDS:
        if ng in t: return False
    if text_width(t) > 24:
        return False
    if pats["greet1"].match(t): return True
    if pats["greet2"].match(t): return True
    if pats["intro"].match(t):  return True
    if pats["trans"].match(t):  return True
    return False

def is_reset_text(text: str, pats) -> bool:
    for ln in text.split("\n"):
        if is_reset_line(ln, pats): return True
    return False

def subtract_interval(intervals, a: float, b: float):
    out = []
    for st,en in intervals:
        if en <= a or b <= st:
            out.append((st,en))
        else:
            if st < a: out.append((st, max(st, a-0.001)))
            if b < en: out.append((min(en, b+0.001), en))
    return [(st,en) for st,en in out if en-st>0.0005]

def split_headers(headers, suppress):
    result = []
    for h in headers:
        parts = [(h.start, h.end)]
        for a,b in suppress:
            parts = subtract_interval(parts, a, b)
        for st,en in parts:
            result.append(SrtItem(0, st, en, h.text))
    return result

def build_progscene8_with_resets(items, base_cols: int,
                                 section_pattern: str,
                                 name: str,
                                 hello_words,
                                 audience_words):
    sec_re = re.compile(section_pattern)
    pats = build_reset_patterns(name, hello_words, audience_words)

    sections = []
    headers = []
    cur = []
    for it in items:
        if sec_re.search(it.text):
            headers.append(it)
            if cur: sections.append(cur); cur=[]
        else:
            cur.append(it)
    if cur: sections.append(cur)

    raw_headers = []
    for i,h in enumerate(headers):
        st=h.start
        en= items[-1].end if i+1==len(headers) else headers[i+1].start-0.001
        raw_headers.append(SrtItem(0, st, en, h.text))

    slot_tracks = {i:[] for i in range(1,5)}
    scenes = []
    suppress_header_intervals = []

    for sec in sections:
        scene_events = []
        scene_start = None
        scene_sum_lines = 0
        header_visible = True
        for it in sec:
            if scene_start is None:
                scene_start = it.start
                header_visible = True
            if is_reset_text(it.text, pats):
                if scene_events:
                    scenes.append((scene_start, it.start-0.001, scene_events, header_visible))
                    if not header_visible:
                        suppress_header_intervals.append((scene_start, it.start-0.001))
                scene_events = []
                scene_start = it.start
                scene_sum_lines = 0
                header_visible = False
            wrapped, used_cols, lines = wrap_item(it.text, base_cols, max_lines=8)
            if (scene_sum_lines + lines > 8) or (len(scene_events) >= 4):
                scenes.append((scene_start, it.start-0.001, scene_events, header_visible))
                if not header_visible:
                    suppress_header_intervals.append((scene_start, it.start-0.001))
                scene_events = []
                scene_start = it.start
                scene_sum_lines = 0
                header_visible = True
            slot = len(scene_events)+1 if len(scene_events)<4 else 4
            new_item = SrtItem(0, it.start, it.end, wrapped)
            scene_events.append((slot, new_item, lines))
            scene_sum_lines += lines
        if scene_events:
            scenes.append((scene_start, sec[-1].end, scene_events, header_visible))
            if not header_visible:
                suppress_header_intervals.append((scene_start, sec[-1].end))
    for st,en,evs,header_visible in scenes:
        for slot, it, _ in evs:
            it.end = max(it.end, en)
            slot_tracks[slot].append(it)
    header_items = split_headers(raw_headers, suppress_header_intervals)
    for s in slot_tracks:
        tr = slot_tracks[s]
        tr.sort(key=lambda x:(x.start,x.end))
        for i in range(1,len(tr)):
            if tr[i].start < tr[i-1].end:
                tr[i-1].end = min(tr[i-1].end, tr[i].start-0.001)
    return slot_tracks, header_items, scenes, suppress_header_intervals

DEFAULT_CROP = {
    "upper_left_x": 0.0,
    "upper_left_y": 0.0,
    "upper_right_x": 1.0,
    "upper_right_y": 0.0,
    "lower_left_x": 0.0,
    "lower_left_y": 1.0,
    "lower_right_x": 1.0,
    "lower_right_y": 1.0
}

COVER_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYxLjE5LjEwMQD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/"
    "/xABLAAEBAAAAAAAAAAAAAAAAAAAACAEBAAAAAAAAAAAAAAAAAAAAABABAAAAAAAAAAAAAAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIAWgCgAMBIgACEQADEQD/2gAMAwEAAhEDEQA/"
    "AJ/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAB//9k="
)

DEFAULT_ATTACHMENT_EDITING = {
    "editing_draft": {
        "ai_remove_filter_words": {"enter_source": "", "right_id": ""},
        "ai_shorts_info": {"report_params": "", "type": 0},
        "crop_info_extra": {"crop_mirror_type": 0, "crop_rotate": 0.0, "crop_rotate_total": 0.0},
        "digital_human_template_to_video_info": {"has_upload_material": False, "template_type": 0},
        "draft_used_recommend_function": "",
        "edit_type": 0,
        "eye_correct_enabled_multi_face_time": 0,
        "has_adjusted_render_layer": False,
        "is_open_expand_player": False,
        "is_use_adjust": False,
        "is_use_ai_expand": False,
        "is_use_ai_remove": False,
        "is_use_audio_separation": False,
        "is_use_chroma_key": False,
        "is_use_curve_speed": False,
        "is_use_digital_human": False,
        "is_use_edit_multi_camera": False,
        "is_use_lip_sync": False,
        "is_use_lock_object": False,
        "is_use_loudness_unify": False,
        "is_use_noise_reduction": False,
        "is_use_one_click_beauty": False,
        "is_use_one_click_ultra_hd": False,
        "is_use_retouch_face": False,
        "is_use_smart_adjust_color": False,
        "is_use_smart_body_beautify": False,
        "is_use_smart_motion": False,
        "is_use_subtitle_recognition": False,
        "is_use_text_to_audio": False,
        "material_edit_session": {"material_edit_info": [], "session_id": "", "session_time": 0},
        "paste_segment_list": [],
        "profile_entrance_type": "",
        "publish_enter_from": "",
        "publish_type": "",
        "single_function_type": 0,
        "text_convert_case_types": [],
        "version": "1.0.0",
        "video_recording_create_draft": ""
    }
}

DEFAULT_ATTACHMENT_PC_COMMON = {
    "ai_packaging_infos": [],
    "ai_packaging_report_info": {
        "caption_id_list": [],
        "commercial_material": "",
        "material_source": "",
        "method": "",
        "page_from": "",
        "style": "",
        "task_id": "",
        "text_style": "",
        "tos_id": "",
        "video_category": ""
    },
    "broll": {
        "ai_packaging_infos": [],
        "ai_packaging_report_info": {
            "caption_id_list": [],
            "commercial_material": "",
            "material_source": "",
            "method": "",
            "page_from": "",
            "style": "",
            "task_id": "",
            "text_style": "",
            "tos_id": "",
            "video_category": ""
        }
    },
    "commercial_music_category_ids": [],
    "pc_feature_flag": 0,
    "recognize_tasks": [],
    "reference_lines_config": {
        "horizontal_lines": [],
        "is_lock": False,
        "is_visible": False,
        "vertical_lines": []
    },
    "safe_area_type": 0,
    "template_item_infos": [],
    "unlock_template_ids": []
}

DEFAULT_DRAFT_AGENCY_CONFIG = {
    "is_auto_agency_enabled": False,
    "is_auto_agency_popup": False,
    "is_single_agency_mode": False,
    "marterials": None,
    "use_converter": False,
    "video_resolution": 720
}

DEFAULT_PERFORMANCE_INFO = {"manual_cancle_precombine_segs": None}

DEFAULT_COMMON_ATTACHMENT_FILES = {
    "aigc_aigc_generate.json": "{}",
    "attachment_gen_ai_info.json": "{}",
    "attachment_plugin_draft.json": "{}",
    "attachment_script_video.json": "{}"
}

def probe_video_metadata(video_path: Path) -> Optional[Dict[str, float]]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path)
    ]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        info = json.loads(raw.decode("utf-8"))
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"⚠️ ffprobe失敗: {video_path} ({exc})")
        return None

    streams = info.get("streams") or []
    if not streams:
        print(f"⚠️ 動画ストリームなし: {video_path}")
        return None

    stream = streams[0]
    width = stream.get("width") or 0
    height = stream.get("height") or 0
    duration_val = stream.get("duration") or info.get("format", {}).get("duration")

    try:
        duration_sec = float(duration_val) if duration_val is not None else 0.0
    except (TypeError, ValueError):
        duration_sec = 0.0

    return {
        "width": int(width) if width else 0,
        "height": int(height) if height else 0,
        "duration_sec": duration_sec
    }

def create_video_material_dict(material_id: str, video_path: Path,
                               width: int, height: int, duration_us: int) -> Dict:
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": dict(DEFAULT_CROP),
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": duration_us,
        "height": height,
        "id": material_id,
        "local_material_id": "",
        "material_id": material_id,
        "material_name": video_path.name,
        "media_path": "",
        "path": str(video_path),
        "remote_url": None,
        "type": "video",
        "width": width
    }

def create_video_segment_dict(material_id: str,
                              start_sec: float,
                              target_duration_sec: float,
                              source_duration_sec: float) -> Dict:
    start_us = seconds_to_microseconds(start_sec)
    target_dur_us = max(1000, seconds_to_microseconds(target_duration_sec))
    source_dur_us = max(1000, seconds_to_microseconds(source_duration_sec))
    return {
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "id": generate_uuid(),
        "material_id": material_id,
        "target_timerange": {
            "start": start_us,
            "duration": target_dur_us
        },
        "common_keyframes": [],
        "keyframe_refs": [],
        "source_timerange": {
            "start": 0,
            "duration": source_dur_us
        },
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [],
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0}
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000}
    }

def ensure_draft_cover(draft_dir: Path):
    cover_file = draft_dir / "draft_cover.jpg"
    if cover_file.exists():
        return
    cover_file.write_bytes(base64.b64decode(COVER_JPEG_BASE64))

def ensure_capcut_registration(capcut_root: Path,
                               draft_dir: Path,
                               draft_data: Dict,
                               video_materials: List[Dict],
                               text_materials: List[Dict],
                               used_template: bool):
    try:
        ensure_draft_cover(draft_dir)
        draft_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"⚠️ カバーファイル作成失敗: {exc}")

    root_meta = capcut_root / "root_meta_info.json"
    if not root_meta.exists():
        return
    try:
        data = json.loads(root_meta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("⚠️ root_meta_info.json の読み込みに失敗しました")
        return

    entries = data.setdefault("all_draft_store", [])
    draft_path_str = str(draft_dir)
    entry = next((e for e in entries if e.get("draft_fold_path") == draft_path_str), None)
    base_entry = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": str(draft_dir / "draft_cover.jpg"),
        "draft_fold_path": draft_path_str,
        "draft_id": draft_data.get("id", ""),
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False,
        "draft_is_web_article_video": False,
        "draft_json_file": str(draft_dir / "draft_info.json"),
        "draft_name": draft_dir.name,
        "draft_new_version": "",
        "draft_root_path": str(capcut_root),
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": 0,
        "tm_draft_cloud_modified": draft_data.get("create_time", 0),
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": 0,
        "tm_draft_cloud_user_id": 0,
        "tm_draft_create": draft_data.get("create_time", 0),
        "tm_draft_modified": draft_data.get("create_time", 0),
        "tm_draft_removed": 0,
        "tm_duration": draft_data.get("duration", 0)
    }

    if entry is None:
        entry = base_entry
        entries.append(entry)
    else:
        entry.update(base_entry)

    if used_template:
        draft_uuid = draft_data.get("id")
        if draft_uuid:
            new_uuid_dir = draft_dir / f"{{{draft_uuid}}}"
            if not new_uuid_dir.exists():
                template_uuid_dirs = [p for p in draft_dir.iterdir()
                                      if p.is_dir() and p.name.startswith("{") and p.name.endswith("}")]
                if template_uuid_dirs:
                    try:
                        template_uuid_dirs[0].rename(new_uuid_dir)
                    except Exception:
                        new_uuid_dir.mkdir(exist_ok=True)
            else:
                pass
    else:
        try:
            common_attachment = draft_dir / "common_attachment"
            common_attachment.mkdir(exist_ok=True)
            for name, content in DEFAULT_COMMON_ATTACHMENT_FILES.items():
                (common_attachment / name).write_text(content, encoding="utf-8")
            resources_dir = draft_dir / "Resources"
            resources_dir.mkdir(exist_ok=True)
            (resources_dir / "audioAlg").mkdir(exist_ok=True)
            digital_human = resources_dir / "digitalHuman"
            digital_human.mkdir(exist_ok=True)
            (digital_human / "audio").mkdir(exist_ok=True)
            (digital_human / "video").mkdir(exist_ok=True)
            (digital_human / "bsinfo").mkdir(exist_ok=True)
            (resources_dir / "videoAlg").mkdir(exist_ok=True)
            (draft_dir / "qr_upload").mkdir(exist_ok=True)
            (draft_dir / "adjust_mask").mkdir(exist_ok=True)
            (draft_dir / "matting").mkdir(exist_ok=True)
            (draft_dir / "smart_crop").mkdir(exist_ok=True)
            (draft_dir / "subdraft").mkdir(exist_ok=True)
            draft_uuid = draft_data.get("id")
            if draft_uuid:
                (draft_dir / f"{{{draft_uuid}}}").mkdir(exist_ok=True)
        except Exception as exc:
            print(f"⚠️ 補助ディレクトリ作成失敗: {exc}")

    draft_settings_path = draft_dir / "draft_settings"
    create_sec = draft_data.get("create_time", 0) // 1_000_000
    draft_settings_path.write_text(
        "[General]\n"
        "cloud_last_modify_platform=mac\n"
        f"draft_create_time={create_sec}\n"
        f"draft_last_edit_time={create_sec}\n"
        "real_edit_keys=0\n"
        "real_edit_seconds=0\n",
        encoding="utf-8"
    )

    draft_backup = draft_dir / "draft_info.json.bak"
    draft_backup.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")

    if not used_template:
        (draft_dir / "template.tmp").write_text(
            json.dumps({
                "canvas_config": {"background": None, "height": 0, "ratio": "original", "width": 0},
                "color_space": -1,
                "config": {
                    "adjust_max_index": 1,
                    "attachment_info": [],
                    "combination_max_index": 1,
                    "export_range": None,
                    "extract_audio_last_index": 1,
                    "lyrics_recognition_id": "",
                    "lyrics_sync": True,
                    "lyrics_taskinfo": [],
                    "maintrack_adsorb": True,
                    "material_save_mode": 0,
                    "multi_language_current": "none",
                    "multi_language_list": [],
                    "multi_language_main": "none",
                    "multi_language_mode": "none",
                    "original_sound_last_index": 1,
                    "record_audio_last_index": 1,
                    "sticker_max_index": 1,
                    "subtitle_keywords_config": None,
                    "subtitle_recognition_id": "",
                    "subtitle_sync": False,
                    "subtitle_taskinfo": [],
                    "system_font_list": [],
                    "use_float_render": False,
                    "video_mute": False,
                    "zoom_info_params": {"offset_x": 0.0, "offset_y": 0.0, "zoom_ratio": 1.0}
                },
                "tracks": []
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        (draft_dir / "template-2.tmp").write_text(
            json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        (draft_dir / "draft_agency_config.json").write_text(
            json.dumps(DEFAULT_DRAFT_AGENCY_CONFIG, ensure_ascii=False), encoding="utf-8"
        )
        (draft_dir / "draft_biz_config.json").write_text("{}", encoding="utf-8")
        (draft_dir / "attachment_editing.json").write_text(
            json.dumps(DEFAULT_ATTACHMENT_EDITING, ensure_ascii=False), encoding="utf-8"
        )
        (draft_dir / "attachment_pc_common.json").write_text(
            json.dumps(DEFAULT_ATTACHMENT_PC_COMMON, ensure_ascii=False), encoding="utf-8"
        )
        (draft_dir / "performance_opt_info.json").write_text(
            json.dumps(DEFAULT_PERFORMANCE_INFO, ensure_ascii=False), encoding="utf-8"
        )

    virtual_store = {
        "draft_materials": [],
        "draft_virtual_store": [
            {
                "type": 0,
                "value": [{
                    "creation_time": 0,
                    "display_name": "",
                    "filter_type": 0,
                    "id": "",
                    "import_time": 0,
                    "import_time_us": 0,
                    "sort_sub_type": 0,
                    "sort_type": 0
                }]
            },
            {
                "type": 1,
                "value": [{"child_id": vm["id"], "parent_id": ""} for vm in video_materials]
            },
            {
                "type": 2,
                "value": []
            }
        ]
    }
    (draft_dir / "draft_virtual_store.json").write_text(
        json.dumps(virtual_store, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    material_lookup: Dict[str, Dict] = {}
    for cat in ("texts", "videos"):
        for mat in draft_data.get("materials", {}).get(cat, []):
            material_lookup[mat["id"]] = mat

    video_ids = {m["id"] for m in video_materials}
    text_ids = {m["id"] for m in text_materials}
    key_values: Dict[str, Dict] = {}
    for track in draft_data.get("tracks", []):
        for seg in track.get("segments", []):
            seg_id = seg.get("id")
            mat_id = seg.get("material_id")
            mat = material_lookup.get(mat_id)
            if not seg_id or not mat:
                continue
            entry = {
                "filter_category": "",
                "filter_detail": "",
                "is_brand": 0,
                "is_from_artist_shop": 0,
                "is_vip": "0",
                "keywordSource": "",
                "materialId": mat_id,
                "materialSubcategory": "local",
                "materialSubcategoryId": "",
                "materialThirdcategory": "インポート",
                "materialThirdcategoryId": "",
                "material_copyright": "",
                "material_is_purchased": "",
                "rank": "5",
                "rec_id": "",
                "requestId": "",
                "role": "",
                "searchId": "",
                "searchKeyword": "",
                "segmentId": seg_id,
                "team_id": "",
                "textTemplateVersion": ""
            }
            if mat_id in video_ids:
                entry.update({
                    "materialCategory": "media",
                    "materialName": os.path.basename(mat.get("path", "")) or mat.get("material_name", "")
                })
            elif mat_id in text_ids:
                entry.update({
                    "materialCategory": "text",
                    "materialName": mat.get("words", "")
                })
            else:
                entry.update({
                    "materialCategory": "material",
                    "materialName": mat.get("material_name", "")
                })
            key_values[seg_id] = entry

    (draft_dir / "key_value.json").write_text(
        json.dumps(key_values, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not used_template:
        (draft_dir / "draft.extra").write_text("{}", encoding="utf-8")

    size = 0
    try:
        for p in draft_dir.rglob("*"):
            if p.is_file():
                size += p.stat().st_size
    except Exception:
        pass
    entry["draft_timeline_materials_size"] = size

    root_meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

# CapCutドラフト生成関数（先ほどの解析結果を活用）
def generate_capcut_draft(header_items: List[SrtItem],
                         slot_tracks: Dict[int, List[SrtItem]],
                         output_dir: Path,
                         project_name: str,
                         review_cues: Optional[List[Tuple[int, float, float]]] = None,
                         review_video_dir: Optional[Path] = None,
                         template_dir: Optional[Path] = None):
    """
    CapCutドラフトを生成
    先ほどの解析で得られた正確な座標を使用:
    - Track 6: header (-0.250, -0.648)
    - Track 7: slot1 (-0.424, -0.278)
    - Track 8: slot2 (-0.337, 0.093)
    - Track 9: slot3 (-0.250, 0.463)
    - Track 10: slot4 (0.301, 0.854)
    """

    # 固定座標（先ほどの解析結果）
    LAYER_POSITIONS = {
        "header": {"x": -0.250, "y": -0.648},
        "slot1": {"x": -0.424, "y": -0.278},
        "slot2": {"x": -0.337, "y": 0.093},
        "slot3": {"x": -0.250, "y": 0.463},
        "slot4": {"x": 0.301, "y": 0.854}
    }

    def create_text_material(text_id: str, content: str):
        return {
            "id": text_id,
            "words": content,
            "font_family": "",
            "font_size": 5.0,
            "font_color": "#FFFFFF",
            "alignment": 0,
            "bold_width": 0.0,
            "italic": False,
            "underline": False,
            "background_color": "",
            "background_alpha": 1.0,
            "border_color": "#000000",
            "border_width": 2.0,
            "shadow": True,
            "shadow_color": "#000000",
            "shadow_offset_x": 1.0,
            "shadow_offset_y": 1.0
        }

    def create_text_segment(material_id: str, start_sec: float, end_sec: float, position: dict):
        return {
            "id": generate_uuid(),
            "material_id": material_id,
            "target_timerange": {
                "start": seconds_to_microseconds(start_sec),
                "duration": seconds_to_microseconds(end_sec - start_sec)
            },
            "visible": True,
            "clip": {
                "transform": {
                    "x": position["x"],
                    "y": position["y"]
                },
                "scale": {
                    "x": 1.0,
                    "y": 1.0
                },
                "rotation": 0.0,
                "alpha": 1.0
            },
            "extra_material_refs": []
        }

    # ドラフトディレクトリ作成
    draft_dir = output_dir / project_name
    used_template = bool(template_dir and template_dir.exists())
    if draft_dir.exists():
        shutil.rmtree(draft_dir)
    if used_template:
        shutil.copytree(template_dir, draft_dir)
    else:
        draft_dir.mkdir(parents=True, exist_ok=True)

    new_text_materials = []
    new_video_materials = []
    review_cues = sorted(review_cues or [], key=lambda x: x[1])
    video_dir = review_video_dir if (review_video_dir and review_video_dir.is_dir()) else None
    if review_cues and review_video_dir and not video_dir:
        print(f"⚠️ 口コミ動画ディレクトリが見つかりません: {review_video_dir}")
    video_segments = []
    video_logs = []
    missing_videos = []

    if used_template:
        draft_file = draft_dir / "draft_info.json"
        draft_data = json.loads(draft_file.read_text(encoding="utf-8"))
        current_time = int(time.time() * 1000000)

        layer_y_targets = {
            "header": LAYER_POSITIONS["header"]["y"],
            "slot1": -0.2777777777777778,
            "slot2": 0.09259259259259259,
            "slot3": 0.46296296296296297,
            "slot4": 0.8537037037037037,
        }
        layer_tracks = {}
        text_keep_ids = set()

        for idx, track in enumerate(draft_data.get("tracks", [])):
            if track.get("type") != "text" or not track.get("segments"):
                for seg in track.get("segments", []):
                    mid = seg.get("material_id")
                    if mid:
                        text_keep_ids.add(mid)
                continue
            base_seg = track["segments"][0]
            y_val = base_seg.get("clip", {}).get("transform", {}).get("y")
            assigned = None
            if y_val is not None:
                for key, target_y in layer_y_targets.items():
                    if abs(y_val - target_y) < 1e-3:
                        assigned = key
                        break
            if assigned:
                layer_tracks[assigned] = {"index": idx, "track": track, "base_segment": copy.deepcopy(base_seg)}
            else:
                for seg in track.get("segments", []):
                    mid = seg.get("material_id")
                    if mid:
                        text_keep_ids.add(mid)

        for key in ["header", "slot1", "slot2", "slot3", "slot4"]:
            if key not in layer_tracks:
                print(f"⚠️ テンプレート内に {key} 用テキストトラックが見つかりません")
                layer_tracks[key] = {"index": None, "track": {"segments": []}, "base_segment": {}}

        slot_x_normalized = -0.5

        def update_layer(layer_name, items, x_override):
            info = layer_tracks[layer_name]
            track = info["track"]
            base_seg = info["base_segment"]
            if not base_seg:
                base_seg = {
                    "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                              "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
                              "transform": {"x": x_override, "y": layer_y_targets[layer_name]}},
                    "target_timerange": {"start": 0, "duration": 0},
                    "source_timerange": None,
                    "extra_material_refs": [],
                    "common_keyframes": [],
                    "keyframe_refs": [],
                    "render_timerange": {"start": 0, "duration": 0},
                    "speed": 1.0,
                    "volume": 1.0,
                    "visible": True,
                }
            new_segments = []
            for item in items:
                text_id = generate_uuid()
                new_text_materials.append(create_text_material(text_id, item.text))
                seg = copy.deepcopy(base_seg)
                seg["id"] = generate_uuid()
                seg["material_id"] = text_id
                seg["target_timerange"] = {
                    "start": seconds_to_microseconds(item.start),
                    "duration": seconds_to_microseconds(max(item.end - item.start, 0.001))
                }
                seg.setdefault("clip", {}).setdefault("transform", {})
                seg["clip"]["transform"]["x"] = x_override
                seg["clip"]["transform"]["y"] = layer_y_targets[layer_name]
                seg["clip"].setdefault("scale", {"x": 1.0, "y": 1.0})
                seg["clip"]["scale"]["x"] = 1.0
                seg["clip"]["scale"]["y"] = 1.0
                seg["clip"]["rotation"] = 0.0
                seg["clip"]["alpha"] = 1.0
                seg["source_timerange"] = {
                    "start": 0,
                    "duration": seconds_to_microseconds(max(item.end - item.start, 0.001))
                }
                seg["extra_material_refs"] = []
                seg["common_keyframes"] = []
                seg["keyframe_refs"] = []
                seg["render_timerange"] = {"start": 0, "duration": 0}
                seg["speed"] = 1.0
                seg["volume"] = 1.0
                seg["visible"] = True
                new_segments.append(seg)
            track["segments"] = new_segments

        header_x = layer_tracks["header"]["base_segment"].get("clip", {}).get("transform", {}).get("x", LAYER_POSITIONS["header"]["x"])
        update_layer("header", header_items, header_x)
        update_layer("slot1", slot_tracks.get(1, []), slot_x_normalized)
        update_layer("slot2", slot_tracks.get(2, []), slot_x_normalized)
        update_layer("slot3", slot_tracks.get(3, []), slot_x_normalized)
        update_layer("slot4", slot_tracks.get(4, []), slot_x_normalized)

        existing_texts = draft_data.get("materials", {}).get("texts", [])
        kept_texts = [m for m in existing_texts if m.get("id") in text_keep_ids]
        draft_data.setdefault("materials", {})["texts"] = kept_texts + new_text_materials

        existing_videos = draft_data["materials"].get("videos", [])
        draft_data["materials"]["videos"] = existing_videos

        if review_cues and video_dir:
            for number, start_sec, end_sec in review_cues:
                clip_path = video_dir / f"{number}.mp4"
                if not clip_path.exists():
                    missing_videos.append(number)
                    continue
                meta = probe_video_metadata(clip_path)
                if not meta:
                    missing_videos.append(number)
                    continue
                meta_duration = meta.get("duration_sec", 0.0) or 0.0
                cue_duration = max(0.001, end_sec - start_sec)
                target_duration = cue_duration if meta_duration <= 0 else min(cue_duration, meta_duration)
                source_duration = target_duration if meta_duration <= 0 else min(meta_duration, target_duration)
                material_id = generate_uuid()
                material_duration = meta_duration if meta_duration > 0 else target_duration
                width = meta.get("width") or 1920
                height = meta.get("height") or 1080
                new_video_materials.append(
                    create_video_material_dict(
                        material_id,
                        clip_path,
                        width,
                        height,
                        seconds_to_microseconds(material_duration)
                    )
                )
                video_segments.append(
                    create_video_segment_dict(material_id, start_sec, target_duration, source_duration)
                )
                video_logs.append((number, clip_path.name, start_sec, start_sec + target_duration,
                                   target_duration < cue_duration - 1e-6))

            if video_segments:
                draft_data["tracks"].insert(0, {
                    "type": "video",
                    "name": "review_videos",
                    "segments": video_segments
                })
                draft_data["materials"]["videos"] = draft_data["materials"].get("videos", []) + new_video_materials

        all_items = header_items + sum(slot_tracks.values(), [])
        total_duration_sec = max([item.end for item in all_items]) if all_items else 0.0
        existing_duration_sec = draft_data.get("duration", 0) / 1000000.0
        if video_segments:
            video_end_sec = max((seg["target_timerange"]["start"] + seg["target_timerange"]["duration"]) / 1000000
                                for seg in video_segments)
            total_duration_sec = max(total_duration_sec, video_end_sec)
        total_duration_sec = max(total_duration_sec, existing_duration_sec)

        draft_data["id"] = generate_uuid()
        draft_data["duration"] = seconds_to_microseconds(total_duration_sec)
        draft_data["create_time"] = current_time
        draft_data.setdefault("last_modified_platform", {}).update({
            "app_id": 359289,
            "app_source": "cc",
            "app_version": "7.1.0",
            "os": "mac"
        })
        draft_data["fps"] = draft_data.get("fps", 30.0)

        draft_file.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")

    else:
        # 新規ドラフトを構築
        NEW_LAYER_POSITIONS = {
            "header": LAYER_POSITIONS["header"],
            "slot1": {"x": -0.5, "y": LAYER_POSITIONS["slot1"]["y"]},
            "slot2": {"x": -0.5, "y": LAYER_POSITIONS["slot2"]["y"]},
            "slot3": {"x": -0.5, "y": LAYER_POSITIONS["slot3"]["y"]},
            "slot4": {"x": -0.5, "y": LAYER_POSITIONS["slot4"]["y"]}
        }

        header_segments = []
        for item in header_items:
            text_id = generate_uuid()
            new_text_materials.append(create_text_material(text_id, item.text))
            header_segments.append(create_text_segment(text_id, item.start, item.end, NEW_LAYER_POSITIONS["header"]))

        text_tracks = []
        if header_segments:
            text_tracks.append({"type": "text", "segments": header_segments})

        for slot_num in range(1, 5):
            slot_segments = []
            for item in slot_tracks.get(slot_num, []):
                text_id = generate_uuid()
                new_text_materials.append(create_text_material(text_id, item.text))
                slot_segments.append(create_text_segment(text_id, item.start, item.end, NEW_LAYER_POSITIONS[f"slot{slot_num}"]))
            if slot_segments:
                text_tracks.append({"type": "text", "segments": slot_segments})

        if review_cues and video_dir:
            for number, start_sec, end_sec in review_cues:
                clip_path = video_dir / f"{number}.mp4"
                if not clip_path.exists():
                    missing_videos.append(number)
                    continue
                meta = probe_video_metadata(clip_path)
                if not meta:
                    missing_videos.append(number)
                    continue
                meta_duration = meta.get("duration_sec", 0.0) or 0.0
                cue_duration = max(0.001, end_sec - start_sec)
                target_duration = cue_duration if meta_duration <= 0 else min(cue_duration, meta_duration)
                source_duration = target_duration if meta_duration <= 0 else min(meta_duration, target_duration)
                material_id = generate_uuid()
                material_duration = meta_duration if meta_duration > 0 else target_duration
                width = meta.get("width") or 1920
                height = meta.get("height") or 1080
                new_video_materials.append(
                    create_video_material_dict(
                        material_id,
                        clip_path,
                        width,
                        height,
                        seconds_to_microseconds(material_duration)
                    )
                )
                video_segments.append(
                    create_video_segment_dict(material_id, start_sec, target_duration, source_duration)
                )
                video_logs.append((number, clip_path.name, start_sec, start_sec + target_duration,
                                   target_duration < cue_duration - 1e-6))

        tracks = []
        if video_segments:
            tracks.append({"type": "video", "name": "review_videos", "segments": video_segments})
        tracks.extend(text_tracks)

        all_items = header_items + sum(slot_tracks.values(), [])
        total_duration_sec = max([item.end for item in all_items]) if all_items else 0.0
        if video_segments:
            video_end_sec = max((seg["target_timerange"]["start"] + seg["target_timerange"]["duration"]) / 1000000
                                for seg in video_segments)
            total_duration_sec = max(total_duration_sec, video_end_sec)

        draft_data = {
            "canvas_config": {
                "background": None,
                "height": 1080,
                "ratio": "original",
                "width": 1920
            },
            "color_space": 0,
            "draft_type": "video",
            "duration": seconds_to_microseconds(total_duration_sec),
            "fps": 30.0,
            "id": generate_uuid(),
            "materials": {
                "texts": new_text_materials,
                "audios": [],
                "videos": new_video_materials,
                "effects": [],
                "stickers": [],
                "images": []
            },
            "tracks": tracks,
            "create_time": int(time.time() * 1000000),
            "last_modified_platform": {
                "app_id": 359289,
                "app_source": "cc",
                "app_version": "7.1.0",
                "os": "mac"
            }
        }

        draft_file = draft_dir / "draft_info.json"
        draft_file.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")

    current_time = draft_data.get("create_time", int(time.time() * 1000000))
    draft_meta = {
        "draft_name": project_name,
        "draft_id": draft_data["id"],
        "tm_draft_create": current_time,
        "tm_draft_modified": current_time,
        "tm_duration": draft_data["duration"]
    }

    # draft_meta_info.json作成
    meta_data = {
        "draft_name": project_name,
        "draft_id": draft_data["id"],
        "tm_draft_create": draft_data["create_time"],
        "tm_draft_modified": draft_data["create_time"],
        "tm_duration": draft_data["duration"]
    }

    meta_file = draft_dir / "draft_meta_info.json"
    with meta_file.open("w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    try:
        ensure_capcut_registration(
            output_dir,
            draft_dir,
            draft_data,
            draft_data.get("materials", {}).get("videos", []),
            draft_data.get("materials", {}).get("texts", []),
            used_template
        )
    except Exception as exc:
        print(f"⚠️ CapCut登録処理で例外が発生: {exc}")

    if video_logs:
        dir_label = str(video_dir if video_dir else review_video_dir)
        print(f"🎞️ 口コミ動画挿入: {len(video_logs)}本 -> {dir_label}")
        for number, name, start_sec, end_sec, trimmed in video_logs:
            note = "（素材端まで使用）" if trimmed else ""
            print(f"   口コミ{number}: {name} {fmt_time(start_sec)} - {fmt_time(end_sec)}{note}")

    if missing_videos:
        missing_str = ", ".join(str(n) for n in sorted(set(missing_videos)))
        print(f"⚠️ 素材未検出: 口コミ{missing_str}")

    return str(draft_dir)

def main():
    ap = argparse.ArgumentParser(description="SRT分割 + CapCutドラフト自動生成")
    ap.add_argument("input", help="入力SRTファイル")
    ap.add_argument("out_prefix", help="出力ファイルのプレフィックス")
    ap.add_argument("--base-cols", type=int, default=48, help="折り返し文字数")
    ap.add_argument("--section-regex", default=r"^口コミ\s*[0-9０-９]+", help="セクション判定正規表現")
    ap.add_argument("--name", default="さちこ", help="話者名")
    ap.add_argument("--hello-words", default="こんばんは,こんにちは,おはようございます", help="挨拶語句")
    ap.add_argument("--audience-words", default="皆さん,みなさん,皆さま,みなさま", help="呼びかけ語")
    default_capcut_dir = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
    ap.add_argument("--capcut-dir", default=str(default_capcut_dir), help="CapCutプロジェクト出力ディレクトリ")
    ap.add_argument("--review-video-dir", default="最新(一括1) 見出しを追加",
                    help="口コミ番号に対応する動画素材ディレクトリ（空文字で無効化）")
    ap.add_argument("--capcut-template", help="CapCutドラフトのテンプレートフォルダ（任意）")

    args = ap.parse_args()

    hello_words = [w.strip() for w in args.hello_words.split(",") if w.strip()]
    audience_words = [w.strip() for w in args.audience_words.split(",") if w.strip()]

    # SRT読み込み・解析
    text = Path(args.input).read_text(encoding="utf-8")
    items = parse_srt(text)
    print(f"📖 SRT読み込み完了: {len(items)}セグメント")

    review_cues = []
    for it in items:
        num = extract_review_number(it.text)
        if num is not None:
            review_cues.append((num, it.start, it.end))
    if review_cues:
        print(f"🔗 口コミ動画候補: {len(review_cues)}箇所検出")

    # 分割SRT生成
    tracks, headers, scenes, suppressed = build_progscene8_with_resets(
        items, base_cols=args.base_cols, section_pattern=args.section_regex,
        name=args.name, hello_words=hello_words, audience_words=audience_words)

    wrote = []

    # 分割SRTファイル出力
    for si, arr in tracks.items():
        if not arr: continue
        p = Path(f"{args.out_prefix}_slot{si}.srt")
        write_srt(arr, p)
        wrote.append(str(p))
        print(f"✅ Slot{si}: {len(arr)}セグメント -> {p}")

    if headers:
        hp = Path(f"{args.out_prefix}_header.srt")
        write_srt(headers, hp)
        wrote.append(str(hp))
        print(f"✅ Header: {len(headers)}セグメント -> {hp}")

    # シーン情報出力
    with Path(f"{args.out_prefix}_scenes.txt").open("w", encoding="utf-8") as f:
        f.write("# Scene Analysis Report\n")
        f.write("# Format: start_time - end_time : blocks=N, lines=N\n\n")
        for st, en, evs, header_visible in scenes:
            total_lines = sum(l for _, _, l in evs)
            f.write(f"{fmt_time(st)} - {fmt_time(en)} : blocks={len(evs)}, lines={total_lines}, header={'visible' if header_visible else 'hidden'}\n")
        f.write("\n# Header suppress intervals\n")
        for st, en in suppressed:
            f.write(f"{fmt_time(st)} - {fmt_time(en)}\n")
    wrote.append(f"{args.out_prefix}_scenes.txt")

    # CapCutドラフト生成
    capcut_dir = Path(args.capcut_dir)
    review_video_dir = Path(args.review_video_dir).expanduser() if args.review_video_dir else None
    template_dir = Path(args.capcut_template).expanduser() if args.capcut_template else None
    project_name = f"{args.out_prefix}_capcut_project"

    try:
        draft_path = generate_capcut_draft(headers, tracks, capcut_dir, project_name,
                                           review_cues=review_cues,
                                           review_video_dir=review_video_dir,
                                           template_dir=template_dir)
        print(f"🎬 CapCutドラフト生成完了: {draft_path}")
        print(f"   📁 draft_info.json: タイムライン定義")
        print(f"   📁 draft_meta_info.json: プロジェクトメタデータ")
        wrote.append(draft_path)
    except Exception as e:
        print(f"❌ CapCutドラフト生成エラー: {e}")

    print("\n🎯 生成完了:")
    for f in wrote:
        print(f"   {f}")

    # 品質チェック
    print("\n🔍 品質チェック:")
    max_lines = max([sum(l for _, _, l in evs) for _, _, evs, _ in scenes])
    print(f"   最大行数: {max_lines}/8 {'✅' if max_lines <= 8 else '❌'}")

    # 禁則文字チェック
    forbidden_start_count = 0
    for slot_items in tracks.values():
        for item in slot_items:
            for line in item.text.split('\n'):
                if line and line[0] in LINE_HEAD_FORBID:
                    forbidden_start_count += 1
    print(f"   禁則違反: {forbidden_start_count}件 {'✅' if forbidden_start_count == 0 else '❌'}")

if __name__ == "__main__":
    main()
