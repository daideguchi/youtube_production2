#!/usr/bin/env python3
"""
Shared issue → fix_hints mapping for preproduction tools.

Why:
- preproduction_audit / production_pack should surface actionable "what to do next" without
  turning SSOT into a rigid spec.
- Keep fix hints deterministic and low-risk (no mutation, no LLM).

SSOT:
- ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueContext:
    channel: str | None = None
    video: str | None = None

    @property
    def channel_token(self) -> str:
        return (self.channel or "CHxx").strip() or "CHxx"

    @property
    def video_token(self) -> str:
        return (self.video or "NNN").strip() or "NNN"

    @property
    def video_token_3(self) -> str:
        raw = self.video_token
        try:
            return f"{int(raw):03d}"
        except Exception:
            if raw.isdigit():
                return raw.zfill(3)
        return raw

    @property
    def script_id(self) -> str:
        return f"{self.channel_token}-{self.video_token_3}"


def _format(hint: str, ctx: IssueContext) -> str:
    return hint.format(
        channel=ctx.channel_token,
        video=ctx.video_token,
        video3=ctx.video_token_3,
        script_id=ctx.script_id,
    )


_HINTS: dict[str, list[str]] = {
    # sources.yaml / entry resolution
    "missing_sources_entry": [
        "Edit `configs/sources.yaml` and add `channels.{channel}: ...` (planning_csv/persona/channel_prompt/etc).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_sources_channel_entry": [
        "Edit `configs/sources.yaml` and add `channels.{channel}: ...` (planning_csv/persona/channel_prompt/etc).",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    "missing_sources_planning_csv": [
        "Set `configs/sources.yaml: channels.{channel}.planning_csv` (usually `workspaces/planning/channels/{channel}.csv`).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_sources_persona": [
        "Optional: set `configs/sources.yaml: channels.{channel}.persona` (default path is `workspaces/planning/personas/{channel}_PERSONA.md`).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_sources_channel_prompt": [
        "Set `configs/sources.yaml: channels.{channel}.channel_prompt` to point to `packages/script_pipeline/channels/{channel}-*/script_prompt.txt`.",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_sources_yaml": [
        "Restore the tracked file `configs/sources.yaml` (primary SoT for entry resolution).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --all --write-latest`",
    ],
    "sources_yaml_parse_error": [
        "Fix YAML syntax in `configs/sources.yaml` (and overlay `packages/script_pipeline/config/sources.yaml` if used).",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    # planning
    "missing_planning_csv": [
        "Create/restore `workspaces/planning/channels/{channel}.csv` (or update `configs/sources.yaml` if the path is different).",
        "Run: `python3 scripts/ops/planning_lint.py --channel {channel} --write-latest`",
    ],
    "missing_planning_row": [
        "Add the planning row for `{script_id}` in `workspaces/planning/channels/{channel}.csv` (UI `/planning` is recommended).",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    "missing_title": [
        "Fill `タイトル` for `{script_id}` in `workspaces/planning/channels/{channel}.csv` (or apply a planning patch `set`).",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    "planning_lint_failed": [
        "Run: `python3 scripts/ops/planning_lint.py --channel {channel} --write-latest` and fix reported errors in the CSV.",
        "Optional: `python3 scripts/ops/planning_sanitize.py --channel {channel} --apply --write-latest` (clears known L3 contamination only).",
    ],
    "planning_lint_global_errors": [
        "Run: `python3 scripts/ops/planning_lint.py --channel {channel} --write-latest` and fix reported errors in the CSV.",
    ],
    "planning_lint_warnings": [
        "Run: `python3 scripts/ops/planning_lint.py --channel {channel} --write-latest` and decide whether to fix warnings before production.",
        "Optional: auto-fix common theme-hint drift via title anchoring: `python3 scripts/ops/planning_realign_to_title.py --channel {channel} --from NNN --to MMM --apply --write-latest`",
    ],
    "planning_lint_exception": [
        "Re-run with stdout to see stack: `python3 scripts/ops/planning_lint.py --channel {channel} --stdout`",
    ],
    "missing_required_fields_by_policy": [
        "Fill the required columns (policy) in `workspaces/planning/channels/{channel}.csv` for `{script_id}`.",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    "planning_requirements_exception": [
        "Check `packages/script_pipeline/tools/planning_requirements.py` (policy rules) and planning CSV headers/values.",
        "Re-run: `python3 scripts/ops/planning_lint.py --channel {channel} --write-latest`",
    ],
    "planning_row_published_lock": [
        "Verify this episode is not already published (Planning `進捗` contains `投稿済み/published`).",
        "If mis-locked, follow: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md` (published lock unlock).",
    ],
    # persona / extension inputs
    "missing_persona": [
        "Optional: create `workspaces/planning/personas/{channel}_PERSONA.md` to stabilize tone/quality.",
        "Re-run: `python3 scripts/ops/production_pack.py --channel {channel} --video {video3} --write-latest`",
    ],
    "persona_required_but_missing": [
        "Create `workspaces/planning/personas/{channel}_PERSONA.md` OR set `persona_required=false` in `packages/video_pipeline/config/channel_presets.json`.",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    # channel assets
    "missing_script_channel_dir": [
        "Ensure `packages/script_pipeline/channels/{channel}-*/` exists (channel scaffold).",
        "Hint: `PYTHONPATH=\\\".:packages\\\" python3 -m script_pipeline.tools.channel_registry create ...`",
    ],
    "missing_channel_info_json": [
        "Create/fix `packages/script_pipeline/channels/{channel}-*/channel_info.json` (JSON object).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "invalid_channel_info_json": [
        "Fix JSON syntax in `packages/script_pipeline/channels/{channel}-*/channel_info.json`.",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "invalid_channel_info_schema": [
        "`channel_info.json` must be a JSON object at top-level.",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_script_prompt": [
        "Create/fix `packages/script_pipeline/channels/{channel}-*/script_prompt.txt` and ensure `configs/sources.yaml` points to it.",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_channel_prompt": [
        "Fix `configs/sources.yaml: channels.{channel}.channel_prompt` to point to an existing file (default: `packages/script_pipeline/channels/{channel}-*/script_prompt.txt`).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "missing_script_prompt_txt": [
        "Create/fix `packages/script_pipeline/channels/{channel}-*/script_prompt.txt` and ensure `configs/sources.yaml` points to it.",
    ],
    "missing_video_workflow": [
        "Set `video_workflow` in `packages/script_pipeline/channels/{channel}-*/channel_info.json` (e.g., `capcut`, `remotion`).",
    ],
    # audio config
    "missing_voice_config": [
        "Create/fix `packages/script_pipeline/audio/channels/{channel}/voice_config.json` (JSON object).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "invalid_voice_config_json": [
        "Fix JSON syntax in `packages/script_pipeline/audio/channels/{channel}/voice_config.json`.",
    ],
    "invalid_voice_config_schema": [
        "`voice_config.json` must be a JSON object at top-level.",
    ],
    # video preset / templates (capcut)
    "missing_video_channel_preset": [
        "Add `channels.{channel}` to `packages/video_pipeline/config/channel_presets.json` (capcut_template required for active preset).",
        "Re-run: `python3 scripts/ops/preproduction_audit.py --channel {channel} --write-latest`",
    ],
    "active_preset_missing_capcut_template": [
        "Set `capcut_template` in `packages/video_pipeline/config/channel_presets.json: channels.{channel}` (required for CapCut draft).",
    ],
    "active_preset_missing_prompt_template": [
        "Optional: set `prompt_template` in `packages/video_pipeline/config/channel_presets.json: channels.{channel}` to stabilize image style.",
    ],
    "missing_prompt_template": [
        "Optional: set `prompt_template` in `packages/video_pipeline/config/channel_presets.json: channels.{channel}` to stabilize image style.",
    ],
    "missing_prompt_template_file": [
        "Create the missing template under `packages/video_pipeline/templates/` OR change preset `prompt_template` to an existing file.",
    ],
    "prompt_template_not_registered": [
        "Add the template id to `packages/video_pipeline/config/template_registry.json` (recommended governance).",
    ],
    # benchmarks (extension)
    "missing_benchmarks": [
        "Optional: add `benchmarks` in `packages/script_pipeline/channels/{channel}-*/channel_info.json` (see `ssot/ops/OPS_CHANNEL_BENCHMARKS.md`).",
    ],
    "benchmarks_empty_channels": [
        "Optional: fill `benchmarks.channels` or set `allow_empty_channels=true` with a reason.",
    ],
    "benchmarks_empty_script_samples": [
        "Optional: add at least 1 `benchmarks.script_samples` entry pointing to `workspaces/research/**` or `workspaces/scripts/**`.",
    ],
}


def fix_hints_for_issue(code: str, *, channel: str | None = None, video: str | None = None) -> list[str]:
    """
    Return formatted fix hints for an issue code.

    Unknown codes return an empty list (caller can omit the field).
    """

    ctx = IssueContext(channel=channel, video=video)
    key = str(code or "").strip()
    if not key:
        return []

    hints = _HINTS.get(key)
    if hints is None and key.startswith("planning_lint."):
        inner = key.split(".", 1)[1].strip()
        hints = _HINTS.get(inner)
        if hints is None:
            hints = _HINTS.get("planning_lint_failed", [])

    if not hints:
        return []

    return [_format(h, ctx) for h in hints if str(h).strip()]
