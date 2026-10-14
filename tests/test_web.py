from fin_assistant.tools import web


def test_internal_addresses_are_refused() -> None:
    for url in [
        "http://localhost:5435",
        "http://127.0.0.1:8320/v1/models",
        "http://10.0.0.5/admin",
        "http://192.168.1.1",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://[::1]:8400/",
    ]:
        result = web.fetch_page(url)
        assert result["found"] is False and "Адрес недоступен" in result["reason"], url


def test_official_sources_are_recognised() -> None:
    for host in ["kgd.gov.kz", "www.gov.kz", "adilet.zan.kz", "court.aifc.kz", "kase.kz"]:
        assert web.OFFICIAL.search(host), host
    assert not web.OFFICIAL.search("mybuh.kz")


def test_schemas_and_functions_match() -> None:
    assert {s["function"]["name"] for s in web.TOOL_SCHEMAS} == set(web.TOOL_REGISTRY)
