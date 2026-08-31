import asyncio

from alembic import context

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import async_engine_from_config


from app.core.config import get_settings
from app.infrastructure.db.base import Base

import app.models


config = context.config


settings = get_settings()


config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_URL
)


target_metadata = Base.metadata



def do_run_migrations(connection):

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )


    with context.begin_transaction():

        context.run_migrations()



async def run_async_migrations():

    connectable = async_engine_from_config(
        config.get_section(
            config.config_ini_section
        ),

        prefix="sqlalchemy.",

        poolclass=NullPool,
    )


    async with connectable.connect() as connection:

        await connection.run_sync(
            do_run_migrations
        )


    await connectable.dispose()



def run_migrations_online():

    asyncio.run(
        run_async_migrations()
    )



run_migrations_online()