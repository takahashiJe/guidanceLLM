"""add route fields to plans and reroute fields to sessions

Revision ID: 0013_navigation_route_fields
Revises: 0012_conversation_history
Create Date: 2025-08-20 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision = '0013_navigation_route_fields'
down_revision = '0012_conversation_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sessions: last_reroute_at, reroute_cooldown_sec
    op.add_column('sessions', sa.Column('last_reroute_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sessions', sa.Column('reroute_cooldown_sec', sa.Integer(), server_default=sa.text('20'), nullable=False))

    # plans: route_geojson(JSONB), route_version(INT default 1), route_updated_at
    op.add_column('plans', sa.Column('route_geojson', pg.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('plans', sa.Column('route_version', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.add_column('plans', sa.Column('route_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('plans', 'route_updated_at')
    op.drop_column('plans', 'route_version')
    op.drop_column('plans', 'route_geojson')
    op.drop_column('sessions', 'reroute_cooldown_sec')
    op.drop_column('sessions', 'last_reroute_at')
