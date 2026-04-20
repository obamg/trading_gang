"""Drop billing tables (plans, subscriptions, invoices).

Revision ID: 003_drop_billing
Revises: 002_seed_plans
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003_drop_billing"
down_revision: Union[str, None] = "002_seed_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("invoices")
    op.drop_table("subscriptions")
    op.drop_table("plans")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE plans (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(50) NOT NULL,
            display_name VARCHAR(100) NOT NULL,
            price_monthly_usd NUMERIC(10,2) DEFAULT 0,
            price_yearly_usd NUMERIC(10,2) DEFAULT 0,
            stripe_price_id_monthly VARCHAR(100),
            stripe_price_id_yearly VARCHAR(100),
            features JSONB NOT NULL DEFAULT '{}'::jsonb,
            max_watchlist_size INTEGER DEFAULT 10,
            alert_delay_seconds INTEGER DEFAULT 300,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES plans(id),
            status VARCHAR(50) NOT NULL,
            stripe_subscription_id VARCHAR(100) UNIQUE,
            stripe_customer_id VARCHAR(100),
            billing_cycle VARCHAR(20) DEFAULT 'monthly',
            current_period_start TIMESTAMPTZ,
            current_period_end TIMESTAMPTZ,
            trial_end TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE invoices (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id UUID REFERENCES subscriptions(id),
            stripe_invoice_id VARCHAR(100) UNIQUE,
            amount_usd NUMERIC(10,2) NOT NULL,
            status VARCHAR(50),
            invoice_pdf_url VARCHAR(500),
            period_start TIMESTAMPTZ,
            period_end TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
