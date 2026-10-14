import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SMOKE = os.environ.get("SMOKE") == "1"
_SUFFIX = "_smoke" if SMOKE else ""
DATA = ROOT / "data" / f"finetune{_SUFFIX}"
MODELS = ROOT / f"models{_SUFFIX}"
RESULTS = ROOT / "experiments" / "finetune" / f"results{_SUFFIX}"
for _d in (DATA, MODELS, RESULTS):
    _d.mkdir(parents=True, exist_ok=True)
TEST_SHARE = 15
TEACHER_WORKERS = 32

REFUSAL_RU = "В предоставленных источниках нет ответа на этот вопрос."
REFUSAL_EN = "The provided sources do not answer this question."


def split_of(document_id: int) -> str:
    return (
        "test" if int(hashlib.md5(str(document_id).encode()).hexdigest(), 16) % 100 < TEST_SHARE else "train"
    )


def bucket(key: str, share: int) -> bool:
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 100 < share


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def is_english(text: str) -> bool:
    latin = len(re.findall(r"[A-Za-z]", text))
    cyr = len(re.findall(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]", text))
    return latin > 2 * cyr


def is_refusal(text: str) -> bool:
    t = text.lower()
    return any(
        p in t
        for p in (
            "нет ответа на этот вопрос",
            "do not answer this question",
            "не содержат ответа",
            "do not contain",
        )
    )


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def teacher(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 800,
    thinking: str | None = None,
    temperature: float = 0.0,
) -> str:
    from fin_assistant.agent.llm import get_client
    from fin_assistant.config import settings

    extra = (
        {"chat_template_kwargs": {"enable_thinking": False}}
        if thinking is None
        else {"reasoning_effort": thinking}
    )
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = get_client().chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra,
            )
            return strip_think(r.choices[0].message.content or "")
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"teacher failed 3 times: {last}") from last


def parse_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def parallel_map(
    fn: Callable[[Any], Any], items: list[Any], *, workers: int = TEACHER_WORKERS, label: str = ""
) -> list[Any]:
    out: list[Any] = [None] * len(items)
    failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, x): i for i, x in enumerate(items)}
        for done, fut in enumerate(as_completed(futures), start=1):
            try:
                out[futures[fut]] = fut.result()
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  [{label}] item failed: {e}", flush=True)
            if done % 200 == 0 or done == len(items):
                print(
                    f"  [{label}] {done}/{len(items)} done, {failed} failed, {time.time() - started:.0f}s",
                    flush=True,
                )
    return out


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
