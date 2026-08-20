"""Tests d'intégration — GET /api/my_portfolio (routers/my_portfolio.py).

Book personnel long terme, statique, hors moteur TITAN. Auth bypass via
ALLOW_UNAUTH=True pour isoler la logique de calcul (poids réel, dérive,
badge LNVGY).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import api
import routers.my_portfolio as my_portfolio_router
from modules import api_core


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    return TestClient(api.app)


def test_my_portfolio_happy_path_prices_available(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 10
    assert body["cash_reserve"]["amount"] == 200.0
    assert [w["ticker"] for w in body["watchlist"]] == ["SEZL"]
    # total = sum(shares * 100) + 200
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["current_value"] == round(2.338323 * 100, 2)
    assert bnp["price_stale"] is False


def test_my_portfolio_lnvgy_is_pending_with_badge_and_no_drift(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 50.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["is_pending"] is True
    assert lnvgy["is_deploying"] is True
    assert lnvgy["deployment_pct"] == 0.0
    assert lnvgy["current_value"] == 0.0
    assert lnvgy["drift_pct"] is None
    assert lnvgy["rebalance_alert"] is False
    assert lnvgy["badge"] == "⏳ En attente earnings 21/08"


def test_my_portfolio_partial_deployment_shows_progress_not_drift_alert(monkeypatch):
    # PSX cible $230. On force un prix tel que la valeur actuelle (shares ×
    # prix) ne représente qu'une fraction du montant cible (< seuil 70%) —
    # ça doit basculer en statut "en cours de déploiement", pas en alerte
    # de dérive, même si l'écart au poids cible dépasse largement ±25%.
    def _price(ticker):
        return 240.47 if ticker == "PSX" else 100.0  # ~$78 / $230 déployé (34%)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    psx = next(p for p in body["positions"] if p["ticker"] == "PSX")
    assert psx["is_deploying"] is True
    assert psx["rebalance_alert"] is False
    assert psx["drift_pct"] is None
    assert 30.0 < psx["deployment_pct"] < 40.0


def test_my_portfolio_fully_deployed_position_keeps_drift_alert(monkeypatch):
    # PSX pleinement déployé (valeur actuelle > 70% du montant cible) mais
    # avec un poids réel qui a dérivé au-delà de ±25% -> l'alerte de
    # rééquilibrage classique doit s'appliquer, pas la barre de déploiement.
    def _price(ticker):
        return 1000.0 if ticker == "PSX" else 100.0  # $324.4 / $230 = 141% déployé
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    psx = next(p for p in body["positions"] if p["ticker"] == "PSX")
    assert psx["deployment_pct"] >= 70.0
    assert psx["is_deploying"] is False
    assert psx["rebalance_alert"] is True
    assert psx["drift_pct"] is not None


def test_my_portfolio_price_fetch_failure_falls_back_to_target_amount(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: None)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_stale"] is True
    assert bnp["current_value"] == 300.0  # fallback target_amount


def test_my_portfolio_rebalance_alert_fires_beyond_threshold(monkeypatch):
    # BNP.PA cible 15% ($300 sur ~$2000). On force son prix très haut pour
    # que son poids réel s'envole et dépasse la dérive tolérée de ±25%.
    def _price(ticker):
        return 100_000.0 if ticker == "BNP.PA" else 1.0
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["rebalance_alert"] is True
    assert bnp["drift_pct"] > 25.0


def test_my_portfolio_real_weight_uses_fixed_2000_envelope_not_invested_sum(monkeypatch):
    """Régression bug 2026-08-20 : le poids réel divisait par la somme
    variable des valeurs actuellement investies au lieu de l'enveloppe fixe
    $2000. Ça gonflait artificiellement le poids réel de TOUTES les autres
    lignes dès qu'une position (PSX, LNVGY) était sous-déployée — MU
    affichait +25.7% de dérive au lieu de +3.5% réel.

    Ici on force PSX très sous-déployé (quasi $0 investi) pendant que
    toutes les autres lignes sont exactement à leur montant cible. Avec le
    bon dénominateur fixe, chaque ligne pleinement déployée doit afficher
    real_weight_pct == target_weight_pct exactement (donc drift == 0),
    peu importe l'état de PSX.
    """
    price_at_target = {
        p["ticker"]: p["target_amount"] / p["shares"]
        for p in [
            {"ticker": "BNP.PA", "target_amount": 300.0, "shares": 2.338323},
            {"ticker": "FMX", "target_amount": 280.0, "shares": 2.29508},
            {"ticker": "DRH", "target_amount": 220.0, "shares": 17.40506},
            {"ticker": "CNC", "target_amount": 220.0, "shares": 3.27527},
            {"ticker": "HRTG", "target_amount": 180.0, "shares": 5.31915},
            {"ticker": "MU", "target_amount": 90.0, "shares": 0.09659},
            {"ticker": "NUTX", "target_amount": 80.0, "shares": 0.41824},
            {"ticker": "ERO", "target_amount": 60.0, "shares": 1.76367},
        ]
    }

    def _price(ticker):
        if ticker == "PSX":
            return 1.0  # quasi rien investi : $0.32 sur $230 cible
        return price_at_target.get(ticker, 100.0)

    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    by_ticker = {p["ticker"]: p for p in body["positions"]}

    psx = by_ticker["PSX"]
    assert psx["is_deploying"] is True
    assert psx["real_weight_pct"] < 1.0

    mu = by_ticker["MU"]
    assert mu["real_weight_pct"] == mu["target_weight_pct"] == 4.5
    assert mu["drift_pct"] == 0.0
    assert mu["rebalance_alert"] is False

    for ticker in ("BNP.PA", "FMX", "DRH", "CNC", "HRTG", "NUTX", "ERO"):
        row = by_ticker[ticker]
        assert row["real_weight_pct"] == row["target_weight_pct"], ticker
        assert row["drift_pct"] == 0.0, ticker


def test_my_portfolio_pnl_computed_from_real_entry_price(monkeypatch):
    """P&L est un axe séparé du poids/dérive : (prix actuel - prix
    d'entrée réel) × actions, indépendant du montant/poids cible."""
    def _price(ticker):
        return {"BNP.PA": 120.64, "PSX": 200.0}.get(ticker, 100.0)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    by_ticker = {p["ticker"]: p for p in body["positions"]}

    # BNP.PA : entrée 110.64 -> prix 120.64 (+10$/action) -> gain
    bnp = by_ticker["BNP.PA"]
    assert bnp["pnl_usd"] == round((120.64 - 110.64) * 2.338323, 2)
    assert bnp["pnl_usd"] > 0
    assert bnp["pnl_pct"] > 0

    # PSX : entrée 246.64 -> prix 200.0 (perte) -> pnl négatif
    psx = by_ticker["PSX"]
    assert psx["pnl_usd"] == round((200.0 - 246.64) * 0.32436, 2)
    assert psx["pnl_usd"] < 0
    assert psx["pnl_pct"] < 0


def test_my_portfolio_lnvgy_has_no_pnl_no_entry_price(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["entry_price"] is None
    assert lnvgy["pnl_usd"] is None
    assert lnvgy["pnl_pct"] is None


def test_my_portfolio_pnl_none_when_price_fetch_fails(monkeypatch):
    # Prix live indisponible -> current_value fallback sur target_amount,
    # mais le P&L ne doit JAMAIS utiliser ce fallback (pas un vrai prix).
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: None)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_stale"] is True
    assert bnp["pnl_usd"] is None
    assert bnp["pnl_pct"] is None


def test_my_portfolio_total_pnl_usd_sums_open_positions_only(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    expected = round(
        sum(p["pnl_usd"] for p in body["positions"] if p["pnl_usd"] is not None), 2
    )
    assert body["total_pnl_usd"] == expected
    # LNVGY (pas de prix d'entrée) ne doit pas contribuer.
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["pnl_usd"] is None


def test_my_portfolio_requires_auth_when_configured(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", lambda t: 100.0)
    client = TestClient(api.app)
    r = client.get("/api/my_portfolio")
    assert r.status_code == 401
