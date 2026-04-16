"""Seed the three default plans: free, pro, elite.

Revision ID: 002_seed_plans
Revises: 001_initial
Create Date: 2026-04-15
"""
import json
from typing import Sequence, Union

from alembic import op

revision: str = "002_seed_plans"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FREE_FEATURES = {
    "radarx": True,
    "whaleradar": False,
    "liquidmap": False,
    "sentimentpulse": True,
    "macropulse": False,
    "gemradar": False,
    "riskcalc": True,
    "tradelog": True,
    "performancecore": False,
    "oracle": False,
    "oracle_auto_execute": False,
    "telegram_alerts": False,
    "binance_sync": False,
}

PRO_FEATURES = {
    "radarx": True,
    "whaleradar": True,
    "liquidmap": True,
    "sentimentpulse": True,
    "macropulse": True,
    "gemradar": True,
    "riskcalc": True,
    "tradelog": True,
    "performancecore": True,
    "oracle": True,
    "oracle_auto_execute": False,
    "telegram_alerts": True,
    "binance_sync": True,
}

ELITE_FEATURES = {**PRO_FEATURES, "oracle_auto_execute": True}


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        """
        INSERT INTO plans (name, display_name, price_monthly_usd, price_yearly_usd,
                           features, max_watchlist_size, alert_delay_seconds, is_active)
        VALUES
          ('free', 'Free', 0, 0, %(free)s::jsonb, 10, 300, TRUE),
          ('pro', 'Pro', 29, 290, %(pro)s::jsonb, 50, 0, TRUE),
          ('elite', 'Elite', 79, 790, %(elite)s::jsonb, 200, 0, TRUE)
        ON CONFLICT DO NOTHING;
        """,
        {
            "free": json.dumps(FREE_FEATURES),
            "pro": json.dumps(PRO_FEATURES),
            "elite": json.dumps(ELITE_FEATURES),
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE name IN ('free', 'pro', 'elite');")
