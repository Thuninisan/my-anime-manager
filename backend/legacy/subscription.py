"""Interpret only provider facts stored on an old subscription row."""
from ..domain.resource_adapters import provider_binding_identity


def subscription_identity_view(row):
    return provider_binding_identity(title=row.series_name, bangumi_id=row.bangumi_id,
                                     tmdb_id=row.tmdb_id, tvdb_id=row.tvdb_id)
