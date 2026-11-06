"""Drop GemRadar — module removed.

Drops the `gemradar_alerts` table and the GemRadar columns on
`user_settings` (four thresholds plus the Oracle weight).

**downgrade() recreates the schema but CANNOT restore the rows.** Alerts are
gone for good, and every user's thresholds and weight come back at their
defaults. Treat this as one-way.

Revision ID: 028_drop_gemradar
Revises: 027_oracle_alerted
Create Date: 2026-10-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "028_drop_gemradar"
down_revision = "027_oracle_alerted"
branch_labels = None
depends_on = None

SETTINGS_COLUMNS = (
    "gemradar_min_mcap_usd",
    "gemradar_max_mcap_usd",
    "gemradar_min_price_change",
    "gemradar_risk_tolerance",
    "weight_gemradar",
)


def upgrade() -> None:
    # IF EXISTS so a database that never had these migrates cleanly.
    op.execute("DROP TABLE IF EXISTS gemradar_alerts")
    for col in SETTINGS_COLUMNS:
        op.execute(f"ALTER TABLE user_settings DROP COLUMN IF EXISTS {col}")


def downgrade() -> None:
    """Schema only — deliberately minimal, the rows are not coming back."""
    op.add_column("user_settings", sa.Column("gemradar_min_mcap_usd", sa.Numeric(20, 2), nullable=False, server_default="1000000"))
    op.add_column("user_settings", sa.Column("gemradar_max_mcap_usd", sa.Numeric(20, 2), nullable=False, server_default="100000000"))
    op.add_column("user_settings", sa.Column("gemradar_min_price_change", sa.Numeric(5, 2), nullable=False, server_default="10.0"))
    op.add_column("user_settings", sa.Column("gemradar_risk_tolerance", sa.String(20), nullable=False, server_default="medium"))
    op.add_column("user_settings", sa.Column("weight_gemradar", sa.Integer, nullable=False, server_default="10"))
    op.create_table(
        "gemradar_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
