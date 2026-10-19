"""Provider diagnostics that never include response bodies, requests, or credentials."""
from __future__ import annotations


def safe_error(exc: Exception) -> dict:
    """Provider exception messages can echo a key. Retain only type and HTTP status."""
    try:
        status = getattr(exc, "status_code", None)
    except Exception:
        status = None
    result = {"ok": False, "error_type": type(exc).__name__}
    if isinstance(status, int) and 100 <= status <= 599:
        result["http_status"] = status
    return result


def error_label(result: dict) -> str:
    label = result.get("error_type", "unavailable")
    if result.get("http_status") is not None:
        label += f" (HTTP {result['http_status']})"
    return label
