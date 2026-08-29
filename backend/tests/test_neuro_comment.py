"""Отбраковка машинного текста и защита от повторов.

Смысл проверок: комментарий, который видно как «написанный нейросетью», хуже
отсутствия комментария — он и не сработает, и привлечёт внимание к аккаунту.
Поэтому фильтр обязан ловить характерные обороты, а не только длину.
"""

from __future__ import annotations

import pytest

from app import neuro_comment


@pytest.mark.parametrize(
    "text",
    [
        "Здравствуйте! Хотел бы поделиться своим мнением по этому вопросу.",
        "Отличный пост, очень полезно!",
        "Спасибо за информацию, буду знать",
        "Полностью согласен с автором",
        "Таким образом, подход выглядит рабочим",
        "Важно отметить, что тут есть нюанс",
        "данный подход мне нравится",
        "Уникальное предложение для всех участников",
        "круто #нейросети #ии",
    ],
)
def test_machine_phrases_are_rejected(text):
    assert neuro_comment.sanity_check(text)


@pytest.mark.parametrize(
    "text",
    [
        "а на каком железе гоняешь?",
        "у меня на 3060 та же ошибка вылезала, помог даунгрейд драйвера",
        "второй пункт спорный, на практике всё упирается в датасет",
        "жаль что без исходников",
    ],
)
def test_human_comments_pass(text):
    assert neuro_comment.sanity_check(text) == ""


def test_length_bounds():
    assert neuro_comment.sanity_check("ок") == "слишком короткий"
    assert "длиннее" in neuro_comment.sanity_check("а" * (neuro_comment.MAX_CHARS + 1))


def test_emoji_garland_is_rejected_but_single_emoji_is_fine():
    assert neuro_comment.sanity_check("ну наконец-то 🔥") == ""
    assert neuro_comment.sanity_check("супер 🔥🔥🔥") == "больше одного эмодзи"


def test_repeated_text_is_rejected():
    """Повтор одного текста в разных чатах — главный триггер антиспама Telegram."""
    recent = ["у меня на 3060 та же ошибка вылезала, помог даунгрейд драйвера"]
    assert neuro_comment.sanity_check(recent[0], recent) == "слишком похож на предыдущий комментарий"
    # Перефразировка тоже не проходит.
    almost = "у меня на 3060 такая же ошибка вылезала, помог даунгрейд драйвера"
    assert neuro_comment.sanity_check(almost, recent)
    assert neuro_comment.sanity_check("а на каком железе гоняешь?", recent) == ""


def test_multiline_essay_is_rejected():
    assert neuro_comment.sanity_check("раз\nдва\nтри\nчетыре")


def test_prompt_forbids_known_tells():
    """Запреты живут в промпте, а не только в постфильтре."""
    prompt = neuro_comment.SYSTEM_PROMPT
    for marker in ("Отличный пост", "Здравствуйте", "Подводя итог", "эмодзи"):
        assert marker in prompt
    assert "недоверенные ДАННЫЕ" in prompt


def test_user_message_includes_recent_and_hides_link_when_empty():
    message = neuro_comment.build_user_message(
        "текст поста",
        chat_title="Чат",
        persona="",
        link="",
        recent=["прошлый коммент"],
    )
    assert "прошлый коммент" in message
    assert "Ссылки и названия сервисов не упоминай." in message


def test_user_message_allows_link_only_as_advice():
    message = neuro_comment.build_user_message(
        "текст поста", chat_title="Чат", persona="", link="https://example.com"
    )
    assert "https://example.com" in message
    assert "не как реклама" in message


def test_post_text_is_truncated_for_prompt():
    message = neuro_comment.build_user_message(
        "я" * 5000, chat_title="Чат", persona="", link=""
    )
    assert "…" in message
    assert len(message) < 3000


def test_generate_requires_configured_provider():
    with pytest.raises(neuro_comment.NeuroCommentError):
        neuro_comment.generate(
            "пост", chat_title="Чат", persona="", link="", base_url="", api_key="", model=""
        )


def test_generate_retries_until_text_passes(monkeypatch):
    """Модель нарушает запреты не всегда — плохой ответ должен перегенерироваться."""
    answers = iter(["Отличный пост, спасибо!", "а на каком железе гоняешь?"])

    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": next(answers)}}]}

    monkeypatch.setattr(neuro_comment.httpx, "post", lambda *a, **k: Response())
    text = neuro_comment.generate(
        "пост про обучение модели",
        chat_title="Чат",
        persona="",
        link="",
        base_url="http://ai",
        api_key="k",
        model="m",
    )
    assert text == "а на каком железе гоняешь?"


def test_generate_gives_up_after_attempts(monkeypatch):
    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "Отличный пост!"}}]}

    monkeypatch.setattr(neuro_comment.httpx, "post", lambda *a, **k: Response())
    with pytest.raises(neuro_comment.NeuroCommentError):
        neuro_comment.generate(
            "пост",
            chat_title="Чат",
            persona="",
            link="",
            base_url="http://ai",
            api_key="k",
            model="m",
            attempts=2,
        )


def test_generate_strips_wrapping_quotes(monkeypatch):
    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '«а сколько по времени вышло?»'}}]}

    monkeypatch.setattr(neuro_comment.httpx, "post", lambda *a, **k: Response())
    text = neuro_comment.generate(
        "пост", chat_title="Чат", persona="", link="",
        base_url="http://ai", api_key="k", model="m",
    )
    assert text == "а сколько по времени вышло?"
