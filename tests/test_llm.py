import json

import httpx
import pytest
from test_core import sample_analysis

from ema_rwe.domain import RWEError
from ema_rwe.llm import extract_with_provider


async def test_configured_provider_structured_output(settings, monkeypatch):
    settings.llm_base_url = "https://provider.example/v1"
    settings.llm_model = "test-model"
    original = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert "untrusted" in body["messages"][0]["content"]
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": sample_analysis().model_dump_json()}}]}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    result = await extract_with_provider(settings, [{"page": 1, "text": "Study design and data sources"}])
    assert result.data_sources[0].value == "Example Primary Care Database"


async def test_provider_invalid_json(settings, monkeypatch):
    settings.llm_base_url = "https://provider.example/v1"
    settings.llm_model = "test-model"
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not JSON"}}]})
            ),
            **kwargs,
        ),
    )
    with pytest.raises(RWEError, match="structured output"):
        await extract_with_provider(settings, [{"page": 1, "text": "Study design"}])
