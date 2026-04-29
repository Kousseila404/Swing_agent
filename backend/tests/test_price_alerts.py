"""Tests unitaires — modules/price_alerts.py

Couvre :
  - add_alert (validation, idempotence par (ticker, direction, target))
  - list_alerts (filter par ticker, include_expired)
  - remove_alert
  - evaluate_alerts (fire below/above, cooldown, expired ignorées)
  - compute_stats (hit_rate, median discount, buckets)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from modules import price_alerts as pa


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    """Redirige le storage vers tmp_path pour ne pas polluer data/price_alerts.json."""
    monkeypatch.setattr(pa, "ALERTS_PATH", tmp_path / "price_alerts.json")
    monkeypatch.setattr(pa, "ALERTS_LOCK_PATH", tmp_path / "price_alerts.json.lock")
    monkeypatch.setattr(pa, "_lookup_current_price", lambda _t: None)
    return tmp_path


# ─────────────────────────────────────────────────────────────────
# add_alert
# ─────────────────────────────────────────────────────────────────

def test_add_alert_basic():
    out = pa.add_alert(ticker="MU", target_price=477.66, direction="below",
                       weight_pct=40.0, note="Tier 1")
    assert out["ticker"] == "MU"
    assert out["direction"] == "below"
    assert out["target_price"] == 477.66
    assert out["weight_pct"] == 40.0
    assert out["fire_count"] == 0
    assert out["id"].startswith("PRA_")


def test_add_alert_uppercases_ticker():
    out = pa.add_alert(ticker="aapl", target_price=150)
    assert out["ticker"] == "AAPL"


def test_add_alert_rejects_invalid_direction():
    with pytest.raises(ValueError, match="direction"):
        pa.add_alert(ticker="MU", target_price=100, direction="sideways")


def test_add_alert_rejects_negative_price():
    with pytest.raises(ValueError, match="> 0"):
        pa.add_alert(ticker="MU", target_price=-5)


def test_add_alert_rejects_empty_ticker():
    with pytest.raises(ValueError, match="ticker"):
        pa.add_alert(ticker="", target_price=100)


def test_add_alert_idempotent():
    """Re-créer la même (ticker, direction, target) ne duplique pas."""
    a = pa.add_alert(ticker="MU", target_price=477.66, direction="below")
    b = pa.add_alert(ticker="MU", target_price=477.66, direction="below")
    assert a["id"] == b["id"]
    assert len(pa.list_alerts()) == 1


def test_add_alert_with_ttl_sets_expiry():
    out = pa.add_alert(ticker="MU", target_price=100, ttl_days=7)
    assert out["expires_at"] is not None


# ─────────────────────────────────────────────────────────────────
# list_alerts / remove_alert
# ─────────────────────────────────────────────────────────────────

def test_list_alerts_filters_by_ticker():
    pa.add_alert(ticker="MU", target_price=100)
    pa.add_alert(ticker="AAPL", target_price=150)
    out = pa.list_alerts(ticker="MU")
    assert len(out) == 1 and out[0]["ticker"] == "MU"


def test_list_alerts_include_expired_false():
    """expires_at < now → exclu si include_expired=False."""
    past = (pa._now_utc() - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    a = pa.add_alert(ticker="MU", target_price=100)
    # Hack : modifier expires_at dans le storage
    items = pa._read_all_unlocked()
    items[0]["expires_at"] = past
    pa._write_all_unlocked(items)

    assert len(pa.list_alerts(include_expired=True)) == 1
    assert len(pa.list_alerts(include_expired=False)) == 0
    _ = a  # unused


def test_remove_alert():
    out = pa.add_alert(ticker="MU", target_price=100)
    assert pa.remove_alert(out["id"]) is True
    assert pa.list_alerts() == []


def test_remove_alert_unknown_id():
    assert pa.remove_alert("PRA_NOPE") is False


# ─────────────────────────────────────────────────────────────────
# evaluate_alerts
# ─────────────────────────────────────────────────────────────────

def test_evaluate_below_fires_when_price_drops():
    pa.add_alert(ticker="MU", target_price=100, direction="below")
    fired = pa.evaluate_alerts({"MU": 95.0})
    assert len(fired) == 1
    assert fired[0]["ticker"] == "MU"
    assert fired[0]["current_price"] == 95.0


def test_evaluate_above_fires_when_price_rises():
    pa.add_alert(ticker="MU", target_price=100, direction="above")
    fired = pa.evaluate_alerts({"MU": 105.0})
    assert len(fired) == 1


def test_evaluate_below_does_not_fire_when_price_above_target():
    pa.add_alert(ticker="MU", target_price=100, direction="below")
    fired = pa.evaluate_alerts({"MU": 105.0})
    assert fired == []


def test_evaluate_persists_fire_count_and_fired_price():
    pa.add_alert(ticker="MU", target_price=100, direction="below")
    pa.evaluate_alerts({"MU": 90.0})
    items = pa.list_alerts()
    assert items[0]["fire_count"] == 1
    assert items[0]["fired_price"] == 90.0
    assert items[0]["last_fired_at"] is not None


def test_evaluate_respects_cooldown():
    """Une 2e éval dans les 24h ne refire pas."""
    pa.add_alert(ticker="MU", target_price=100, direction="below")
    first = pa.evaluate_alerts({"MU": 90.0})
    second = pa.evaluate_alerts({"MU": 89.0})
    assert len(first) == 1 and second == []


def test_evaluate_skips_expired():
    a = pa.add_alert(ticker="MU", target_price=100, direction="below")
    items = pa._read_all_unlocked()
    items[0]["expires_at"] = (pa._now_utc() - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    pa._write_all_unlocked(items)
    fired = pa.evaluate_alerts({"MU": 90.0})
    assert fired == []
    _ = a


def test_evaluate_empty_input():
    pa.add_alert(ticker="MU", target_price=100)
    assert pa.evaluate_alerts({}) == []


def test_evaluate_ignores_non_numeric_prices():
    pa.add_alert(ticker="MU", target_price=100, direction="below")
    fired = pa.evaluate_alerts({"MU": "garbage"})  # type: ignore[dict-item]
    assert fired == []


# ─────────────────────────────────────────────────────────────────
# compute_stats
# ─────────────────────────────────────────────────────────────────

def test_compute_stats_empty():
    s = pa.compute_stats()
    assert s["n_total"] == 0
    assert s["hit_rate_pct"] is None


def test_compute_stats_hit_rate():
    pa.add_alert(ticker="MU",   target_price=100, direction="below", created_price=110.0)
    pa.add_alert(ticker="AAPL", target_price=200, direction="below")
    pa.evaluate_alerts({"MU": 95.0})
    s = pa.compute_stats()
    assert s["n_total"] == 2
    assert s["n_fired"] == 1
    assert s["n_active"] == 1
    assert s["hit_rate_pct"] == 50.0


def test_compute_stats_median_discount():
    """Discount = (target − created) / created. -8% sur 1 alerte fired."""
    pa.add_alert(ticker="MU", target_price=92.0, direction="below", created_price=100.0)
    pa.evaluate_alerts({"MU": 91.0})
    s = pa.compute_stats()
    assert s["median_discount_captured_pct"] == pytest.approx(-8.0, abs=0.1)


def test_compute_stats_n_expired_unfired():
    a = pa.add_alert(ticker="MU", target_price=100, direction="below")
    items = pa._read_all_unlocked()
    items[0]["expires_at"] = (pa._now_utc() - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    pa._write_all_unlocked(items)
    s = pa.compute_stats()
    assert s["n_total"] == 1
    assert s["n_expired_unfired"] == 1
    _ = a


# ─────────────────────────────────────────────────────────────────
# _is_expired edge cases
# ─────────────────────────────────────────────────────────────────

def test_is_expired_handles_no_expiry():
    now = datetime.now(UTC)
    assert pa._is_expired({"expires_at": None}, now) is False


def test_is_expired_handles_garbage():
    now = datetime.now(UTC)
    assert pa._is_expired({"expires_at": "not-a-date"}, now) is False
