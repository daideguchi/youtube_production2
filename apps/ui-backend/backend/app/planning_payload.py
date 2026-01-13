from __future__ import annotations

from typing import Any, Dict, List

from backend.app.normalize import normalize_optional_text
from backend.app.planning_models import PlanningFieldPayload, PlanningInfoResponse
from backend.tools.optional_fields_registry import OPTIONAL_FIELDS, get_planning_section


def build_planning_payload(metadata: Dict[str, Any]) -> PlanningInfoResponse:
    """Convert metadata.planning and sheet_flag into API payload."""

    planning_section = get_planning_section(metadata)
    fields: List[PlanningFieldPayload] = []
    for column_name, key in OPTIONAL_FIELDS.items():
        fields.append(
            PlanningFieldPayload(
                key=key,
                column=column_name,
                label=column_name,
                value=normalize_optional_text(planning_section.get(key)),
            )
        )
    flag_value = normalize_optional_text(metadata.get("sheet_flag"))
    return PlanningInfoResponse(creation_flag=flag_value, fields=fields)


def build_planning_payload_from_row(row: Dict[str, str]) -> PlanningInfoResponse:
    fields: List[PlanningFieldPayload] = []
    for column_name, key in OPTIONAL_FIELDS.items():
        fields.append(
            PlanningFieldPayload(
                key=key,
                column=column_name,
                label=column_name,
                value=normalize_optional_text(row.get(column_name)),
            )
        )
    return PlanningInfoResponse(
        creation_flag=normalize_optional_text(row.get("作成フラグ")),
        fields=fields,
    )

