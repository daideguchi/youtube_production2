import sys

from factory_common.paths import video_pkg_root


def test_import_llm_context_analyzer() -> None:
    sys.path.insert(0, str(video_pkg_root() / "src"))
    from srt2images.llm_context_analyzer import LLMContextAnalyzer  # noqa: F401
