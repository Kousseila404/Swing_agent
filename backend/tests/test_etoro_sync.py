"""Synchro eToro (lecture seule) — normalisation, persistance, vue argent réel."""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from modules import api_core, etoro_sync

SUMMARY = {
    "accountCurrency": "USD",
    "timestamp": "2026-11-20T21:51:48Z",
    "totals": {"totalValue": 1000.0, "availableCash": 100.0, "unrealizedPnl": 12.0},
    "holdings": [
        {"market": {"instrumentId": 6689, "symbol": "EQT", "name": "EQT"}, "invested": 250.0,
         "value": 200.0, "pnl": -50.0, "pnlPercent": -20.0, "units": 4.159733},
        {"market": {"instrumentId": 2312, "symbol": "0992.HK", "name": "Lenovo"}, "invested": 140.0,
         "value": 100.0, "pnl": -40.0, "pnlPercent": -28.6, "units": 37.094844},
        {"market": {"instrumentId": 1, "symbol": "SAGE CVR"}, "invested": 0.0, "value": 0.01,
         "pnl": 0.01, "units": 0.4},
    ],
    "page": {"complete": True},
    "copiedTraders": [
        {"mirrorId": 1, "username": "alice", "invested": 500.0, "value": 600.0, "openPnl": 50.0,
         "closedPnl": 30.0, "availableCash": 5.0, "stopLossPercentage": 50.0},
    ],
}


def test_normalize_summary_split_and_weights():
    snap = etoro_sync.normalize_summary(SUMMARY)
    assert snap["totals"]["total_value"] == 1000.0
    assert snap["split"]["copy_value"] == 600.0
    assert snap["split"]["copy_pct"] == 60.0
    assert snap["split"]["cash_pct"] == 10.0
    assert snap["direct"][0]["symbol"] == "EQT"          # trié par valeur
    assert snap["copy"][0]["open_pnl_pct"] == 10.0


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("totals"),
    lambda d: d["totals"].pop("totalValue"),
    lambda d: d.update(holdings="x"),
    lambda d: d["page"].update(complete=False),
])
def test_normalize_summary_rejects_incomplete(mutate):
    d = copy.deepcopy(SUMMARY)
    mutate(d)
    with pytest.raises(ValueError):
        etoro_sync.normalize_summary(d)


def test_normalize_public_api_aggregates_dca_and_mirrors():
    payload = {"clientPortfolio": {
        "credit": 100.0, "unrealizedPnL": 5.0,
        "positions": [
            {"instrumentID": 1483, "amount": 100.0, "units": 1.0, "openRate": 100.0, "leverage": 1,
             "mirrorID": 0, "unrealizedPnL": {"pnL": 10.0, "closeRate": 110.0}},
            {"instrumentID": 1483, "amount": 120.0, "units": 1.0, "openRate": 120.0, "leverage": 1,
             "mirrorID": 0, "unrealizedPnL": {"pnL": -10.0, "closeRate": 110.0}},
            {"instrumentID": 999, "amount": 50.0, "units": 1.0, "mirrorID": 7,
             "unrealizedPnL": {"pnL": 5.0}},  # position copiée → ignorée en direct
        ],
        "mirrors": [{
            "mirrorID": 7, "parentUsername": "bob", "availableAmount": 20.0,
            "initialInvestment": 60.0, "depositSummary": 10.0, "withdrawalSummary": 0.0,
            "closedPositionsNetProfit": 3.0, "stopLossPercentage": 10.0,
            "positions": [{"instrumentID": 999, "amount": 50.0, "unrealizedPnL": {"pnL": 5.0}}],
        }],
    }}
    snap = etoro_sync.normalize_public_api(payload, {1483: {"symbolFull": "PSX"}})
    assert len(snap["direct"]) == 1
    psx = snap["direct"][0]
    assert psx["symbol"] == "PSX" and psx["units"] == 2.0 and psx["value"] == 220.0
    assert psx["avg_open_rate"] == 110.0
    bob = snap["copy"][0]
    assert bob["value"] == 75.0 and bob["invested"] == 70.0 and bob["open_pnl"] == 5.0
    assert snap["totals"]["total_value"] == 395.0  # 100 cash + 220 + 75


