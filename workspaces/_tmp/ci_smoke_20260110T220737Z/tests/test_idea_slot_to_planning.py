from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from factory_common import paths as repo_paths
from factory_common.idea_store import load_cards, new_card, save_cards


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "workspaces"
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(ws))
    repo_paths.workspace_root.cache_clear()
    repo_paths.repo_root.cache_clear()
    try:
        yield ws
    finally:
        repo_paths.workspace_root.cache_clear()
        repo_paths.repo_root.cache_clear()


def _write_planning_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def test_slot_creates_patch_and_adds_row(tmp_workspace: Path) -> None:
    channel = "CH01"

    # Minimal planning CSV with required columns for CH01 >= 191
    headers = [
        "No.",
        "チャンネル",
        "動画番号",
        "動画ID",
        "タイトル",
        "作成フラグ",
        "進捗",
        "品質チェック結果",
        "更新日時",
        "企画意図",
        "ターゲット層",
        "具体的な内容（話の構成案）",
        "内容",
        "悩みタグ_メイン",
        "悩みタグ_サブ",
        "ライフシーン",
        "キーコンセプト",
        "ベネフィット一言",
        "たとえ話イメージ",
        "説明文_リード",
        "説明文_この動画でわかること",
    ]
    planning_path = repo_paths.channels_csv_path(channel)
    _write_planning_csv(
        planning_path,
        headers,
        [
            {
                "No.": "190",
                "チャンネル": channel,
                "動画番号": "190",
                "動画ID": "CH01-190",
                "タイトル": "dummy",
                "進捗": "topic_research: pending",
                "品質チェック結果": "未完了",
                "更新日時": "2025-01-01T00:00:00Z",
            }
        ],
    )

    # READY idea card
    idea_path = repo_paths.ideas_store_path(channel)
    card = new_card(
        channel=channel,
        theme="距離感/言葉/執着",
        working_title="優しい言葉が人を傷つける理由",
        hook="あなたの何気ない一言が、刃のように残ることがあります。",
        promise="最後まで聞くと『言葉で傷つけない判断軸』が手に入ります。",
        angle="口業/業/相手の受け取り方",
        status="READY",
        tags=["寝落ち", "朗読"],
    )
    card["idea_id"] = "CH01-IDEA-20251231-0001"
    save_cards(idea_path, [card])

    repo_root = repo_paths.repo_root()
    cmd = [sys.executable, str(repo_root / "scripts" / "ops" / "idea.py"), "slot", "--channel", channel, "--n", "1", "--apply"]
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + "\n" + res.stderr

    # Planning row added
    with planning_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    new_row = rows[-1]
    assert new_row.get("動画番号") == "191"
    assert new_row.get("動画ID") == "CH01-191"
    assert new_row.get("タイトル") == "優しい言葉が人を傷つける理由"
    assert new_row.get("作成フラグ") == "3"
    # Required fields should be populated for >=191
    assert (new_row.get("悩みタグ_メイン") or "").strip()
    assert (new_row.get("キーコンセプト") or "").strip()
    assert (new_row.get("ベネフィット一言") or "").strip()
    assert (new_row.get("説明文_リード") or "").strip()

    # Idea marked as PRODUCING with planning_ref
    _, cards = load_cards(channel)
    assert cards[0]["status"] == "PRODUCING"
    pref = cards[0].get("planning_ref") or {}
    assert pref.get("script_id") == "CH01-191"
    assert pref.get("video") == "191"


