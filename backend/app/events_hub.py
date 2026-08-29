"""In-memory шина событий для live-обновлений через WebSocket.

Зачем своя, а не Redis pub/sub: uvicorn запускается одним процессом (см.
Dockerfile — без `--workers`), поэтому подписчики и издатели живут в одной
памяти. Если когда-нибудь появится несколько воркеров, эту шину надо будет
заменить на внешний брокер (Redis/NATS), а интерфейс (`publish`/`subscribe`)
оставить прежним.

Тонкость с потоками: обработчики маршрутов у нас синхронные (`def ...`), FastAPI
выполняет их в пуле потоков, а WebSocket-обработчик — корутина в главном event
loop. Класть в `asyncio.Queue` можно только из его цикла, поэтому `publish()`
делает это через `loop.call_soon_threadsafe(...)`. Ссылку на цикл шине сообщает
lifespan приложения (`bind_loop`).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger("outly.events")

# Сколько событий держим в очереди одного подписчика, прежде чем начать
# отбрасывать самые старые. Лента — вспомогательная вещь; лучше потерять пару
# старых строк, чем расти в памяти, если вкладку заморозили.
_MAX_QUEUE = 100


class EventHub:
    """Раздаёт события подписчикам, сгруппированным по владельцу (owner_id)."""

    def __init__(self) -> None:
        # owner_id -> набор очередей его открытых вкладок/сокетов.
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Запомнить главный event loop — из него будем будить очереди."""
        self._loop = loop

    def subscribe(self, owner_id: int) -> asyncio.Queue:
        """Зарегистрировать нового подписчика. Вызывается из WS-обработчика."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subs[owner_id].add(queue)
        return queue

    def unsubscribe(self, owner_id: int, queue: asyncio.Queue) -> None:
        """Убрать подписчика при закрытии сокета."""
        subs = self._subs.get(owner_id)
        if not subs:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(owner_id, None)

    def _deliver(self, owner_id: int, event: dict) -> None:
        """Положить событие во все очереди владельца (в главном цикле)."""
        for queue in list(self._subs.get(owner_id, ())):
            if queue.full():
                # Очередь переполнена (медленный/замороженный клиент): роняем
                # самое старое событие, чтобы освободить место под новое.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish(self, owner_id: int, event: dict) -> None:
        """Разослать событие. Безопасно вызывать из синхронного кода/другого потока.

        Если никто не слушает или цикл ещё не привязан — тихо выходим: события
        всё равно лежат в БД, живая доставка — необязательная надстройка.
        """
        if self._loop is None or owner_id not in self._subs:
            return
        try:
            self._loop.call_soon_threadsafe(self._deliver, owner_id, event)
        except RuntimeError:
            # Цикл закрывается (выключение) — доставлять уже некуда.
            logger.debug("publish после остановки цикла — пропущено")

    def campaign_update(
        self,
        owner_id: int,
        campaign_id: int,
        progress: float | None,
        status: str | None,
    ) -> None:
        """Лёгкий живой апдейт прогресса рассылки (без записи в event_logs).

        Нужен, чтобы процент на странице «Рассылка» двигался по мере логов,
        не засоряя при этом ленту событий отдельными строками.
        """
        payload: dict = {"type": "campaign", "campaign_id": campaign_id}
        if progress is not None:
            payload["progress"] = round(float(progress), 1)
        if status is not None:
            payload["status"] = status
        self.publish(owner_id, payload)


# Единственный экземпляр на процесс.
hub = EventHub()
