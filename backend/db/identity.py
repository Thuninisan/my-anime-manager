"""The subscription owns its confirmed work identity; all edits use this service."""
import json
from datetime import datetime, timezone

from ..domain.resource import validate_resource_identity
from ..legacy.subscription import subscription_identity_view
from ..domain.persistence import RESOURCE_IDENTITY_SCHEMA_VERSION

BINDING_FIELDS = ("media_type", "bangumi_subject_id", "tmdb_series_id", "tmdb_movie_id", "tvdb_series_id")


def read_resource_identity(row):
    if row.resource_identity_json is None:
        return subscription_identity_view(row)
    if row.identity_schema_version != RESOURCE_IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported_resource_identity_version")
    identity = json.loads(row.resource_identity_json)
    validate_resource_identity(identity)
    if identity["bangumi_subject_id"] != row.bangumi_id:
        raise ValueError("subscription_subject_identity_conflict")
    return identity


def update_resource_identity(row, identity, *, source):
    """Atomic within the caller transaction. Historical consumers are untouched.

    There is no durable catalog cache keyed by subscription. RSS catalogs and
    metadata contexts are run-local; the next run obtains fresh provider IDs.
    Preview sessions keep their own immutable binding/catalog and are not evicted.
    """
    validate_resource_identity(identity)
    if identity["bangumi_subject_id"] != row.bangumi_id:
        raise ValueError("subscription_subject_identity_conflict")
    if not isinstance(source, str) or not source:
        raise ValueError("identity_source_required")
    old = read_resource_identity(row) if row.resource_identity_json is not None else None
    changed = old is None or any(old[field] != identity[field] for field in BINDING_FIELDS)
    if changed:
        row.identity_revision = (row.identity_revision or 0) + 1
        row.identity_source = source
        row.identity_updated_at = datetime.now(timezone.utc).isoformat()
    row.resource_identity_json = json.dumps(identity, ensure_ascii=False, allow_nan=False)
    row.identity_schema_version = RESOURCE_IDENTITY_SCHEMA_VERSION
    return changed
