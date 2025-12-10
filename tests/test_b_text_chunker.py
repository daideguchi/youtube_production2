import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.builder import chunk_b_text  # noqa: E402


def test_chunk_b_text_prefers_punctuation_boundary():
    text = "これはテストです。次の文もテストです。さらにもう一文足します。"
    chunks = chunk_b_text(text, max_len=20)
    assert all(len(c["text"]) <= 20 for c in chunks)
    # 句点で切れていることを確認（最初のチャンク末尾が句点）
    assert chunks[0]["text"].endswith("。")


def test_chunk_b_text_force_split_when_no_boundary():
    text = "あ" * 50  # no punctuation, must force split
    chunks = chunk_b_text(text, max_len=20)
    assert len(chunks) == 3
    assert chunks[0]["text"] == "あ" * 20
    assert chunks[1]["text"] == "あ" * 20
    assert chunks[2]["text"] == "あ" * 10
