"""create readings if missing

Brings `readings` under migration control. It was never created by a migration:
the baseline (ca5f88078400) is empty because it was stamped over a hand-made
schema, and every database since has had the table created by
`Database.create_all()` at sensor-reader startup instead.

That worked, but it meant any later change to the `Reading` model would never
reach an existing database - `create_all` creates missing tables, it does not
alter existing ones - and it left `readings` invisible to autogenerate drift
checks. With this in place, migrations own the whole schema and `create_all`
comes out of the service.

The create is guarded, so this is a no-op on every database that already has
the table (which is all of them today) and a real create on a fresh one.

Revision ID: a1292fda24f3
Revises: b3f1a27c94d5
Create Date: 2026-08-31 16:10:28.812856

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1292fda24f3'
down_revision: Union[str, Sequence[str], None] = 'b3f1a27c94d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "readings"
INDEX = "ix_readings_timestamp"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """Create `readings`, unless it is already there."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        # SQLite only autoincrements an INTEGER primary key, so it gets that
        # variant; Postgres still gets BIGSERIAL. Same declaration as the model.
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("pm25", sa.Float(), nullable=False),
        sa.Column("pm10", sa.Float(), nullable=False),
        sa.Column("raw_pm25", sa.Float(), nullable=True),
        sa.Column("raw_pm10", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        # func.now() so each dialect renders its own: CURRENT_TIMESTAMP on
        # SQLite, now() on Postgres.
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f(INDEX), TABLE, ["timestamp"], unique=False)


def downgrade() -> None:
    """Drop `readings`.

    Destructive: this is every stored reading. Nothing in the deployment ever
    downgrades - both Dockerfiles run `alembic upgrade head` and nothing else -
    so this exists for symmetry and for the tests, not as an operation to run
    against the Pi.
    """
    if not _has_table(TABLE):
        return
    op.drop_index(op.f(INDEX), table_name=TABLE)
    op.drop_table(TABLE)
