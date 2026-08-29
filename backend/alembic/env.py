"""Среда выполнения Alembic.

URL и метаданные берём из самого приложения: app.config.settings.database_url
и app.database.Base. Так «источник правды» один — модели SQLAlchemy, а не
отдельная копия схемы. Импорт app.models регистрирует все таблицы на метаданных
(тот же приём, что и init_db), поэтому autogenerate видит полную схему.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app import models  # noqa: F401  (импорт ради регистрации моделей на метаданных)

# Приложение лежит рядом (prepend_sys_path = . в alembic.ini запускается из backend/).
from app.config import settings as app_settings
from app.database import Base

config = context.config

# URL из настроек приложения — не дублируем его в alembic.ini.
config.set_main_option("sqlalchemy.url", app_settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Миграции без подключения — генерируем SQL по URL."""
    context.configure(
        url=app_settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Миграции с живым подключением к базе."""
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
