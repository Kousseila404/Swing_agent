"""Cache cross-universe sector_metrics (P2+P3) — invalidation au mtime."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from modules import sector_metrics


def _write_universe(path: Path, tickers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "updated_at": "2026-04-20T10:00:00Z",
        "tickers":    tickers,
    }))


def _fake_tickers(n: int = 10) -> dict:
    return {
        f"T{i}": {
            "ticker":              f"T{i}",
            "sector":              "Technology" if i % 2 == 0 else "Healthcare",
            "market_cap":          (i + 1) * 1e10,
            "forward_pe":          10 + i * 2,
            "return_on_equity":    0.15 + i * 0.01,
            "operating_margin":    0.20,
            "ev_to_ebitda":        15,
            "free_cash_flow":      1e9,
            "debt_to_equity":      0.5,
            "current_ratio":       1.2,
            "recommendation_mean": 2.0,
            "price_target_mean":   150,
            "current_price":       120,
        }
        for i in range(n)
    }


def test_scored_universe_is_cached(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    _write_universe(path, _fake_tickers(5))
    monkeypatch.setattr(sector_metrics, "_UNIVERSE_PATH", path)
    sector_metrics._clear_universe_cache()

    real_score = sector_metrics._score_universe
    calls = {"n": 0}

    def counting_score(tickers):
        calls["n"] += 1
        return real_score(tickers)

    with patch.object(sector_metrics, "_score_universe", side_effect=counting_score):
        u1, s1 = sector_metrics._get_universe_and_scored()
        u2, s2 = sector_metrics._get_universe_and_scored()
        u3, s3 = sector_metrics._get_universe_and_scored()

    assert calls["n"] == 1, "le scoring doit être calculé une seule fois"
    assert s1 is s2 is s3, "même objet caché retourné"
    assert len(s1) == 5


def test_cache_invalidates_on_mtime_change(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    _write_universe(path, _fake_tickers(3))
    monkeypatch.setattr(sector_metrics, "_UNIVERSE_PATH", path)
    sector_metrics._clear_universe_cache()

    _, s1 = sector_metrics._get_universe_and_scored()

    # Réécrit avec un contenu différent + nouveau mtime
    time.sleep(0.01)
    _write_universe(path, _fake_tickers(8))
    # Force mtime advance
    new_mtime = time.time() + 1
    os.utime(path, (new_mtime, new_mtime))

    _, s2 = sector_metrics._get_universe_and_scored()
    assert len(s1) == 3
    assert len(s2) == 8
    assert s1 is not s2


def test_compute_sector_detail_reuses_cache(monkeypatch, tmp_path):
    """compute_all + compute_sector_detail = 1 seul scoring cross-universe."""
    path = tmp_path / "universe.json"
    _write_universe(path, _fake_tickers(6))
    monkeypatch.setattr(sector_metrics, "_UNIVERSE_PATH", path)
    sector_metrics._clear_universe_cache()

    # Pas besoin de vrai momentum (yfinance) : mock pour le test.
    monkeypatch.setattr(
        sector_metrics,
        "get_momentum_6m",
        lambda force_refresh=False: ({}, "2026-04-20T10:00:00Z", True),
    )

    real_score = sector_metrics._score_universe
    calls = {"n": 0}

    def counting_score(tickers):
        calls["n"] += 1
        return real_score(tickers)

    with patch.object(sector_metrics, "_score_universe", side_effect=counting_score):
        payload = sector_metrics.compute_all()
        detail = sector_metrics.compute_sector_detail("Technology")

    assert calls["n"] == 1, (
        "compute_all + compute_sector_detail doivent partager "
        "le même scoring cross-universe en cache"
    )
    assert payload["sectors"]
    assert detail is not None
    assert detail["sector"] == "Technology"
    assert len(detail["tickers"]) > 0


def test_clear_cache_forces_recompute(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    _write_universe(path, _fake_tickers(4))
    monkeypatch.setattr(sector_metrics, "_UNIVERSE_PATH", path)
    sector_metrics._clear_universe_cache()

    real_score = sector_metrics._score_universe
    calls = {"n": 0}

    def counting_score(tickers):
        calls["n"] += 1
        return real_score(tickers)

    with patch.object(sector_metrics, "_score_universe", side_effect=counting_score):
        sector_metrics._get_universe_and_scored()
        sector_metrics._clear_universe_cache()
        sector_metrics._get_universe_and_scored()

    assert calls["n"] == 2