def test_save_snapshot_one_history_row_per_day():
    snap = etoro_sync.normalize_summary(SUMMARY)
    etoro_sync.save_snapshot(snap)
    etoro_sync.save_snapshot({**snap, "totals": {**snap["totals"], "total_value": 1100.0}})
    hist = etoro_sync.load_history()
    assert len(hist) == 1 and hist[0]["total_value"] == 1100.0
    assert hist[0]["copy_by_trader"] == {"alice": 600.0}
    assert etoro_sync.load_latest()["totals"]["total_value"] == 1100.0


def test_overview_without_snapshot():
    o = etoro_sync.build_overview()
    assert o["available"] is False and o["api_configured"] is False


def test_overview_stale_flag():
    snap = etoro_sync.normalize_summary(SUMMARY)
    snap["as_of"] = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    etoro_sync.save_snapshot(snap)
    assert etoro_sync.build_overview()["stale"] is True


def test_actions_from_fired_thesis_signal_and_wide_copy_stop(monkeypatch):
    import modules.my_portfolio_thesis as th
    monkeypatch.setattr(th, "get_all_theses", lambda: {
        "EQT": {"sell_signals": [{"libelle": "TITAN < 60", "statut": "declenche"},
                                 {"libelle": "gaz", "statut": "a_surveiller"}]},
        "LNVGY": {"sell_signals": [{"libelle": "x", "statut": "a_surveiller"}]},
    })
    acts = etoro_sync.actions_today(etoro_sync.normalize_summary(SUMMARY))
    assert [a["ticker"] for a in acts] == ["EQT", "copy:alice"]
    assert acts[0]["reasons"] == ["TITAN < 60"] and acts[0]["watch_count"] == 1


def test_reconcile_maps_price_ticker_and_flags_gaps(monkeypatch):
    import modules.my_portfolio_data as book
    monkeypatch.setattr(book, "POSITIONS", [
        {"ticker": "EQT", "shares": 4.0},                      # écart d'unités
        {"ticker": "LNVGY", "price_ticker": "0992.HK", "shares": 37.09484},  # OK via price_ticker
        {"ticker": "PSX", "shares": 1.0},                      # absent d'eToro
    ])
    issues = {(i["symbol"], i["kind"]) for i in
              etoro_sync.reconcile_with_book(etoro_sync.normalize_summary(SUMMARY))}
    assert issues == {("EQT", "units_mismatch"), ("PSX", "missing_on_etoro")}  # CVR < 5 $ ignoré


def _client(monkeypatch) -> TestClient:
    import api
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    return TestClient(api.app)


def test_router_ingest_then_overview(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/etoro/snapshot", json=SUMMARY)
    assert r.status_code == 200 and r.json()["split"]["copy_pct"] == 60.0
    o = c.get("/api/etoro/overview").json()
    assert o["available"] is True and o["snapshot"]["source"] == "connector"


def test_router_ingest_invalid_422(monkeypatch):
    assert _client(monkeypatch).post("/api/etoro/snapshot", json={"foo": 1}).status_code == 422


def test_router_sync_without_keys_409(monkeypatch):
    assert _client(monkeypatch).post("/api/etoro/sync").status_code == 409


def test_cli_without_keys_is_noop(monkeypatch):
    # main() importe config → load_dotenv : forcer « non configuré » pour ne
    # jamais appeler l'API réelle même si les clés sont dans backend/.env.
    monkeypatch.setattr(etoro_sync, "api_configured", lambda: False)
    assert etoro_sync.main([]) == 0
    assert etoro_sync.load_latest() is None
