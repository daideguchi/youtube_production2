from __future__ import annotations

import pytest

from factory_common.youtube_handle import find_channel_ids_in_youtube_html, normalize_youtube_handle


def test_normalize_youtube_handle_accepts_handle_and_url():
    assert normalize_youtube_handle("@buddha-a001") == "@buddha-a001"
    assert normalize_youtube_handle("buddha-a001") == "@buddha-a001"
    assert normalize_youtube_handle("https://www.youtube.com/@buddha-a001") == "@buddha-a001"
    assert normalize_youtube_handle("https://www.youtube.com/@buddha-a001/about") == "@buddha-a001"


def test_normalize_youtube_handle_rejects_channel_url():
    with pytest.raises(ValueError):
        normalize_youtube_handle("https://www.youtube.com/channel/UCY2W5huV0xtLYNt9xpq70HA")


def test_find_channel_ids_in_youtube_html_filters_uc_ids():
    html = (
        '... "browseId":"UCY2W5huV0xtLYNt9xpq70HA" ... '
        '... "browseId":"FEwhat_to_watch" ... '
        '... "externalId":"UCY2W5huV0xtLYNt9xpq70HA" ... '
        '<meta property="og:url" content="https://www.youtube.com/channel/UCY2W5huV0xtLYNt9xpq70HA" />'
    )
    assert find_channel_ids_in_youtube_html(html) == {"UCY2W5huV0xtLYNt9xpq70HA"}


def test_find_channel_ids_in_youtube_html_can_return_multiple_uc_ids():
    html = (
        '... "browseId":"UCY2W5huV0xtLYNt9xpq70HA" ... '
        '... "browseId":"UCLl4VOZ21zq9Fexo0822w7A" ... '
    )
    assert find_channel_ids_in_youtube_html(html) == {
        "UCY2W5huV0xtLYNt9xpq70HA",
        "UCLl4VOZ21zq9Fexo0822w7A",
    }

