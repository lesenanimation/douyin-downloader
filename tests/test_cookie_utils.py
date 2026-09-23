from utils.cookie_utils import apply_cookie_aliases, parse_cookie_header, sanitize_cookies


def test_sanitize_cookies_filters_illegal_keys():
    raw = {
        "": "douyin.com",
        " ttwid ": "ttwid-value",
        "msToken": "token-value",
        "bad;key": "x",
        "ok-key": "ok",
    }

    sanitized = sanitize_cookies(raw)

    assert sanitized["ttwid"] == "ttwid-value"
    assert sanitized["msToken"] == "token-value"
    assert sanitized["ok-key"] == "ok"
    assert "" not in sanitized
    assert "bad;key" not in sanitized


def test_parse_cookie_header_skips_invalid_parts():
    parsed = parse_cookie_header("ttwid=aaa; ; bad; msToken=bbb; =foo; bad;key=1")

    assert parsed["ttwid"] == "aaa"
    assert parsed["msToken"] == "bbb"
    assert "" not in parsed
    assert "bad;key" not in parsed


def test_apply_cookie_aliases_mirrors_login_twins():
    mirrored = apply_cookie_aliases(
        {
            "sessionid": "sess-1",
            "uid_tt_ss": "uid-1",
            "passport_csrf_token": "csrf-1",
        }
    )

    assert mirrored["sessionid_ss"] == "sess-1"
    assert mirrored["uid_tt"] == "uid-1"
    assert mirrored["passport_csrf_token_default"] == "csrf-1"
