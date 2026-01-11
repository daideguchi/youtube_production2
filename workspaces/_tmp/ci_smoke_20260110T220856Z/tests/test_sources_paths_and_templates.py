import csv
from pathlib import Path

import yaml

from script_pipeline.tools import planning_requirements


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def _read_csv_sample_row(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return next(reader, [])


def test_sources_paths_exist_and_planning_templates_match_headers():
    cfg = yaml.safe_load(Path("configs/sources.yaml").read_text(encoding="utf-8"))
    channels = cfg.get("channels") or {}
    assert isinstance(channels, dict) and channels, "configs/sources.yaml must define channels"

    missing: list[str] = []
    header_mismatches: list[str] = []

    for channel_code, channel_cfg in sorted(channels.items()):
        if not isinstance(channel_cfg, dict):
            continue

        # sources.yaml -> core paths should exist
        for key in ("planning_csv", "persona", "channel_prompt"):
            raw = channel_cfg.get(key)
            if not raw:
                missing.append(f"{channel_code}:{key} (empty)")
                continue
            path = Path(str(raw))
            if not path.exists():
                missing.append(f"{channel_code}:{key} ({path})")

        # Portal expects a planning template CSV for each channel.
        planning_csv = Path(str(channel_cfg.get("planning_csv") or ""))
        template_csv = planning_csv.parent.parent / "templates" / f"{channel_code}_planning_template.csv"
        if not template_csv.exists():
            missing.append(f"{channel_code}:planning_template ({template_csv})")
            continue

        template_header = _read_csv_header(template_csv)
        if not template_header:
            header_mismatches.append(f"{channel_code}: template header is empty")

        sample = _read_csv_sample_row(template_csv)
        if not sample:
            header_mismatches.append(f"{channel_code}: template missing sample row")
        elif len(sample) != len(template_header):
            header_mismatches.append(
                f"{channel_code}: template sample row length != header length ({len(sample)} != {len(template_header)})"
            )

        # Template must contain required columns (planning requirements policy).
        required_specs = planning_requirements.get_channel_requirement_specs(channel_code)
        required_columns: list[str] = []
        for spec in required_specs:
            cols = spec.get("required_columns") or []
            if isinstance(cols, list):
                required_columns.extend([str(c) for c in cols if c])
        for col in required_columns:
            if col not in template_header:
                header_mismatches.append(f"{channel_code}: template missing required column: {col}")

    assert not missing, "Missing SSOT paths:\n" + "\n".join(sorted(missing))
    assert not header_mismatches, "Planning template mismatches:\n" + "\n".join(sorted(header_mismatches))
