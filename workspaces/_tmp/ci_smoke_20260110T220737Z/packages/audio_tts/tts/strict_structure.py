from dataclasses import dataclass
from typing import Optional, List, Dict

@dataclass
class AudioSegment:
    """
    音声合成の最小単位。
    Strict Modeではこの単位で読み・間・音声ファイルが確定する。
    """
    text: str                  # 表示用テキスト（漢字混じり）
    reading: Optional[str]     # 合成用テキスト（カタカナ/AquesTalk記法）。Noneならtextを使う（非推奨）
    
    # Timing Control
    pre_pause_sec: float = 0.0  # 前置ポーズ（秒）
    post_pause_sec: float = 0.0 # 後置ポーズ（秒）
    
    # Metadata
    is_heading: bool = False   # 見出し（# / ##）かどうか
    heading_level: int = 0     # 1 (#), 2 (##), etc.
    original_line_index: int = 0 # 元テキストの行番号（デバッグ用）
    
    # Synthesis Result
    wav_path: Optional[str] = None # 生成された一時WAVファイルのパス
    duration_sec: float = 0.0      # 音声自体の長さ（ポーズ含まず）
    
    # Validation info
    mecab_reading: str = ""
    voicevox_reading: str = ""
    arbiter_verdict: str = "" # "mecab", "voicevox", "llm_fixed", or "match"

@dataclass
class PipelineResult:
    wav_path: str
    srt_path: str
    log_path: str
    total_duration: float
    segments: List[AudioSegment]
