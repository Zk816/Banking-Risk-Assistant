import hashlib
import hmac
import json
import os

import chainlit as cl
import httpx
from chainlit.server import app

BRAND_PATHS = ("/logo", "/favicon", "/avatars/", "/public/")


@app.middleware("http")
async def no_stale_brand(request, call_next):
    if request.url.path == "/logo" and b"theme=system" in request.scope.get("query_string", b""):
        request.scope["query_string"] = request.scope["query_string"].replace(b"theme=system", b"theme=dark")
    response = await call_next(request)
    if request.url.path.startswith(BRAND_PATHS):
        response.headers["Cache-Control"] = "no-cache"
    return response


API_URL = os.environ.get("KZBANK_API_URL", "http://localhost:8400")
HEADERS = {"X-API-Key": os.environ.get("KZBANK_API_KEY", "")}
TIMEOUT_S = 900.0
MAX_QUESTION_CHARS = int(os.environ.get("MAX_QUESTION_CHARS", "20000"))

TOOL_LABELS = {
    "get_bank_metrics": "Показатели банка",
    "compare_banks": "Сравнение банков",
    "rank_banks": "Рейтинг банков",
    "metric_history": "Динамика показателя",
    "sector_history": "Динамика по сектору",
    "list_banks": "Список банков",
    "get_ratings": "Кредитные рейтинги",
    "country_indicator": "Показатели стран",
    "get_base_rate": "Базовая ставка",
    "get_exchange_rate": "Курс валют",
    "search_documents": "Поиск по документам",
    "web_search": "Поиск в интернете",
    "fetch_page": "Чтение страницы",
}


HISTORY_TURNS = 6
HISTORY_CHARS = 3000


def _users() -> dict[str, str]:
    out = {}
    for entry in os.environ.get("CHAT_USERS", "").split(","):
        name, _, stored = entry.strip().partition(":")
        if name and stored:
            out[name] = stored
    return out


@cl.password_auth_callback
def auth(username: str, password: str) -> cl.User | None:
    stored = _users().get(username)
    if not stored:
        return None
    _, iterations, salt, digest = stored.split(":")
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return cl.User(identifier=username) if hmac.compare_digest(candidate, digest) else None


@cl.on_chat_resume
async def on_resume(thread) -> None:
    return None


def _history() -> list[dict[str, str]]:
    turns = [m for m in cl.chat_context.to_openai() if m.get("role") in ("user", "assistant")][:-1]
    return [
        {"role": m["role"], "content": str(m.get("content") or "")[:HISTORY_CHARS]}
        for m in turns[-HISTORY_TURNS:]
    ]


@cl.set_starters
async def starters() -> list[cl.Starter]:
    return [
        cl.Starter(
            label="Проблемные кредиты", message="У каких банков самая высокая доля проблемных кредитов?"
        ),
        cl.Starter(label="Капитал Kaspi", message="Соблюдает ли Kaspi норматив достаточности капитала k2?"),
        cl.Starter(label="Рейтинги Halyk", message="Какие кредитные рейтинги у Halyk Bank?"),
        cl.Starter(
            label="Гарантия депозитов",
            message="Какая максимальная сумма гарантийного возмещения по депозитам?",
        ),
        cl.Starter(
            label="Анализ своей отчетности",
            message="Прикрепите Excel-файл с балансом, ОПиУ или ДДС (скрепка слева), и я посчитаю коэффициенты и риски.",
        ),
    ]


