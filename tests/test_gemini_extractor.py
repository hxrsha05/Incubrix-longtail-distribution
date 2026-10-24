"""The Gemini reader sends the page and the key (in a header), parses the answer, and its mistakes are caught by the same verification."""
import json

import httpx
import pytest

from app.errors import IncuBrixError
from app.rules_capture import GeminiExtractor, verify

PAGE = "Community rules. Self-promotion is not allowed. You may post one post per week. Bots are not allowed on this forum."


def fake_gemini(rules, status=200, usage=None):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"], seen["body"] = str(request.url), dict(request.headers), json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "x"}}, headers={"retry-after": "7"})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"rules": rules})}]}}],
                                         "usageMetadata": usage or {"promptTokenCount": 900, "candidatesTokenCount": 120}})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_the_request_carries_the_page_and_the_key_in_a_header_not_in_the_address():
    http, seen = fake_gemini([{"rule": "self_promo_allowed", "value": False, "quote": "Self-promotion is not allowed.", "confidence": 0.9}])
    out = GeminiExtractor(http, "gem-secret-1", "gemini-flash-test", price_in=0.1, price_out=0.4).extract(PAGE, "https://forum.example/rules")
    assert seen["url"].endswith("/v1beta/models/gemini-flash-test:generateContent") and "gem-secret-1" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "gem-secret-1"
    assert "Self-promotion" in seen["body"]["contents"][0]["parts"][0]["text"] and seen["body"]["generationConfig"]["temperature"] == 0
    assert out["rules"][0]["rule"] == "self_promo_allowed" and out["usage"] == {"input_tokens": 900, "output_tokens": 120}


def test_an_invented_quote_is_dropped_by_the_same_verification():
    http, _ = fake_gemini([
        {"rule": "self_promo_allowed", "value": False, "quote": "Self-promotion is not allowed.", "confidence": 0.9},
        {"rule": "link_policy", "value": "none", "quote": "Links are never allowed anywhere.", "confidence": 0.9},  # not on the page
    ])
    found = GeminiExtractor(http, "k", "m").extract(PAGE, None)
    rules, evidence, dropped = verify(found["rules"], PAGE)
    assert list(rules) == ["self_promo_allowed"] and "possible invention" in dropped[0]["why"]


@pytest.mark.parametrize("status,code", [(429, "PLATFORM_RATE_LIMIT"), (403, "CONNECTION_AUTH_EXPIRED"), (500, "PLATFORM_UNAVAILABLE")])
def test_model_errors_become_the_shared_error_codes(status, code):
    http, _ = fake_gemini([], status=status)
    with pytest.raises(IncuBrixError) as err:
        GeminiExtractor(http, "k", "m").extract(PAGE, None)
    assert err.value.code.value == code


def test_cost_comes_from_the_configured_prices():
    assert GeminiExtractor(httpx.Client(), "k", "m", price_in=0.1, price_out=0.4).cost_usd({"input_tokens": 1_000_000, "output_tokens": 500_000}) == 0.3
