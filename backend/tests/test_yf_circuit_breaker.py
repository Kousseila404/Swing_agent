"""Tests du circuit-breaker yfinance (modules/yf_circuit_breaker.py)."""
from __future__ import annotations

import pytest
import requests

from modules.yf_circuit_breaker import (
    RateLimitTripped,
    YFCircuitBreaker,
    _looks_like_rate_limit,
)


@pytest.fixture()
def breaker() -> YFCircuitBreaker:
    return YFCircuitBreaker()


def _make_http_error(status_code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    return requests.HTTPError(f"{status_code} Error", response=resp)


def test_detection_regex_429():
    assert _looks_like_rate_limit(Exception("Edge: Too Many Requests 429"))


def test_detection_regex_rate_limit():
    assert _looks_like_rate_limit(Exception("YFRateLimitError: rate limit exceeded"))


def test_detection_response_status():
    assert _looks_like_rate_limit(_make_http_error(429))
    assert _looks_like_rate_limit(_make_http_error(403))


def test_detection_negative():
    assert not _looks_like_rate_limit(Exception("timeout after 15s"))
    assert not _looks_like_rate_limit(_make_http_error(500))


def test_closed_by_default(breaker: YFCircuitBreaker):
    assert breaker.is_tripped() is False
    breaker.ensure_closed()  # ne doit pas lever


def test_record_trips_on_429(breaker: YFCircuitBreaker):
    tripped = breaker.record(_make_http_error(429))
    assert tripped is True
    assert breaker.is_tripped() is True


def test_record_ignores_non_rate_limit(breaker: YFCircuitBreaker):
    tripped = breaker.record(ConnectionError("net down"))
    assert tripped is False
    assert breaker.is_tripped() is False


def test_ensure_closed_raises_when_tripped(breaker: YFCircuitBreaker):
    breaker.record(_make_http_error(429))
    with pytest.raises(RateLimitTripped):
        breaker.ensure_closed()


def test_reset_closes_breaker(breaker: YFCircuitBreaker):
    breaker.record(_make_http_error(429))
    assert breaker.is_tripped() is True
    breaker.reset()
    assert breaker.is_tripped() is False
    breaker.ensure_closed()


def test_snapshot_contains_reason(breaker: YFCircuitBreaker):
    breaker.record(Exception("Too Many Requests"))
    snap = breaker.snapshot()
    assert snap["tripped"] is True
    assert "Too Many Requests" in snap["reason"]
    assert snap["tripped_at"] is not None


def test_double_trip_is_idempotent(breaker: YFCircuitBreaker):
    """Record successive doit conserver la 1re raison (ne pas masquer l'origine)."""
    breaker.record(Exception("first: 429 rate limit"))
    first_reason = breaker.snapshot()["reason"]
    breaker.record(Exception("second: 429 again"))
    assert breaker.snapshot()["reason"] == first_reason


# ─────────────────────────────────────────────────────────────────
# Lot 11 — auto-reset après cooldown
# ─────────────────────────────────────────────────────────────────

def test_auto_reset_after_cooldown_expires():
    """Tripped → wait > cooldown → next is_tripped() → False."""
    import time as _time
    b = YFCircuitBreaker(cooldown_seconds=1)
    b.record(_make_http_error(429))
    assert b.is_tripped() is True
    _time.sleep(1.1)
    assert b.is_tripped() is False
    b.ensure_closed()  # ne lève plus


def test_auto_reset_not_triggered_within_cooldown():
    """Tripped → wait < cooldown → still tripped."""
    b = YFCircuitBreaker(cooldown_seconds=60)
    b.record(_make_http_error(429))
    assert b.is_tripped() is True
    # cooldown 60s mais on attend 0s → toujours tripped
    assert b.is_tripped() is True
    with pytest.raises(RateLimitTripped):
        b.ensure_closed()


def test_snapshot_exposes_cooldown_and_age():
    """snapshot() doit exposer cooldown_seconds, tripped_age_sec, auto_reset_in_sec."""
    b = YFCircuitBreaker(cooldown_seconds=300)
    b.record(_make_http_error(429))
    snap = b.snapshot()
    assert snap["cooldown_seconds"] == 300
    assert snap["tripped_age_sec"] is not None
    assert snap["tripped_age_sec"] >= 0
    assert snap["auto_reset_in_sec"] is not None
    assert snap["auto_reset_in_sec"] <= 300


def test_snapshot_no_age_when_closed(breaker: YFCircuitBreaker):
    snap = breaker.snapshot()
    assert snap["tripped"] is False
    assert snap["tripped_age_sec"] is None
    assert snap["auto_reset_in_sec"] is None
    assert snap["cooldown_seconds"] > 0