async def analyze_upload(message: cl.Message, element) -> None:
    with open(element.path, "rb") as f:
        data = f.read()
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=HEADERS) as client:
        question = (message.content or "").strip()[:MAX_QUESTION_CHARS] or "Проанализируй риски."
        r = await client.post(
            f"{API_URL}/v1/analyze", files={"file": (element.name, data)}, data={"question": question}
        )
    body = r.json()
    if r.status_code != 200:
        await cl.Message(content=f"Не удалось проанализировать файл: {body.get('message')}").send()
        return
    if body.get("kind") == "table":
        await cl.Message(
            content=f"{body['analysis']}\n\n---\n_Файл распознан как таблица данных, а не финансовая "
            f"отчетность · {body['duration_ms'] / 1000:.1f} с_"
        ).send()
        return
    async with cl.Step(name="Сопоставление строк отчетности", type="tool") as step:
        step.output = "\n".join(f"{k} ← {v}" for k, v in body["mapped_rows"].items())
    rows = [
        f"| {x['meaning']} | " + " | ".join(x["values"].get(p, "—") for p in body["periods"]) + " |"
        for x in body["ratios"]
    ]
    table = (
        (
            "| Показатель | "
            + " | ".join(body["periods"])
            + " |\n|---|"
            + "---|" * len(body["periods"])
            + "\n"
            + "\n".join(rows)
        )
        if rows
        else ""
    )
    flags = "\n".join(f"- ⚠️ {f}" for f in body["risk_flags"]) or "- нет сигналов риска"
    await cl.Message(
        content=f"{body['analysis']}\n\n---\n**Рассчитанные показатели**\n\n{table}\n\n"
        f"**Сигналы риска**\n{flags}\n\n_{body['duration_ms'] / 1000:.1f} с_"
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    files = [e for e in (message.elements or []) if getattr(e, "path", None)]
    if files:
        await analyze_upload(message, files[0])
        return
    question = (message.content or "").strip()
    if len(question) > MAX_QUESTION_CHARS:
        await cl.Message(
            content=f"_Сообщение длиннее {MAX_QUESTION_CHARS} символов — беру первые {MAX_QUESTION_CHARS}. "
            f"Большие таблицы лучше прикрепить файлом._"
        ).send()
        question = question[:MAX_QUESTION_CHARS]
    body, steps = None, {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=HEADERS) as client:
            async with client.stream(
                "POST", f"{API_URL}/v1/ask/stream", json={"question": question, "history": _history()}
            ) as r:
                if r.status_code != 200:
                    await cl.Message(
                        content=f"Ошибка {r.status_code}: {(await r.aread()).decode()[:300]}"
                    ).send()
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "tool_start":
                        step = cl.Step(name=TOOL_LABELS.get(event["name"], event["name"]), type="tool")
                        step.input = event["arguments"]
                        await step.send()
                        steps.setdefault(event["name"], []).append(step)
                    elif event["type"] == "tool_done" and steps.get(event["name"]):
                        step = steps[event["name"]].pop(0)
                        step.output = "найдено" if event.get("found") else "нет данных"
                        await step.update()
                    elif event["type"] == "retry":
                        await cl.Message(content="_Перепроверяю цифры по источникам…_").send()
                    elif event["type"] == "error":
                        await cl.Message(
                            content=f"Ошибка: {event['message']} (request_id {event['request_id']})"
                        ).send()
                        return
                    elif event["type"] == "answer":
                        body = event["body"]
    except httpx.HTTPError as e:
        await cl.Message(content=f"API недоступен: {e}").send()
        return
    if body is None:
        await cl.Message(content="Ответ не получен.").send()
        return

    footer = []
    if body.get("sources"):
        footer.append("**Источники данных:** " + ", ".join(dict.fromkeys(body["sources"])))
    seen, cites = set(), []
    for c in body.get("citations", []):
        if c["citation"] not in seen:
            seen.add(c["citation"])
            cites.append(f"- {c['citation']}" + (f" ({c['date']})" if c.get("date") else ""))
    if cites:
        footer.append("**Документы:**\n" + "\n".join(cites[:8]))
    footer.append(f"_{body['duration_ms'] / 1000:.1f} с · request_id {body['request_id']}_")
    await cl.Message(content=body["answer"] + "\n\n---\n" + "\n\n".join(footer)).send()
