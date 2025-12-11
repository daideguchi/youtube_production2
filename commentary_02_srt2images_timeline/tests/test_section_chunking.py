from commentary_02_srt2images_timeline.src.srt2images.llm_context_analyzer import (
    LLMContextAnalyzer,
    SectionBreak,
)


def _fake_segments(n: int):
    segs = []
    for i in range(n):
        segs.append({"index": i, "text": f"text-{i}", "start": float(i), "end": float(i) + 1.0})
    return segs


def test_generate_initial_sections_splits_by_max_segments(monkeypatch):
    analyzer = LLMContextAnalyzer()
    analyzer.MAX_SEGMENTS_PER_CALL = 600  # explicit for the test
    calls = []

    def fake_call_llm_for_analysis(segments, target_sections, min_sections, max_sections, start_offset):
        calls.append(
            {
                "len": len(segments),
                "target": target_sections,
                "min": min_sections,
                "max": max_sections,
                "offset": start_offset,
            }
        )
        # return one dummy SectionBreak per chunk
        return [
            SectionBreak(
                start_segment=start_offset,
                end_segment=min(len(segments) - 1 + start_offset, start_offset + 10),
                reason="dummy",
                emotional_tone="neutral",
                summary="summary",
                visual_focus="focus",
            )
        ]

    monkeypatch.setattr(analyzer, "_call_llm_for_analysis", fake_call_llm_for_analysis)

    segments = _fake_segments(1200)
    analyzer._generate_initial_sections(segments, target_sections=20)

    # Expect chunking into 3 calls: 600, ~600 with overlap adjustments
    assert len(calls) >= 2
    assert all(c["len"] <= 600 for c in calls)
