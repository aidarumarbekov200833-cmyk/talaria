FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения + миграции. alembic.ini и папка alembic нужны, чтобы
# docker-entrypoint.sh мог выполнить `alembic upgrade head` при старте.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Запуск не от root. Если в приложении найдётся RCE, разница между root и
# обычным пользователем — это разница между «сломали контейнер» и «сломали
# контейнер и пошли дальше». Порт 8000 непривилегированный, так что root здесь
# был не нужен и раньше. Каталог /app отдаём ему целиком: alembic пишет
# временные файлы рядом с конфигом.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Точка входа сначала приводит схему к актуальной миграции, затем — uvicorn.
CMD ["./docker-entrypoint.sh"]
