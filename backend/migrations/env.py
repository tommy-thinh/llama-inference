"""Apply explicit migrations using environment credentials or a test connection."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from backend.models import Base

config = context.config
target_metadata = Base.metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url="postgresql+psycopg://", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
    else:
        url = os.environ.get("LLAMA_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
        if not url:
            raise RuntimeError("Export LLAMA_DATABASE_URL before running Alembic")
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
        with engine.connect() as connection:
            migrate(connection)
        engine.dispose()
