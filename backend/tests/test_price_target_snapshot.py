"""Tests snapshot quotidien prix cible — write/read isolés via tmp_path
(HISTORY_DIR/LATEST_PATH/CALIBRATION_STATE_PATH monkey-patchés).
"""
from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from modules import price_target_snapshot as pts


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    history_dir = tmp_path / ".price_target_history"
    latest = history_dir / "latest.json"
    calib_state = tmp_path / ".price_target_calibration" / "state.json"
    monkeypatch.setattr(pts, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(pts, "LATEST_PATH", latest)
    monkeypatch.setattr(pts, "CALIBRATION_STATE_PATH", calib_state)
    return {"history_dir": history_dir, "latest": latest, "calib_state": calib_state}


def _mk_scored(n: int = 15) -> dict:
    return {
        f"T{i}": {
            "ticker": f"T{i}",
            "sector": "Technology",
            "current_price": 100.0 + i,
            "forward_pe": 15.0 + (i % 5),
            "ev_to_ebitda": 10.0 + (i % 3),
            "peg_ratio": 1.2,
            "quality_score": 60.0,
            "value_score": 55.0,
            "piotroski_score": 6,
            "f_score": 6,
        }
        for i in range(n)
    }


# ─────────────────────────────────────────────────────────────────
# load_calibrated_weights
# ─────────────────────────────────────────────────────────────────

def test_load_calibrated_weights_fallback_when_absent(isolated_paths):
    weights, band_pct = pts.load_calibrated_weights()
    assert weights == pts.DEFAULT_WEIGHTS
    assert band_pct == pts.DEFAULT_BAND_PCT


def test_load_calibrated_weights_reads_best_weights(isolated_paths):
    calib_state = isolated_paths["calib_state"]
    calib_state.parent.mkdir(parents=True)
    calib_state.write_text(json.dumps({
        "best_weights": {
            "w_multiple": 0.5638, "w_peg": 0.0, "w_buffett": 0.4362, "band_pct": 0.15,
        },
    }))
    weights, band_pct = pts.load_calibrated_weights()
    assert weights == {
        "w_multiple": 0.5638, "w_peg": 0.0, "w_buffett": 0.4362,
        "w_analyst": pts.DEFAULT_WEIGHTS["w_analyst"],  # absent du state.json fixture → fallback
    }
    assert band_pct == 0.15


def test_load_calibrated_weights_fallback_on_corrupt_state(isolated_paths):
    calib_state = isolated_paths["calib_state"]
    calib_state.parent.mkdir(parents=True)
    calib_state.write_text("{not json")
    weights, band_pct = pts.load_calibrated_weights()
    assert weights == pts.DEFAULT_WEIGHTS
    assert band_pct == pts.DEFAULT_BAND_PCT


# ─────────────────────────────────────────────────────────────────
# compute_and_persist / load_latest
# ─────────────────────────────────────────────────────────────────

def test_compute_and_persist_writes_dated_snapshot_and_latest(isolated_paths):
    summary = pts.compute_and_persist(_mk_scored(), snapshot_date=date(2026, 8, 10))

    assert summary["n_tickers"] == 15
    assert summary["n_with_target"] > 0

    dated = isolated_paths["history_dir"] / "price_targets_20260810.json.gz"
    assert dated.exists()
    payload = json.loads(gzip.decompress(dated.read_bytes()))
    assert payload["snapshot_date"] == "2026-08-10"
    assert payload["horizon_months"] == 12
    assert "T0" in payload["tickers"]
    assert payload["tickers"]["T0"]["current_price"] == 100.0

    assert isolated_paths["latest"].exists()


def test_load_latest_matches_last_compute(isolated_paths):
    pts.compute_and_persist(_mk_scored(), snapshot_date=date(2026, 8, 10))
    latest = pts.load_latest()
    assert set(latest.keys()) == {f"T{i}" for i in range(15)}
    assert latest["T0"]["method"] in ("fundamental_blend", "unavailable")


def test_get_latest_for_ticker(isolated_paths):
    pts.compute_and_persist(_mk_scored(), snapshot_date=date(2026, 8, 10))
    rec = pts.get_latest_for_ticker("T0")
    assert rec.get("horizon_months") == 12
    assert pts.get_latest_for_ticker("UNKNOWN") == {}


def test_load_latest_empty_when_absent(isolated_paths):
    assert pts.load_latest() == {}


def test_compute_and_persist_reruns_same_day_overwrite(isolated_paths):
    pts.compute_and_persist(_mk_scored(5), snapshot_date=date(2026, 8, 10))
    files_before = list(isolated_paths["history_dir"].glob("price_targets_*.json.gz"))
    pts.compute_and_persist(_mk_scored(5), snapshot_date=date(2026, 8, 10))
    files_after = list(isolated_paths["history_dir"].glob("price_targets_*.json.gz"))
    assert len(files_before) == len(files_after) == 1
