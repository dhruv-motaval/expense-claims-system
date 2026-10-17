from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b2f90471b17c'
down_revision = '45cc8add3d05'
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, plain JSON on SQLite.
JsonOrJsonb = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.add_column('receipts', sa.Column('original_filename', sa.String(length=255), nullable=True))
    op.add_column('receipts', sa.Column('content_type', sa.String(length=100), nullable=True))
    op.add_column('receipts', sa.Column('file_size', sa.Integer(), nullable=True))
    op.add_column('receipts', sa.Column('checksum_sha256', sa.String(length=64), nullable=True))
    op.add_column('receipts', sa.Column('extracted_data', JsonOrJsonb, nullable=True))
    op.add_column('receipts', sa.Column('processing_status', sa.String(length=20),
                                        nullable=False, server_default='completed'))


def downgrade() -> None:
    op.drop_column('receipts', 'processing_status')
    op.drop_column('receipts', 'extracted_data')
    op.drop_column('receipts', 'checksum_sha256')
    op.drop_column('receipts', 'file_size')
    op.drop_column('receipts', 'content_type')
    op.drop_column('receipts', 'original_filename')
