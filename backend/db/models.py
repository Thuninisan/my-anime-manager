"""ORM models for the application database."""

from sqlalchemy import Float, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
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
    collected_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ResourceSource(Base):
    __tablename__ = "resource_sources"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False)
    download_tag: Mapped[str] = mapped_column(Text, nullable=False, default="")
    download_attribute: Mapped[str | None] = mapped_column(Text)
    index_type: Mapped[str] = mapped_column(Text, nullable=False)


class ResourceTorrentFile(Base):
    __tablename__ = "resource_torrent_files"

    resource_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class ResourceRecognition(Base):
    __tablename__ = "resource_recognitions"
    resource_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")


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
    processing_mode: Mapped[str | None] = mapped_column(Text)
    replace_bangumi_id: Mapped[int | None] = mapped_column(Integer)
    processing_present: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TorrentCardBangumi(Base):
    __tablename__ = "torrent_card_bangumi"
    card_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    bangumi_id: Mapped[int] = mapped_column(Integer, nullable=False)


class TorrentCardOperation(Base):
    __tablename__ = "torrent_card_operations"
    card_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    torrent_path: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    target_path: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    bangumi_sort: Mapped[int | None] = mapped_column(Integer)


class StructuredNode(Base):
    """Typed tree nodes for extension data without opaque JSON columns."""
    __tablename__ = "structured_nodes"
    __table_args__ = (Index("idx_structured_nodes_owner", "owner_kind", "owner_id", "root_field"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    root_field: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer)
    key_name: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(Integer)
    value_type: Mapped[str] = mapped_column(Text, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text)


class LegacyImport(Base):
    __tablename__ = "legacy_imports"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    imported_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Subscription(Base):
    __tablename__ = "rss_subscriptions"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
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

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subgroup_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subgroup_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    offset: Mapped[int | None] = mapped_column(Integer)


class SubscriptionFeedRule(Base):
    __tablename__ = "rss_subscription_feed_rules"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_type: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class BangumiMapping(Base):
    __tablename__ = "bangumi_mappings_v2"
    __table_args__ = (Index("idx_bangumi_mappings_tmdb", "tmdb_id"),
                      Index("idx_bangumi_mappings_tvdb", "tvdb_id"))

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    name_original: Mapped[str | None] = mapped_column(Text)
    mikan_id: Mapped[int | None] = mapped_column(Integer)
    anidb_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    tmdb_season: Mapped[int | None] = mapped_column(Integer)
    tvdb_season: Mapped[int | None] = mapped_column(Integer)


class BangumiMappingOverride(Base):
    __tablename__ = "bangumi_mapping_overrides"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field: Mapped[str] = mapped_column(Text, primary_key=True)
    text_value: Mapped[str | None] = mapped_column(Text)
    int_value: Mapped[int | None] = mapped_column(Integer)


class DownloadEpisode(Base):
    __tablename__ = "download_episodes"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    guid: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pub_date: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tmdb_ep: Mapped[int | None] = mapped_column(Integer)
    tmdb_season: Mapped[int | None] = mapped_column(Integer)
    tvdb_ep: Mapped[int | None] = mapped_column(Integer)
    tmdb_ep_calc: Mapped[int | None] = mapped_column(Integer)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="downloaded")


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
