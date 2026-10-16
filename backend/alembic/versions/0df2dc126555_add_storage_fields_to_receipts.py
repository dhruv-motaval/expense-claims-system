from alembic import op
import sqlalchemy as sa


revision = '0df2dc126555'
down_revision = '48a8a47ed309'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('receipts', sa.Column('user_id', sa.Integer(), nullable=True))
    op.add_column('receipts', sa.Column('s3_image_key', sa.String(length=500), nullable=True))
    op.add_column('receipts', sa.Column('s3_source_text_key', sa.String(length=500), nullable=True))
    op.add_column('receipts', sa.Column('s3_extracted_key', sa.String(length=500), nullable=True))
    op.create_index(op.f('ix_receipts_user_id'), 'receipts', ['user_id'], unique=False)
    try:
        op.create_foreign_key('fk_receipts_user_id', 'receipts', 'users', ['user_id'], ['id'])
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_constraint('fk_receipts_user_id', 'receipts', type_='foreignkey')
    except Exception:
        pass
    try:
        op.drop_index(op.f('ix_receipts_user_id'), table_name='receipts')
    except Exception:
        pass
    op.drop_column('receipts', 's3_extracted_key')
    op.drop_column('receipts', 's3_source_text_key')
    op.drop_column('receipts', 's3_image_key')
    op.drop_column('receipts', 'user_id')
