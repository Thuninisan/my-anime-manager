"""Read-only old processing plan coordinates; no work binding is inferred."""
from ..domain.episode import create_episode_mapping
from ..domain.persistence import episode_mapping_snapshot


def replacement_operation_view(operation, subject_id):
    if "episode_mapping_snapshot" in operation or operation.get("bangumi_sort") is None:
        return operation
    empty = {"series_id": None, "episode_id": None, "season_number": None, "episode_number": None}
    mapping = create_episode_mapping(
        {"season_number": None, "episode_number": None},
        {"subject_id": subject_id, "episode_id": None, "episode_number": None,
         "episode_absolute": operation["bangumi_sort"]}, dict(empty), dict(empty))
    return {**operation, "episode_mapping_snapshot": episode_mapping_snapshot(mapping, source="legacy_partial")}
