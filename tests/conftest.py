import pytest


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    from fin_assistant.config import settings
    from fin_assistant.monitoring import tracing

    monkeypatch.setattr(settings, "langfuse_public_key", "")
    tracing._client.cache_clear()
    yield
    tracing._client.cache_clear()
