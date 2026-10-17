from alembic import op
import sqlalchemy as sa


revision = '45cc8add3d05'
down_revision = '0df2dc126555'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('receipts', sa.Column('s3_pages_keys', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('receipts', 's3_pages_keys')
