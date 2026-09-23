import argparse
import asyncio
import sys
import time
import types
from pathlib import Path

import pytest

from tools.cookie_fetcher import (
    extract_ms_token_from_text,
    extract_query_value_from_text,
    filter_cookies,
    goto_with_fallback,
    pick_observed_value,
    try_extract_ms_token,
    wait_for_login_confirmation,
)


class FakePage:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "wait_until": wait_until,
                "timeout": timeout,
            }
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SlowPage:
    def __init__(self):
        self.calls = []
        self.cancelled = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "wait_until": wait_until,
                "timeout": timeout,
            }
        )
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def evaluate(self, _):
        return ""


def test_goto_with_fallback_when_networkidle_timeout():
    page = FakePage([TimeoutError("network idle timeout"), object()])
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "domcontentloaded"
    assert page.calls[0]["wait_until"] == "networkidle"
    assert page.calls[1]["wait_until"] == "domcontentloaded"


def test_goto_with_fallback_raises_non_timeout_errors():
    page = FakePage([RuntimeError("unexpected error")])

    with pytest.raises(RuntimeError, match="unexpected error"):
        asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert len(page.calls) == 1


def test_goto_with_fallback_handles_target_closed():
    class TargetClosedError(Exception):
        pass

    page = FakePage([TargetClosedError("Target page, context or browser has been closed")])
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "target_closed"
    assert len(page.calls) == 1


def test_goto_with_fallback_returns_timeout_when_fallback_also_times_out():
    page = FakePage([TimeoutError("primary timeout"), TimeoutError("fallback timeout")])
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "timeout"
    assert len(page.calls) == 2


def test_wait_for_login_confirmation_returns_without_waiting_navigation():
    page = SlowPage()
    started = time.time()

    asyncio.run(
        wait_for_login_confirmation(
            page,
            "https://www.douyin.com/",
            input_func=lambda: "",
        )
    )
    elapsed = time.time() - started

    assert elapsed < 1
    assert len(page.calls) == 1
    assert page.cancelled is True


def test_wait_for_login_confirmation_handles_completed_navigation():
    page = FakePage([object()])

    asyncio.run(
        wait_for_login_confirmation(
            page,
            "https://www.douyin.com/",
            input_func=lambda: "",
        )
    )

    assert len(page.calls) == 1


def test_try_extract_ms_token_from_observed_headers():
    page = SlowPage()

    token = asyncio.run(
        try_extract_ms_token(
            page,
            {"ttwid": "x"},
            ["ttwid=abc; msToken=token-from-header"],
            [],
        )
    )

    assert token == "token-from-header"


def test_extract_ms_token_from_text_supports_json_and_query_formats():
    assert (
        extract_ms_token_from_text("https://www.douyin.com/?foo=1&msToken=query-token&bar=2")
        == "query-token"
    )
    assert extract_ms_token_from_text('{"msToken":"json-token","x":1}') == "json-token"


def test_filter_cookies_keeps_waf_and_fingerprint_keys_but_drops_unrelated_keys():
    cookies = filter_cookies(
        {
            "ttwid": "ttwid-token",
            "msToken": "ms-token",
            "_waftokenid": "waf-token",
            "s_v_web_id": "verify-id",
            "__ac_signature": "ac-signature",
            "random_cookie": "should-be-filtered",
        }
    )

    assert cookies["ttwid"] == "ttwid-token"
    assert cookies["msToken"] == "ms-token"
    assert cookies["_waftokenid"] == "waf-token"
    assert cookies["s_v_web_id"] == "verify-id"
    assert cookies["__ac_signature"] == "ac-signature"
    assert "random_cookie" not in cookies


def test_filter_cookies_keeps_login_twins_and_mirrors_sessionid():
    cookies = filter_cookies(
        {
            "ttwid": "ttwid-token",
            "sessionid": "sess-1",
            "uid_tt": "uid-1",
            "passport_csrf_token": "csrf-1",
            "webid": "webid-1",
            "random_cookie": "drop-me",
        }
    )

    assert cookies["sessionid"] == "sess-1"
    assert cookies["sessionid_ss"] == "sess-1"
    assert cookies["uid_tt"] == "uid-1"
    assert cookies["uid_tt_ss"] == "uid-1"
    assert cookies["passport_csrf_token_default"] == "csrf-1"
    assert cookies["webid"] == "webid-1"
    assert "random_cookie" not in cookies


def test_extract_query_value_from_text_reads_webid():
    url = "https://www.douyin.com/aweme/v1/web/aweme/post/?webid=12345&msToken=x"
    assert extract_query_value_from_text(url, "webid") == "12345"
    assert pick_observed_value(["", " first ", "second"]) == "second"


@pytest.mark.asyncio
async def test_capture_cookies_returns_1_when_browser_launch_fails(monkeypatch):
    class _Mgr:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    fake_playwright = types.ModuleType("playwright")
    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: _Mgr()
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("无法启动浏览器")

    monkeypatch.setattr("utils.browser_launch.launch_playwright_browser", _boom)

    import tools.cookie_fetcher as cf

    args = argparse.Namespace(
        browser="chromium",
        headless=False,
        url="https://www.douyin.com/",
        output=Path("cookies.json"),
        config=None,
        include_all=False,
    )
    assert await cf.capture_cookies(args) == 1