def test_slot_reuses_existing_patch_file(tmp_workspace: Path) -> None:
    channel = "CH01"

    headers = [
        "No.",
        "チャンネル",
        "動画番号",
        "動画ID",
        "タイトル",
        "作成フラグ",
        "進捗",
        "品質チェック結果",
        "更新日時",
        "企画意図",
        "ターゲット層",
        "具体的な内容（話の構成案）",
        "内容",
        "悩みタグ_メイン",
        "悩みタグ_サブ",
        "ライフシーン",
        "キーコンセプト",
        "ベネフィット一言",
        "たとえ話イメージ",
        "説明文_リード",
        "説明文_この動画でわかること",
    ]
    planning_path = repo_paths.channels_csv_path(channel)
    _write_planning_csv(
        planning_path,
        headers,
        [
            {
                "No.": "190",
                "チャンネル": channel,
                "動画番号": "190",
                "動画ID": "CH01-190",
                "タイトル": "dummy",
                "作成フラグ": "3",
                "進捗": "topic_research: pending",
                "品質チェック結果": "未完了",
                "更新日時": "2025-01-01T00:00:00Z",
            }
        ],
    )

    idea_path = repo_paths.ideas_store_path(channel)
    card = new_card(
        channel=channel,
        theme="距離感/言葉/執着",
        working_title="優しい言葉が人を傷つける理由",
        hook="あなたの何気ない一言が、刃のように残ることがあります。",
        promise="最後まで聞くと『言葉で傷つけない判断軸』が手に入ります。",
        angle="口業/業/相手の受け取り方",
        status="READY",
        tags=["寝落ち", "朗読"],
    )
    card["idea_id"] = "CH01-IDEA-20251231-0002"
    save_cards(idea_path, [card])

    repo_root = repo_paths.repo_root()
    base_cmd = [sys.executable, str(repo_root / "scripts" / "ops" / "idea.py"), "slot", "--channel", channel, "--n", "1"]

    # First run: generate patch only (no apply)
    res1 = subprocess.run(base_cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert res1.returncode == 0, res1.stdout + "\n" + res1.stderr

    patch_dir = repo_paths.planning_patches_root()
    patches = list(patch_dir.glob("*.yaml"))
    assert len(patches) == 1

    # Second run: should reuse the existing patch file instead of refusing to overwrite
    res2 = subprocess.run(base_cmd + ["--apply"], cwd=str(repo_root), capture_output=True, text=True)
    assert res2.returncode == 0, res2.stdout + "\n" + res2.stderr

    patches2 = list(patch_dir.glob("*.yaml"))
    assert len(patches2) == 1

    with planning_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[-1].get("動画ID") == "CH01-191"

    _, cards = load_cards(channel)
    assert cards[0]["status"] == "PRODUCING"


def test_slot_reconciles_when_planning_row_already_exists(tmp_workspace: Path) -> None:
    channel = "CH01"

    headers = [
        "No.",
        "チャンネル",
        "動画番号",
        "動画ID",
        "タイトル",
        "作成フラグ",
        "進捗",
        "品質チェック結果",
        "更新日時",
        "企画意図",
        "ターゲット層",
        "具体的な内容（話の構成案）",
        "内容",
        "悩みタグ_メイン",
        "悩みタグ_サブ",
        "ライフシーン",
        "キーコンセプト",
        "ベネフィット一言",
        "たとえ話イメージ",
        "説明文_リード",
        "説明文_この動画でわかること",
    ]
    planning_path = repo_paths.channels_csv_path(channel)
    _write_planning_csv(
        planning_path,
        headers,
        [
            {
                "No.": "190",
                "チャンネル": channel,
                "動画番号": "190",
                "動画ID": "CH01-190",
                "タイトル": "dummy",
                "作成フラグ": "3",
                "進捗": "topic_research: pending",
                "品質チェック結果": "未完了",
                "更新日時": "2025-01-01T00:00:00Z",
            }
        ],
    )

    idea_path = repo_paths.ideas_store_path(channel)
    card = new_card(
        channel=channel,
        theme="距離感/言葉/執着",
        working_title="優しい言葉が人を傷つける理由",
        hook="あなたの何気ない一言が、刃のように残ることがあります。",
        promise="最後まで聞くと『言葉で傷つけない判断軸』が手に入ります。",
        angle="口業/業/相手の受け取り方",
        status="READY",
        tags=["寝落ち", "朗読"],
    )
    card["idea_id"] = "CH01-IDEA-20251231-0003"
    save_cards(idea_path, [card])

    repo_root = repo_paths.repo_root()
    idea_cmd = [sys.executable, str(repo_root / "scripts" / "ops" / "idea.py"), "slot", "--channel", channel, "--n", "1"]

    # Create a patch (but don't apply via idea.py yet)
    res1 = subprocess.run(idea_cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert res1.returncode == 0, res1.stdout + "\n" + res1.stderr
    patch_dir = repo_paths.planning_patches_root()
    patches = list(patch_dir.glob("*.yaml"))
    assert len(patches) == 1

    # Apply the patch directly (simulates a crash before idea store update)
    patch_path = patches[0]
    apply_cmd = [sys.executable, str(repo_root / "scripts" / "ops" / "planning_apply_patch.py"), "--patch", str(patch_path), "--apply"]
    res2 = subprocess.run(apply_cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert res2.returncode == 0, res2.stdout + "\n" + res2.stderr

    with planning_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2

    # Now running slot --apply again should reconcile the card without adding a duplicate row.
    res3 = subprocess.run(idea_cmd + ["--apply"], cwd=str(repo_root), capture_output=True, text=True)
    assert res3.returncode == 0, res3.stdout + "\n" + res3.stderr

    with planning_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows2 = list(csv.DictReader(f))
    assert len(rows2) == 2

    _, cards = load_cards(channel)
    assert cards[0]["status"] == "PRODUCING"
    pref = cards[0].get("planning_ref") or {}
    assert pref.get("script_id") == "CH01-191"
    assert pref.get("video") == "191"
