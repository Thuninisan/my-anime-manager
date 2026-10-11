"""ORM models for the application database."""

from sqlalchemy import CheckConstraint, Float, Index, Integer, JSON, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
    recognition_title_snapshot: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("source", "source_id"),
        Index("idx_resources_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    index_type: Mapped[str] = mapped_column(Text, nullable=False, default="tmdb")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    torrent_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rss_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    size_label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    torrent_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    torrent_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recognition_status: Mapped[str | None] = mapped_column(Text)
    recognition_error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    collected_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ResourceSource(Base):
    __tablename__ = "resource_sources"
    download_tag_extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False)
    download_tag: Mapped[str] = mapped_column(Text, nullable=False, default="")
    download_attribute: Mapped[str | None] = mapped_column(Text)
    index_type: Mapped[str] = mapped_column(Text, nullable=False)


class ResourceBangumiCandidate(Base):
    __tablename__ = "resource_bangumi_candidates"
    __table_args__ = (UniqueConstraint("resource_id", "bangumi_id", "index_season", "media_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bangumi_id: Mapped[int] = mapped_column(Integer, nullable=False)
    index_id: Mapped[int] = mapped_column(Integer, nullable=False)
    index_season: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False, default="TV")
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TorrentCard(Base):
    __tablename__ = "torrent_cards"
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    __table_args__ = (Index("idx_torrent_cards_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    info_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    torrent_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    show_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bgm_rating: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    poster_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="downloading")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    encoding_group: Mapped[str] = mapped_column(Text, nullable=False, default="")
    video_codec: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bangumi: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    completion_snapshot_json: Mapped[str | None] = mapped_column(Text)
    completion_schema_version: Mapped[int | None] = mapped_column(Integer)


class Subscription(Base):
    __tablename__ = "rss_subscriptions"
    poster_url: Mapped[str | None] = mapped_column(Text)
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    bgm_extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    tmdb_extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    tvdb_extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_identity_json: Mapped[str | None] = mapped_column(Text)
    identity_source: Mapped[str | None] = mapped_column(Text)
    identity_updated_at: Mapped[str | None] = mapped_column(Text)
    identity_revision: Mapped[int | None] = mapped_column(Integer)
    identity_schema_version: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    series_name: Mapped[str | None] = mapped_column(Text)
    download_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str | None] = mapped_column(Text)
    bgm_season: Mapped[int | None] = mapped_column(Integer)
    bgm_sort_start: Mapped[int | None] = mapped_column(Integer)
    bgm_sort_end: Mapped[int | None] = mapped_column(Integer)
    bgm_subject_name: Mapped[str | None] = mapped_column(Text)
    bgm_series_name: Mapped[str | None] = mapped_column(Text)
    bgm_rating: Mapped[float | None] = mapped_column(Float)
    bgm_air_date: Mapped[str | None] = mapped_column(Text)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_season: Mapped[int | None] = mapped_column(Integer)
    tmdb_ep_offset: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_season: Mapped[int | None] = mapped_column(Integer)
    tvdb_ep_offset: Mapped[int | None] = mapped_column(Integer)


class SubscriptionFeed(Base):
    __tablename__ = "rss_subscription_feeds"
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subgroup_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subgroup_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    offset: Mapped[int | None] = mapped_column(Integer)

    filter_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    exclude_patterns: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

class BangumiMapping(Base):
    __tablename__ = "bangumi_mappings_v2"
    __table_args__ = (Index("idx_bangumi_mappings_tmdb", "tmdb_id"),
                      Index("idx_bangumi_mappings_tvdb", "tvdb_id"))

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_cn: Mapped[str | None] = mapped_column(Text)
    title_jp: Mapped[str | None] = mapped_column(Text)
    mikan_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_season: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    tvdb_season: Mapped[int | None] = mapped_column(Integer)
    override: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("0"))


class MappingSyncState(Base):
    __tablename__ = "mapping_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initialized: Mapped[bool] = mapped_column(default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[str | None] = mapped_column(Text)
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text)


class DownloadEpisode(Base):
    """RSS download state, media destination, and provider episode coordinates."""
    __tablename__ = "download_episodes"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pub_date: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    download_path: Mapped[str | None] = mapped_column(Text)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="downloaded")

    mapping_initialized: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("0"))
    bangumi_episode_id: Mapped[int | None] = mapped_column(Integer)
    bangumi_episode_number: Mapped[float | None] = mapped_column(Float)
    tmdb_series_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_episode_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_season_number: Mapped[int | None] = mapped_column(Integer)
    tmdb_episode_number: Mapped[float | None] = mapped_column(Float)
    tvdb_series_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_episode_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_season_number: Mapped[int | None] = mapped_column(Integer)
    tvdb_episode_number: Mapped[float | None] = mapped_column(Float)


class TorrentPreviewSession(Base):
    """Disposable canonical snapshot; incompatible schemas require a new preview."""
    __tablename__ = "torrent_preview_sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    torrent_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    torrent_name: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)


class WorkflowRecovery(Base):
    """Only unfinished workflows; terminal results belong in business data/logs."""
    __tablename__ = "workflow_recovery"
    __table_args__ = (
        UniqueConstraint("workflow_type", "business_key"),
        CheckConstraint("schema_version >= 1"),
        CheckConstraint("revision >= 1"),
        CheckConstraint("state IN ('pending', 'running', 'waiting', 'blocked')"),
        Index("ix_workflow_recovery_schedule", "state", "next_run_at"),
        Index("ix_workflow_recovery_lease", "state", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False)
    business_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_run_at: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
