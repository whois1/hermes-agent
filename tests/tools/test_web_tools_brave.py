"""Tests for Brave Search web backend integration."""

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest


class TestBraveRequest:
    def test_raises_without_api_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRAVE_SEARCH_API_KEY", None)
            from tools.web_tools import _brave_request

            with pytest.raises(ValueError, match="BRAVE_SEARCH_API_KEY"):
                _brave_request("web/search", {"q": "test"})

    def test_gets_with_subscription_token_header(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"web": {"results": []}}
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-test-key"}):
            with patch("tools.web_tools.httpx.get", return_value=mock_response) as mock_get:
                from tools.web_tools import _brave_request

                result = _brave_request("web/search", {"q": "hello", "count": 2})

                mock_get.assert_called_once()
                call = mock_get.call_args
                assert "api.search.brave.com/res/v1/web/search" in call.args[0]
                assert call.kwargs["headers"]["X-Subscription-Token"] == "brave-test-key"
                assert call.kwargs["params"] == {"q": "hello", "count": 2}
                assert result == {"web": {"results": []}}


class TestNormalizeBraveSearchResults:
    def test_basic_normalization(self):
        from tools.web_tools import _normalize_brave_search_results

        raw = {
            "web": {
                "results": [
                    {"title": "One", "url": "https://one.test", "description": "First"},
                    {"title": "Two", "url": "https://two.test", "description": "Second"},
                ]
            }
        }

        result = _normalize_brave_search_results(raw)

        assert result["success"] is True
        assert result["data"]["web"][0] == {
            "title": "One",
            "url": "https://one.test",
            "description": "First",
            "position": 1,
        }
        assert result["data"]["web"][1]["position"] == 2

    def test_missing_web_results_is_empty(self):
        from tools.web_tools import _normalize_brave_search_results

        assert _normalize_brave_search_results({}) == {"success": True, "data": {"web": []}}


class TestWebSearchBrave:
    def test_search_dispatches_to_brave(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "web": {"results": [{"title": "Result", "url": "https://r.test", "description": "desc"}]}
        }
        mock_response.raise_for_status = MagicMock()

        with patch("tools.web_tools._get_backend", return_value="brave"), \
             patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-test"}), \
             patch("tools.web_tools.httpx.get", return_value=mock_response), \
             patch("tools.interrupt.is_interrupted", return_value=False):
            from tools.web_tools import web_search_tool

            result = json.loads(web_search_tool("test query", limit=3))

            assert result["success"] is True
            assert result["data"]["web"][0]["title"] == "Result"


class TestWebExtractBrave:
    def test_extract_uses_basic_direct_fetch(self):
        mock_response = MagicMock()
        mock_response.url = "https://example.test/page"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><head><title>Example</title></head><body><h1>Hello</h1><p>World</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("tools.web_tools._get_backend", return_value="brave"), \
             patch("tools.web_tools.httpx.get", return_value=mock_response), \
             patch("tools.web_tools.is_safe_url", return_value=True), \
             patch("tools.web_tools.check_website_access", return_value=None), \
             patch("tools.web_tools.process_content_with_llm", return_value=None):
            from tools.web_tools import web_extract_tool

            result = json.loads(asyncio.get_event_loop().run_until_complete(
                web_extract_tool(["https://example.test/page"], use_llm_processing=False)
            ))

            assert result["results"][0]["url"] == "https://example.test/page"
            assert result["results"][0]["title"] == "Example"
            assert "Hello" in result["results"][0]["content"]
            assert "World" in result["results"][0]["content"]
