"""The one place we call a language model.

Points at any OpenAI-compatible server. Today that is the local ollama on
:11435; swapping to vLLM, or to a hosted API, is a change to .env plus a client
constructor — nothing above this file knows the difference.
"""

import time
from functools import lru_cache

from openai import APIConnectionError, APIStatusError, OpenAI

from kzbank.config import settings
from kzbank.logging_setup import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """One client per process."""
    return OpenAI(
        base_url=settings.llm_base_url,
        # Local servers ignore this, but the client requires a non-empty string.
        api_key="local",
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )


def chat(system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 1000) -> str:
    """Send one system+user turn and return the reply text.

    temperature=0 by default: the same sources must give the same answer.
    Creativity is a liability when the output cites law.

    Raises:
        RuntimeError: if the server is unreachable or returns an error status.
    """
    started = time.perf_counter()
    try:
        response = get_client().chat.completions.create(
            model=settings.llm_model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except APIConnectionError as e:
        log.error("llm unreachable at %s after %.1fs", settings.llm_base_url,
                  time.perf_counter() - started)
        raise RuntimeError(
            f"Cannot reach the model server at {settings.llm_base_url}. "
            f"Is ollama running?  curl {settings.llm_base_url}/models"
        ) from e
    except APIStatusError as e:
        log.error("llm returned %s for model %s", e.status_code, settings.llm_model)
        raise RuntimeError(
            f"Model server returned {e.status_code} for model "
            f"'{settings.llm_model}': {e.message}"
        ) from e

    usage = response.usage
    log.info(
        "llm model=%s %.2fs prompt=%s completion=%s",
        settings.llm_model,
        time.perf_counter() - started,
        usage.prompt_tokens if usage else "?",
        usage.completion_tokens if usage else "?",
    )

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("Model returned an empty response.")
    return content.strip()
