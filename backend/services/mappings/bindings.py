"""Persist explicitly selected provider bindings and mapping corrections."""

from ... import data


def select_bangumi(bangumi_id: int, name: str, name_original: str = "") -> dict:
    if bangumi_id <= 0 or not name.strip():
        raise ValueError("无效的 Bangumi 条目")
    data.add_mapping(bangumi_id, name.strip(), name_original.strip())
    return {"name": data.get_bangumi_name(bangumi_id),
            "has_mikan_id": data.get_mikan_id(bangumi_id) is not None}
