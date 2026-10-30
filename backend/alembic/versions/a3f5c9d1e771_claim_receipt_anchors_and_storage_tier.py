"""claim/receipt duplicate anchors + receipt storage tiering

revision: a3f5c9d1e771
down_revision: b2f90471b17c
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3f5c9d1e771'
down_revision = 'b2f90471b17c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # duplicate-proof anchors (normalized on write; matching is strict equality)
    op.add_column('claims', sa.Column('receipt_no', sa.String(length=120), nullable=True))
    op.add_column('claims', sa.Column('trans_time', sa.String(length=8), nullable=True))
    op.create_index('ix_claims_receipt_no', 'claims', ['receipt_no'])
    op.add_column('receipts', sa.Column('receipt_no', sa.String(length=120), nullable=True))
    op.add_column('receipts', sa.Column('trans_time', sa.String(length=8), nullable=True))
    # storage tiering: HOT (active: local disk / MinIO), COLD (S3), plus the
    # paid-cache pointer that the MinIO lifecycle rule expires after payment
    op.add_column('receipts', sa.Column('storage_tier', sa.String(length=8),
                                        nullable=False, server_default='HOT'))
    op.add_column('receipts', sa.Column('paid_cache_key', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('receipts', 'paid_cache_key')
    op.drop_column('receipts', 'storage_tier')
    op.drop_column('receipts', 'trans_time')
    op.drop_column('receipts', 'receipt_no')
    op.drop_index('ix_claims_receipt_no', table_name='claims')
    op.drop_column('claims', 'trans_time')
    op.drop_column('claims', 'receipt_no')
