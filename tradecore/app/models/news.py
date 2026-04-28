from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, created_at_col


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    importance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    coins: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        Index("idx_news_published", "published_at"),
        Index("idx_news_importance", "importance"),
    )
