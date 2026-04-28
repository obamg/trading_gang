"""Add news_articles table for NewsPulse module.

Revision ID: 004
Revises: 003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_id", sa.String(64), unique=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("sentiment", sa.String(20), nullable=True),
        sa.Column("importance", sa.String(20), nullable=True),
        sa.Column("coins", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified", sa.Boolean(), default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_news_published", "news_articles", ["published_at"])
    op.create_index("idx_news_importance", "news_articles", ["importance"])


def downgrade() -> None:
    op.drop_index("idx_news_importance", table_name="news_articles")
    op.drop_index("idx_news_published", table_name="news_articles")
    op.drop_table("news_articles")
