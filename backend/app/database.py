"""Database engine, session factory, and the declarative base."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

# Пул соединений настраиваем явно. Дефолт SQLAlchemy — 5 постоянных + 10
# «сверх нормы», а uvicorn держит пул из 40 потоков: синхронные обработчики
# упирались в очередь за соединением задолго до того, как кончались потоки.
# pool_recycle нужен, потому что Postgres/pgbouncer молча рвут соединения,
# простоявшие без дела, и первый запрос после простоя падал.
# SQLite (тесты, локальный запуск) использует пул без «сверх нормы» соединений,
# и pool_size/max_overflow для него — недопустимые аргументы: create_engine на
# них падает с TypeError ещё на импорте. Поэтому настройки пула применяем только
# к настоящей СУБД, а не «на всякий случай ко всему».
_engine_kwargs: dict = {"pool_pre_ping": True}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
    )

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# init_db() удалён намеренно.
#
# Раньше при каждом старте вызывался Base.metadata.create_all(): он создавал
# недостающие таблицы напрямую, в обход Alembic. Из-за этого схема в базе могла
# отличаться от того, что описано в миграциях (create_all не умеет ALTER —
# новые колонки в существующих таблицах он молча пропускает), и `alembic
# upgrade head` потом падал на середине.
#
# Единственный источник правды по схеме — миграции Alembic. Их применяет
# docker-entrypoint.sh перед стартом uvicorn.
