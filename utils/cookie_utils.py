from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

# RFC6265 token 分隔符与空白字符
INVALID_COOKIE_NAME_CHARS = set('()<>@,;:\\"/[]?={} \t\r\n')

# Douyin's logged-in web APIs expect both the primary cookie and its
# ``_ss`` / ``_default`` twin. Playwright often only stores one of the
# pair; mirroring the non-empty value keeps the request looking like a
# real browser session.
COOKIE_ALIAS_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("sessionid", "sessionid_ss"),
    ("uid_tt", "uid_tt_ss"),
    ("passport_csrf_token", "passport_csrf_token_default"),
)


def is_valid_cookie_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in name):
        return False
    if any(ch in INVALID_COOKIE_NAME_CHARS for ch in name):
        return False
    return True


def sanitize_cookies(cookies: Mapping[Any, Any]) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    for raw_key, raw_value in (cookies or {}).items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not is_valid_cookie_name(key):
            continue
        value = "" if raw_value is None else str(raw_value).strip()
        sanitized[key] = value
    return sanitized


def apply_cookie_aliases(cookies: Mapping[Any, Any]) -> Dict[str, str]:
    """Fill missing ``sessionid_ss`` / ``uid_tt_ss`` / csrf-default twins."""
    mirrored = sanitize_cookies(cookies)
    for source, alias in COOKIE_ALIAS_PAIRS:
        source_value = (mirrored.get(source) or "").strip()
        alias_value = (mirrored.get(alias) or "").strip()
        if source_value and not alias_value:
            mirrored[alias] = source_value
        elif alias_value and not source_value:
            mirrored[source] = alias_value
    return mirrored


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    if not cookie_header:
        return {}
    parsed: Dict[str, str] = {}
    for item in cookie_header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if not is_valid_cookie_name(key):
            continue
        parsed[key] = value.strip()
    return parsed
