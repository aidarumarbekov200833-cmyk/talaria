"""ИИ-чат с tool calling над настоящими данными пользователя.

Прокси к OpenAI-совместимому API (base_url, ключ и модель из настроек). Модель
видит список инструментов и может их вызывать — сервер выполняет вызов над
настоящей базой и возвращает результат в следующий запрос. Выдуманных ответов
инструментов нет: если данных нет, так и написано в result.

Модель сообщений не хранится — состояние держит фронтенд. Сервер получает всю
историю в каждом запросе и возвращает только последний ответ ИИ.
"""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai_tools import TOOL_TITLES, TOOLS, WRITE_TOOLS, run_tool
from ..database import get_db
from ..dependencies import get_current_user
from ..models import User
from ..plans import ai_allowed, effective_plan_slug, get_plan
from ..ratelimit import limiter
from ..schemas import AiChatIn, AiChatOut, AiToolCallOut
from ..services import build_system_prompt, log_event, resolve_ai_provider

logger = logging.getLogger("outly.ai")

router = APIRouter(prefix="/api/ai", tags=["ai"])


# Результат каких инструментов браузер получает целиком. Остальные читают данные,
# и модель их уже увидела на сервере: возвращать в DOM списки лидов незачем.
# save_offer сюда входит ради ui_action «открыть офферы» и номера записи.
_UI_RESULT_TOOLS = {"control_interface", "save_offer"}


@router.post("/chat", response_model=AiChatOut, dependencies=[Depends(limiter("ai"))])
def chat(
    payload: AiChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiChatOut:
    """Отправить сообщения модели, выполнить её tool calls, вернуть финальный ответ.

    Если модель вызывает инструменты — выполняем их на сервере и шлём результаты
    ей же обратно, пока она не ответит текстом. Лимит итераций — защита от
    зацикливания, если модель бесконечно вызывает один инструмент.
    """
    # ИИ доступен только на платных тарифах — проверяем до обращения к API.
    if not ai_allowed(db, user.id):
        plan_name = get_plan(effective_plan_slug(db, user.id))["name"]
        raise HTTPException(
            402,
            f"ИИ-помощник не входит в тариф «{plan_name}». Оплатите тариф выше "
            "в разделе «Тариф».",
        )

    # Провайдер один на весь сервис и настраивается админом: пользователю не
    # нужно (и нельзя) вводить чужой ключ, поэтому и сообщение об ошибке зовёт
    # не «в настройки», а к владельцу.
    base_url, api_key, model, custom_prompt, tools_enabled = resolve_ai_provider(db)
    if not base_url or not api_key or not model:
        raise HTTPException(
            503,
            "ИИ-помощник пока не подключён на стороне сервиса. " "Напишите администратору.",
        )

    # Промпт собираем на каждый запрос: в него входит текущий тариф и остаток
    # лимита этого пользователя, иначе помощник начнёт их домысливать.
    system = build_system_prompt(db, user.id, custom_prompt)

    messages = [{"role": m.role, "content": m.content} for m in payload.messages]

    max_turns = 5
    # Сколько раз за одну реплику помощник может что-то записать. Цикл вызовов
    # инструментов идёт без участия человека: без этого предела одна неудачно
    # понятая просьба успела бы создать до max_turns × (вызовов за шаг) записей.
    max_writes = 2
    writes_done = 0
    all_tool_calls: list[AiToolCallOut] = []

    for _turn in range(max_turns):
        try:
            reply = _call_api(
                base_url,
                api_key,
                model,
                system,
                messages,
                TOOLS if tools_enabled else [],
            )
        except Exception as exc:
            # Подробности — только в журнал и логи сервера. Раньше текст
            # исключения уходил клиенту как есть, а туда попадают URL провайдера,
            # его сообщения об ошибках и иногда фрагменты ключа — то есть любой
            # пользователь мог вытянуть часть конфига установки, просто сломав запрос.
            logger.warning("Запрос к ИИ-провайдеру не удался", exc_info=True)
            # В журнал — без текста исключения: туда попадают URL провайдера и
            # иногда фрагменты ключа, а журнал виден в интерфейсе. Детали — в logger выше.
            log_event(db, user.id, "ai", "ИИ-помощник не ответил: ошибка провайдера", "error")
            raise HTTPException(
                502,
                "ИИ-провайдер не ответил. Попробуйте ещё раз; если повторяется — "
                "сообщите администратору.",
            ) from exc

        msg = reply.get("choices", [{}])[0].get("message", {})
        role = msg.get("role", "assistant")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Текстовый ответ — финал.
            #
            # Раньше здесь писалось «Ответ получен за N итераций» — внутренняя
            # механика цикла tool-calls, которая ничего не говорит владельцу и при
            # этом забивала ленту событий на «Главной» после каждой реплики в чате.
            # Простой вопрос-ответ теперь вообще не событие: в журнал попадает только
            # то, где помощник реально лез в данные — и с названиями того, что сделал.
            if all_tool_calls:
                used = ", ".join(
                    dict.fromkeys(TOOL_TITLES.get(c.name, c.name) for c in all_tool_calls)
                )
                log_event(db, user.id, "ai", f"ИИ-помощник: {used}", "success")
            return AiChatOut(
                reply=content,
                model=reply.get("model", model),
                tool_calls=all_tool_calls,
            )

        # Модель вызвала инструменты — выполняем их.
        messages.append({"role": role, "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            if name in WRITE_TOOLS and writes_done >= max_writes:
                # Отказ уходит модели обычным результатом инструмента: она
                # объяснит это пользователю сама, вместо обрыва запроса.
                result = {
                    "saved": False,
                    "error": (
                        "За одну реплику помощник может сохранить не больше "
                        f"{max_writes} записей. Попросите следующее отдельным сообщением."
                    ),
                }
            else:
                result = run_tool(db, user.id, name, args)
                if name in WRITE_TOOLS and result.get("saved"):
                    writes_done += 1
            result_str = json.dumps(result, ensure_ascii=False)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": result_str,
                }
            )
            all_tool_calls.append(
                AiToolCallOut(
                    name=name,
                    arguments=args_str,
                    result_summary=result_str[:200],
                    # Браузеру нужен только валидированный UI-командный объект.
                    # Результаты чтения модель уже увидела на сервере; повторно
                    # отдавать в DOM списки лидов и событий незачем.
                    result=result if name in _UI_RESULT_TOOLS else {},
                )
            )

    # Достигли лимита итераций — вернём, что есть.
    log_event(
        db,
        user.id,
        "ai",
        "ИИ-помощник не успел довести задачу до конца — слишком много шагов. "
        "Попробуйте спросить конкретнее",
        "warn",
    )
    return AiChatOut(
        reply="[Достигнут лимит вызовов инструментов]",
        model=model,
        tool_calls=all_tool_calls,
    )


def _call_api(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """Один запрос к OpenAI-совместимому API.

    Возвращает весь ответ как есть: выбор между текстом и tool_calls делает
    вызывающая сторона. Ошибки HTTP/сети приводятся к обычному Exception с
    понятным сообщением — вызывающий оборачивает его в 502.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    if tools:
        body["tools"] = tools

    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
    except httpx.RequestError as exc:
        raise Exception(f"Сеть: {exc}") from exc

    if resp.status_code >= 400:
        try:
            message = resp.json().get("error", {}).get("message", resp.text)
        except (json.JSONDecodeError, ValueError):
            message = resp.text
        raise Exception(f"HTTP {resp.status_code}: {message}")

    return resp.json()
