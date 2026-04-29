"""Test earnings calendar gate dans auto_proposer."""
from __future__ import annotations

from datetime import date, timedelta

from modules.auto_proposer import _days_until_earnings


def test_days_until_earnings_future():
    target = (date.today() + timedelta(days=5)).isoformat()
    assert _days_until_earnings(target) == 5


def test_days_until_earnings_past():
    target = (date.today() - timedelta(days=2)).isoformat()
    assert _days_until_earnings(target) == -2


def test_days_until_earnings_invalid():
    assert _days_until_earnings(None) is None
    assert _days_until_earnings("") is None
    assert _days_until_earnings("not-a-date") is None


def test_days_until_earnings_with_time_suffix():
    target = (date.today() + timedelta(days=10)).isoformat() + " 12:00:00"
    # Notre fonction ne lit que les 10 premiers chars → date pure parsable.
    assert _days_until_earnings(target) == 10
