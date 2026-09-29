"""ORM models for the application database."""

from sqlalchemy import Float, Index, Integer, JSON, Text, UniqueConstraint, text
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
    torrent_files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    collected_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ResourceSource(Base):
    __tablename__ = "resource_sources"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    rss_url: Mapped[str] = mapped_column(Text, nullable=False)
    downloadtag: Mapped[dict] = mapped_column(JSON, nullable=False)
    index_type: Mapped[str] = mapped_column(Text, nullable=False)


class ResourceRecognition(Base):
    __tablename__ = "resource_recognitions"
    resource_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
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
    bangumi_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    processing: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class LegacyImport(Base):
    __tablename__ = "legacy_imports"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    imported_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Subscription(Base):
    __tablename__ = "subscriptions"

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class BangumiMapping(Base):
    __tablename__ = "bangumi_mappings"
    __table_args__ = (Index("idx_bangumi_mappings_tmdb", "tmdb_id"),
                      Index("idx_bangumi_mappings_tvdb", "tvdb_id"))

    bangumi_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class JsonDocument(Base):
    """Mutable application documents whose legacy format is a JSON object."""
    __tablename__ = "json_documents"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
