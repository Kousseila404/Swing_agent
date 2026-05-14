"""Tests pour modules.yf_retry."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from modules.yf_circuit_breaker import yf_breaker
from modules.yf_retry import YFNonRetriable, yf_safe_call


@pytest.fixture(autouse=True)
def _reset_breaker():
    yf_breaker.reset()
    yield
    yf_breaker.reset()


class TestYfSafeCall:
    def test_returns_value_on_success(self):
        result = yf_safe_call(lambda: 42, label="test")
        assert result == 42

    def test_retries_transient_error_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("network blip")
            return "ok"

        with patch("modules.yf_retry.time.sleep"):  # accélère les tests
            result = yf_safe_call(fn, label="t", retries=3, backoff_base=0.01)
        assert result == "ok"
        assert calls["n"] == 3

    def test_exhausts_retries_raises_nonretriable(self):
        def fn():
            raise ConnectionError("persistent network down")

        with patch("modules.yf_retry.time.sleep"):
            with pytest.raises(YFNonRetriable):
                yf_safe_call(fn, label="t", retries=2, backoff_base=0.01)

    def test_rate_limit_trips_breaker_no_retry(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            # Message qui matche le pattern rate-limit du breaker
            raise RuntimeError("HTTP 429 Too Many Requests")

        with pytest.raises(YFNonRetriable):
            yf_safe_call(fn, label="t", retries=5, backoff_base=0.01)
        # Un seul appel — pas de retry sur 429.
        assert calls["n"] == 1
        assert yf_breaker.is_tripped()

    def test_breaker_open_raises_immediately(self):
        # Simule un breaker déjà OPEN.
        yf_breaker.record(RuntimeError("rate limit"))
        assert yf_breaker.is_tripped()

        with pytest.raises(YFNonRetriable):
            yf_safe_call(lambda: "never", label="t")

    def test_non_transient_non_rate_limit_propagates(self):
        # Ex: KeyError → erreur déterministe, doit remonter telle quelle.
        def fn():
            raise KeyError("missing column")

        with pytest.raises(KeyError):
            yf_safe_call(fn, label="t", retries=2)
