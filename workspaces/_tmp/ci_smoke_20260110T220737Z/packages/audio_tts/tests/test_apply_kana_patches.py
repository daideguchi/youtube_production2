import unittest
from typing import List, Dict, Any
from audio_tts.tts.reading_structs import KanaPatch
from audio_tts.tts.synthesis import apply_kana_patches

class TestApplyKanaPatches(unittest.TestCase):
    def setUp(self):
        # Setup a mock accent_phrase structure
        # Text: "こんにちは" (5 moras)
        self.accent_phrases = [
            {
                "moras": [
                    {"text": "コ", "consonant": "k", "consonant_length": 0.1, "vowel_length": 0.1},
                    {"text": "ン", "consonant": None, "consonant_length": None, "vowel_length": 0.1},
                    {"text": "ニ", "consonant": "n", "consonant_length": 0.1, "vowel_length": 0.1},
                    {"text": "チ", "consonant": "ch", "consonant_length": 0.1, "vowel_length": 0.1},
                    {"text": "ワ", "consonant": "w", "consonant_length": 0.1, "vowel_length": 0.1},
                ],
                "pause_mora": None
            }
        ]

    def _get_mora_texts(self, phrases=None) -> List[str]:
        target = phrases if phrases is not None else self.accent_phrases
        texts = []
        for ap in target:
            for m in ap["moras"]:
                texts.append(m["text"])
        return texts

    def test_no_patches(self):
        """パッチがなければ変更されないこと"""
        original = self._get_mora_texts()
        patched = apply_kana_patches(self.accent_phrases, [])
        self.assertEqual(self._get_mora_texts(patched), original)

    def test_simple_replacement_match_length(self):
        """長さが合う場合の単純文字置換 (Layer 4 fallback)"""
        # "コンニチワ" -> "オハヨウ_" (5文字) - Just for test
        # Replace indices 0-5 (all)
        patch = KanaPatch(
            block_id=0,
            token_index=0,
            mora_range=(0, 5),
            correct_kana="オハヨウワ", # 5 chars
            correct_moras=None
        )
        patched = apply_kana_patches(self.accent_phrases, [patch])
        
        current = self._get_mora_texts(patched)
        self.assertEqual(current, list("オハヨウワ"))
        
        # Check that phoneme info is reset (need to access patched object directly)
        self.assertIsNone(patched[0]["moras"][0]["consonant"])

    def test_correct_moras_priority(self):
        """correct_moras が指定されている場合、correct_kana より優先されること"""
        # Replace "コン" (0-2) with "キョ" (1 mora? No, assume correct_moras list is provided)
        # Let's say we want to replace "コン" with "コー" (2 moras: コ, ー)
        patch = KanaPatch(
            block_id=0,
            token_index=0,
            mora_range=(0, 2),
            correct_kana="ダミー", # Should be ignored
            correct_moras=["コ", "ー"]
        )
        patched = apply_kana_patches(self.accent_phrases, [patch])
        
        current = self._get_mora_texts(patched)
        # Expect "コ", "ー", "ニ", "チ", "ワ"
        self.assertEqual(current, ["コ", "ー", "ニ", "チ", "ワ"])

    def test_partial_replacement(self):
        """部分的な置換 (真ん中だけ変える)"""
        # "ニチ" (2-4) -> "バン"
        patch = KanaPatch(
            block_id=0,
            token_index=0,
            mora_range=(2, 4),
            correct_kana="バン",
            correct_moras=None
        )
        patched = apply_kana_patches(self.accent_phrases, [patch])
        
        current = self._get_mora_texts(patched)
        # Expect "コ", "ン", "バ", "ン", "ワ"
        self.assertEqual(current, ["コ", "ン", "バ", "ン", "ワ"])

    def test_out_of_bounds_ignored(self):
        """範囲外のパッチは無視され、エラーにならないこと"""
        original = self._get_mora_texts()
        # Range 10-12 is out of bounds (len is 5)
        patch = KanaPatch(
            block_id=0,
            token_index=0,
            mora_range=(10, 12),
            correct_kana="ムリ",
            correct_moras=None
        )
        patched = apply_kana_patches(self.accent_phrases, [patch])
        self.assertEqual(self._get_mora_texts(patched), original)

    def test_length_mismatch_applies_within_range(self):
        """correct_morasがなく、文字数とモーラ数が合わない場合でも、指定範囲内で適用されること"""
        original = self._get_mora_texts()
        # Range 0-2 (2 moras) vs "ナガイヨ" (4 chars)
        patch = KanaPatch(
            block_id=0,
            token_index=0,
            mora_range=(0, 2),
            correct_kana="ナガイヨ",
            correct_moras=None
        )
        patched = apply_kana_patches(self.accent_phrases, [patch])
        
        # Range 0-2 (indices 0, 1) should be replaced by first 2 chars of "ナガイヨ" -> "ナ", "ガ"
        # Others remain unchanged
        expected = ["ナ", "ガ", "ニ", "チ", "ワ"]
        self.assertEqual(self._get_mora_texts(patched), expected)

if __name__ == "__main__":
    unittest.main()
