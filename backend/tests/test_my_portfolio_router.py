"""Tests d'intégration — GET /api/my_portfolio (routers/my_portfolio.py).

Book personnel long terme, statique, hors moteur TITAN. Auth bypass via
ALLOW_UNAUTH=True pour isoler la logique de calcul (poids réel, dérive,
badge LNVGY).

`_safe_price` retourne désormais un tuple (prix_usd, price_as_of) et prend
la position (dict) en argument, pas juste le ticker — nécessaire pour
résoudre price_ticker/currency/shares_per_adr par ligne (BNP.PA EUR,
LNVGY via 0992.HK). Les tests ci-dessous monkeypatchent `_safe_price`
directement pour rester agnostiques à cette résolution ; la conversion FX
elle-même (`_usd_multiplier`/`get_fx_rate`) est testée séparément plus bas.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import api
import routers.my_portfolio as my_portfolio_router
from modules import api_core
from modules import my_portfolio_earnings as earnings_mod
from modules import my_portfolio_thesis as thesis_mod
from modules import portfolio_risk as risk_mod


@pytest.fixture(autouse=True)
def _isolate_thesis_store(tmp_path, monkeypatch):
    # Jamais lire/écrire le vrai data/my_portfolio_thesis.json de prod
    # pendant les tests — même précaution que le risk state ci-dessous.
    monkeypatch.setattr(thesis_mod, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(thesis_mod, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_core, "API_TOKEN", "")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", True)
    # Neutralise la conversion FX par défaut (identité) — le routeur
    # l'applique aussi à `entry_price` indépendamment de _safe_price (mocké
    # dans presque tous les tests ci-dessous), donc sans ce défaut chaque
    # test toucherait le réseau (get_fx_rate -> yfinance) pour BNP.PA (EUR).
    # La conversion FX elle-même est testée isolément plus bas.
    monkeypatch.setattr(my_portfolio_router, "_usd_multiplier", lambda c: 1.0)
    return TestClient(api.app)


def _flat_price(value, as_of=None):
    """Fabrique un _safe_price qui retourne le même prix pour toutes les lignes.

    `_safe_price` retourne un 4-tuple (price_usd, as_of, raw_native_price, fx)
    depuis Upgrade 3 (rebalance_order) — ici raw_native_price=value et fx=1.0
    par défaut (les lignes non-USD dont la conversion FX importe sont testées
    séparément plus bas, via `_safe_price` non mocké)."""
    if value is None:
        return lambda p: (None, None, None, None)
    return lambda p: (value, as_of, value, 1.0)


def test_my_portfolio_happy_path_prices_available(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
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


def test_my_portfolio_lnvgy_position_opened_no_longer_pending(monkeypatch):
    """Régression 2026-08-21 : LNVGY était en attente (shares=0, badge
    earnings) jusqu'à l'ouverture réelle le 21/08 (37.094844 actions
    ordinaires 0992.HK, relevé eToro) — is_pending doit repasser à False et
    le badge "en attente" ne doit plus s'afficher (position ouverte)."""
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(140.0 / 37.094844))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["is_pending"] is False
    assert lnvgy["current_value"] == round(37.094844 * (140.0 / 37.094844), 2)
    assert lnvgy.get("badge") is None


def test_my_portfolio_partial_deployment_shows_progress_not_drift_alert(monkeypatch):
    # PSX cible $230. On force un prix tel que la valeur actuelle (shares ×
    # prix) ne représente qu'une fraction du montant cible (< seuil 70%) —
    # ça doit basculer en statut "en cours de déploiement", pas en alerte
    # de dérive, même si l'écart au poids cible dépasse largement ±25%.
    def _price(p):
        v = 240.47 if p["ticker"] == "PSX" else 100.0
        return (v, None, v, 1.0)  # ~$78 / $230 déployé (34%)
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
    def _price(p):
        v = 1000.0 if p["ticker"] == "PSX" else 100.0
        return (v, None, v, 1.0)  # $324.4 / $230 = 141% déployé
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    psx = next(p for p in body["positions"] if p["ticker"] == "PSX")
    assert psx["deployment_pct"] >= 70.0
    assert psx["is_deploying"] is False
    assert psx["rebalance_alert"] is True
    assert psx["drift_pct"] is not None


def test_my_portfolio_price_fetch_failure_falls_back_to_target_amount(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(None))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_stale"] is True
    assert bnp["current_value"] == 300.0  # fallback target_amount


def test_my_portfolio_rebalance_alert_fires_beyond_threshold(monkeypatch):
    # BNP.PA cible 15% ($300 sur ~$2000). On force son prix très haut pour
    # que son poids réel s'envole et dépasse la dérive tolérée de ±25%.
    def _price(p):
        v = 100_000.0 if p["ticker"] == "BNP.PA" else 1.0
        return (v, None, v, 1.0)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["rebalance_alert"] is True
    assert bnp["drift_pct"] > 25.0

    # Upgrade 3 — vérification live (TestClient) : une dérive >25% simulée
    # produit un rebalance_order cohérent (SELL, puisque la ligne est très
    # au-dessus de sa cible $300).
    order = bnp["rebalance_order"]
    assert order is not None
    assert order["direction"] == "SELL"
    assert order["currency"] == "EUR"
    delta_usd = 300.0 - bnp["current_value"]
    assert order["amount_usd"] == round(delta_usd, 2)
    assert order["amount_native"] == round(delta_usd, 2)  # fx mocké à 1.0 ici
    assert order["shares_native"] > 0

    # Aucune ligne cash_reserve/watchlist n'a de rebalance_order (pas
    # d'instrument tradable cible pour le cash, watchlist = 0% cible).
    assert "rebalance_order" not in body["cash_reserve"]
    assert all("rebalance_order" not in w for w in body["watchlist"])


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

    def _price(p):
        ticker = p["ticker"]
        if ticker == "PSX":
            return (1.0, None, 1.0, 1.0)  # quasi rien investi : $0.32 sur $230 cible
        v = price_at_target.get(ticker, 100.0)
        return (v, None, v, 1.0)

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
    d'entrée réel) × actions, indépendant du montant/poids cible.

    BNP.PA étant coté EUR, la conversion FX est neutralisée (taux 1.0) par
    `_client()` pour tester la formule P&L elle-même indépendamment du taux
    de change — voir la section FX plus bas pour la conversion bout en bout.
    """
    def _price(p):
        v = {"BNP.PA": 120.64, "PSX": 200.0}.get(p["ticker"], 100.0)
        return (v, None, v, 1.0)
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


def test_my_portfolio_lnvgy_pnl_uses_hkd_entry_price_converted_to_usd(monkeypatch):
    """Régression 2026-08-21 : entry_price LNVGY = 29.58 HKD (exécution
    21/08/2026, relevé eToro) — doit être reconverti en USD au même taux
    live que current_price avant le calcul du P&L (sinon on compare un
    prix USD à un prix HKD, résultat non sensique)."""
    client = _client(monkeypatch)
    # _client() neutralise _usd_multiplier par défaut (voir sa docstring) —
    # on l'écrase après coup pour ce test-ci, qui veut justement vérifier la
    # conversion HKD -> USD sur entry_price.
    monkeypatch.setattr(my_portfolio_router, "_usd_multiplier", lambda c: 1.0 / 7.8)  # HKD -> USD
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(4.0))  # prix courant $4.00
    r = client.get("/api/my_portfolio")
    body = r.json()
    lnvgy = next(p for p in body["positions"] if p["ticker"] == "LNVGY")
    entry_price_usd = 29.58 / 7.8
    assert lnvgy["entry_price"] == 29.58  # champ brut HKD non modifié dans POSITIONS
    assert lnvgy["pnl_usd"] == round((4.0 - entry_price_usd) * 37.094844, 2)
    assert lnvgy["pnl_pct"] == round((4.0 / entry_price_usd - 1) * 100, 1)


def test_my_portfolio_pnl_none_when_price_fetch_fails(monkeypatch):
    # Prix live indisponible -> current_value fallback sur target_amount,
    # mais le P&L ne doit JAMAIS utiliser ce fallback (pas un vrai prix).
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(None))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_stale"] is True
    assert bnp["pnl_usd"] is None
    assert bnp["pnl_pct"] is None


def test_my_portfolio_total_pnl_usd_sums_positions_with_known_entry_price(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    expected = round(
        sum(p["pnl_usd"] for p in body["positions"] if p["pnl_usd"] is not None), 2
    )
    assert body["total_pnl_usd"] == expected
    # Toutes les lignes ont désormais un entry_price connu (LNVGY inclus
    # depuis le 21/08/2026) -> aucune ne doit avoir un pnl_usd None ici.
    assert all(p["pnl_usd"] is not None for p in body["positions"])


# ─────────────────────────────────────────────────────────────────
# Thèse structurée — fusion dans /api/my_portfolio (modules/my_portfolio_thesis.py)
# ─────────────────────────────────────────────────────────────────

def test_my_portfolio_thesis_fields_default_empty_skeleton(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["why_bought"] == {"catalyseurs": [], "valorisation": None, "role_portefeuille": None}
    assert bnp["sell_signals"] == []
    assert bnp["verification"] == {"derniere_verification": None, "verdict": None, "historique_verifications": []}
    assert bnp["thesis_updated_at"] is None
    # Les anciens champs texte libres n'existent plus sur POSITIONS.
    assert "reason" not in bnp
    assert "sell_signal" not in bnp


def test_my_portfolio_thesis_fields_merged_when_edited(monkeypatch):
    thesis_mod.update_thesis("BNP.PA", why_bought={"catalyseurs": ["Stabilisateur du book"]})
    thesis_mod.update_thesis("BNP.PA", sell_signals=[{"libelle": "Corrélation > 0.40", "statut": "a_surveiller"}])
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    r = _client(monkeypatch).get("/api/my_portfolio")
    bnp = next(p for p in r.json()["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["why_bought"]["catalyseurs"] == ["Stabilisateur du book"]
    assert bnp["sell_signals"][0]["libelle"] == "Corrélation > 0.40"


def test_my_portfolio_requires_auth_when_configured(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    client = TestClient(api.app)
    r = client.get("/api/my_portfolio")
    assert r.status_code == 401


def test_my_portfolio_price_as_of_surfaced_per_row(monkeypatch):
    def _price(p):
        return (100.0, "2026-08-21T14:30:00+00:00", 100.0, 1.0)
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _price)
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["price_as_of"] == "2026-08-21T14:30:00+00:00"


# ─────────────────────────────────────────────────────────────────
# Générateur d'ordres de rééquilibrage (Upgrade 3) — _rebalance_order
# ─────────────────────────────────────────────────────────────────

def _row(**overrides):
    row = {
        "rebalance_alert": True, "price_stale": False,
        "target_amount": 300.0, "current_value": 100.0, "currency": "USD",
    }
    row.update(overrides)
    return row


def test_rebalance_order_null_when_no_alert():
    assert my_portfolio_router._rebalance_order(_row(rebalance_alert=False), 50.0, 1.0) is None


def test_rebalance_order_null_when_price_stale():
    assert my_portfolio_router._rebalance_order(_row(price_stale=True), 50.0, 1.0) is None


def test_rebalance_order_null_when_raw_native_price_unavailable():
    assert my_portfolio_router._rebalance_order(_row(), None, 1.0) is None


def test_rebalance_order_null_when_fx_unavailable():
    assert my_portfolio_router._rebalance_order(_row(), 50.0, None) is None


def test_rebalance_order_buy_direction_usd():
    # target 300, valeur actuelle 100 -> delta +200 -> BUY.
    order = my_portfolio_router._rebalance_order(_row(), raw_native_price=50.0, fx=1.0)
    assert order == {
        "direction": "BUY", "shares_native": 4.0, "amount_native": 200.0,
        "currency": "USD", "amount_usd": 200.0,
    }


def test_rebalance_order_sell_direction_native_currency():
    # target 100, valeur actuelle 300 -> delta -200 -> SELL, exprimé en EUR.
    row = _row(target_amount=100.0, current_value=300.0, currency="EUR")
    order = my_portfolio_router._rebalance_order(row, raw_native_price=90.0, fx=1.1)
    assert order["direction"] == "SELL"
    assert order["currency"] == "EUR"
    assert order["amount_usd"] == -200.0
    assert order["amount_native"] == round(-200.0 / 1.1, 2)
    assert order["shares_native"] == round(abs((-200.0 / 1.1) / 90.0), 6)
    assert order["shares_native"] > 0  # toujours une magnitude positive


# ─────────────────────────────────────────────────────────────────
# FX — conversion EUR/HKD → USD (bug BNP.PA + mapping LNVGY→0992.HK)
# ─────────────────────────────────────────────────────────────────

def test_usd_multiplier_is_identity_for_usd():
    assert my_portfolio_router._usd_multiplier("USD") == 1.0


def test_usd_multiplier_eur_multiplies_direct_quote(monkeypatch):
    # EURUSD=X cote directement en USD par EUR (ex: 1.1677).
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: 1.1677 if pair == "EURUSD=X" else None)
    assert my_portfolio_router._usd_multiplier("EUR") == 1.1677


def test_usd_multiplier_hkd_inverts_usdhkd_quote(monkeypatch):
    # USDHKD=X cote en HKD par USD -> il faut inverser pour obtenir un
    # multiplicateur HKD -> USD.
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: 7.8 if pair == "USDHKD=X" else None)
    assert my_portfolio_router._usd_multiplier("HKD") == 1.0 / 7.8


def test_usd_multiplier_returns_none_when_fx_unavailable(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: None)
    assert my_portfolio_router._usd_multiplier("EUR") is None


def test_safe_price_bnp_pa_converts_eur_to_usd(monkeypatch):
    # Bug réel : prix BNP.PA fetché en EUR (107.22) mais jamais converti,
    # donnant $250.81 au lieu des ~$292.76 réels. Vérifie la conversion.
    monkeypatch.setattr(
        my_portfolio_router, "get_current_price_detailed",
        lambda ticker, use_alpaca=True: (107.22, "2026-08-21T15:00:00+00:00", "2026-08-21T15:00:05+00:00")
    )
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: 1.1677 if pair == "EURUSD=X" else None)
    bnp = next(p for p in my_portfolio_router.POSITIONS if p["ticker"] == "BNP.PA")
    price_usd, as_of, raw_price, fx = my_portfolio_router._safe_price(bnp)
    assert price_usd == 107.22 * 1.1677
    assert as_of == "2026-08-21T15:00:00+00:00"
    assert raw_price == 107.22
    assert fx == 1.1677


def test_safe_price_bnp_pa_returns_none_when_fx_unavailable(monkeypatch):
    monkeypatch.setattr(
        my_portfolio_router, "get_current_price_detailed",
        lambda ticker, use_alpaca=True: (107.22, "2026-08-21T15:00:00+00:00", "2026-08-21T15:00:05+00:00")
    )
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: None)
    bnp = next(p for p in my_portfolio_router.POSITIONS if p["ticker"] == "BNP.PA")
    price_usd, as_of, raw_price, fx = my_portfolio_router._safe_price(bnp)
    assert price_usd is None
    assert raw_price is None
    assert fx is None


def test_safe_price_lnvgy_queries_hk_alias_no_adr_multiplier(monkeypatch):
    # LNVGY (ADR US illiquide, prix bloqué à $0) -> doit interroger 0992.HK
    # (cotation primaire HKEX), qui est aussi l'instrument réellement détenu
    # (37.094844 actions ORDINAIRES 0992.HK, pas des unités ADR — voir
    # my_portfolio_data.py) -> pas de multiplicateur ADR, juste la conversion
    # de devise HKD -> USD.
    seen_tickers = []

    def _fake_price(ticker, use_alpaca=True):
        seen_tickers.append(ticker)
        return (100.0, "2026-08-21T08:00:00+00:00", "2026-08-21T08:00:05+00:00")  # 0992.HK en HKD

    monkeypatch.setattr(my_portfolio_router, "get_current_price_detailed", _fake_price)
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: 7.8 if pair == "USDHKD=X" else None)

    lnvgy = next(p for p in my_portfolio_router.POSITIONS if p["ticker"] == "LNVGY")
    price_usd, as_of, raw_price, fx = my_portfolio_router._safe_price(lnvgy)

    assert seen_tickers == ["0992.HK"]  # jamais "LNVGY" directement
    assert price_usd == 100.0 * (1.0 / 7.8)
    assert as_of == "2026-08-21T08:00:00+00:00"
    assert raw_price == 100.0
    assert fx == 1.0 / 7.8


def test_safe_price_applies_shares_per_adr_when_configured(monkeypatch):
    # Le mécanisme shares_per_adr reste valide pour une future ligne qui
    # détiendrait réellement des unités ADR plutôt que l'action ordinaire —
    # testé isolément ici puisque LNVGY (POSITIONS) ne l'utilise plus.
    monkeypatch.setattr(
        my_portfolio_router, "get_current_price_detailed",
        lambda ticker, use_alpaca=True: (100.0, "2026-08-21T08:00:00+00:00", "2026-08-21T08:00:05+00:00")
    )
    monkeypatch.setattr(my_portfolio_router, "get_fx_rate", lambda pair: 7.8 if pair == "USDHKD=X" else None)

    synthetic_adr_position = {
        "ticker": "FAKE_ADR", "price_ticker": "FAKE.HK",
        "currency": "HKD", "shares_per_adr": 20,
    }
    price_usd, _as_of, raw_price, fx = my_portfolio_router._safe_price(synthetic_adr_position)
    assert price_usd == 100.0 * 20 * (1.0 / 7.8)
    assert raw_price == 100.0
    assert fx == 1.0 / 7.8


def test_safe_price_bypasses_alpaca_iex_feed(monkeypatch):
    # Audit 2026-08-21 : Alpaca (plan gratuit, carnet IEX seul) dérive de
    # plusieurs % vs le NBBO consolidé sur les tickers peu liquides du book
    # (FMX/HRTG constatés à ±7 %). Ce book n'étant pas exécuté via Alpaca,
    # my_portfolio doit toujours forcer use_alpaca=False.
    captured = {}

    def _fake_price(ticker, use_alpaca=True):
        captured["use_alpaca"] = use_alpaca
        return (100.0, "2026-08-21T15:00:00+00:00", "2026-08-21T15:00:05+00:00")

    monkeypatch.setattr(my_portfolio_router, "get_current_price_detailed", _fake_price)
    fmx = next(p for p in my_portfolio_router.POSITIONS if p["ticker"] == "FMX")
    my_portfolio_router._safe_price(fmx)
    assert captured["use_alpaca"] is False


# ─────────────────────────────────────────────────────────────────
# Earnings dynamiques (Upgrade 2) — badge/next_earnings_date calculés côté
# router à partir du cache modules/my_portfolio_earnings, plus de champ
# `badge` statique dans POSITIONS.
# ─────────────────────────────────────────────────────────────────

def test_my_portfolio_earnings_fields_null_when_no_cache(monkeypatch):
    # Cache vide (défaut _client) : jamais de vrai appel réseau, tous les
    # champs earnings à None/False, pas de badge.
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["next_earnings_date"] is None
    assert bnp["earnings_days_until"] is None
    assert bnp["earnings_source"] is None
    assert bnp["earnings_data_stale"] is False
    assert bnp["badge"] is None


def test_my_portfolio_earnings_upcoming_shows_badge(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    upcoming = (date.today() + timedelta(days=10)).isoformat()

    def _fake_snapshot(symbols):
        return {s: {"next_earnings_date": upcoming, "source": "finnhub", "stale": False} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["next_earnings_date"] == upcoming
    assert bnp["earnings_days_until"] == 10
    assert bnp["earnings_source"] == "finnhub"
    assert bnp["badge"] == "📅 Earnings dans 10 j"


def test_my_portfolio_earnings_just_reported_transient_badge(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    reported = (date.today() - timedelta(days=2)).isoformat()

    def _fake_snapshot(symbols):
        return {s: {"next_earnings_date": reported, "source": "yfinance", "stale": False} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["earnings_days_until"] == -2
    assert bnp["badge"] == f"✅ Résultats publiés le {reported}"


def test_my_portfolio_earnings_outside_window_no_badge(monkeypatch):
    # Régression du bug initial (Upgrade 2) : un earnings lointain (76j) ne
    # doit jamais afficher de badge "en attente" figé indéfiniment.
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    far = (date.today() + timedelta(days=76)).isoformat()

    def _fake_snapshot(symbols):
        return {s: {"next_earnings_date": far, "source": "finnhub", "stale": False} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["earnings_days_until"] == 76
    assert bnp["badge"] is None

    # Aussi 6 jours après (hors fenêtre "résultats publiés" de 5j) -> plus de badge.
    just_past = (date.today() - timedelta(days=6)).isoformat()

    def _fake_snapshot_past(symbols):
        return {s: {"next_earnings_date": just_past, "source": "finnhub", "stale": False} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot_past)
    r2 = _client(monkeypatch).get("/api/my_portfolio")
    bnp2 = next(p for p in r2.json()["positions"] if p["ticker"] == "BNP.PA")
    assert bnp2["badge"] is None


def test_my_portfolio_earnings_data_stale_flag_surfaced(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))

    def _fake_snapshot(symbols):
        return {s: {"next_earnings_date": None, "source": None, "stale": True} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["earnings_data_stale"] is True
    assert bnp["badge"] is None  # pas de date connue malgré le staleness


def test_my_portfolio_lnvgy_earnings_resolved_via_price_ticker(monkeypatch):
    """LNVGY (ADR non couvert) doit interroger le cache sous 0992.HK (la
    cotation primaire réellement détenue), jamais sous 'LNVGY' directement —
    même convention que `_safe_price`/`price_ticker`."""
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    captured = {}

    def _fake_snapshot(symbols):
        captured["symbols"] = symbols
        return {"0992.HK": {"next_earnings_date": "2026-09-01", "source": "yfinance", "stale": False}}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    assert "0992.HK" in captured["symbols"]
    assert "LNVGY" not in captured["symbols"]
    assert "SEZL" in captured["symbols"]  # watchlist inclus dans le fetch

    lnvgy = next(p for p in r.json()["positions"] if p["ticker"] == "LNVGY")
    assert lnvgy["next_earnings_date"] == "2026-09-01"
    assert lnvgy["earnings_source"] == "yfinance"


def test_my_portfolio_watchlist_gets_earnings_fields(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))
    upcoming = (date.today() + timedelta(days=3)).isoformat()

    def _fake_snapshot(symbols):
        return {s: {"next_earnings_date": upcoming, "source": "finnhub", "stale": False} for s in symbols}
    monkeypatch.setattr(my_portfolio_router, "get_earnings_snapshot", _fake_snapshot)

    r = _client(monkeypatch).get("/api/my_portfolio")
    body = r.json()
    assert len(body["watchlist"]) == 1
    sezl = body["watchlist"][0]
    assert sezl["ticker"] == "SEZL"
    assert sezl["next_earnings_date"] == upcoming
    assert sezl["badge"] == "📅 Earnings dans 3 j"


def test_my_portfolio_earnings_live_roundtrip_via_disk_cache(monkeypatch, tmp_path):
    """Vérification live (Upgrade 2, définition du projet — voir
    docs/UPGRADE_PROGRESS.md) : TestClient(api.app) bout-en-bout SANS mock
    de `get_earnings_snapshot` — le cache disque est réellement écrit puis
    relu par le router, prouvant que le pipeline complet fonctionne (pas
    seulement la logique unitaire isolée)."""
    monkeypatch.setattr(earnings_mod, "_CACHE_PATH", tmp_path / ".my_portfolio_earnings_cache.json")
    upcoming = (date.today() + timedelta(days=5)).isoformat()
    earnings_mod._save_cache({
        "BNP.PA": {"next_earnings_date": upcoming, "source": "finnhub", "fetched_at": time.time()},
    })
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))

    r = _client(monkeypatch).get("/api/my_portfolio")
    assert r.status_code == 200
    body = r.json()
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["next_earnings_date"] == upcoming
    assert bnp["earnings_days_until"] == 5
    assert bnp["earnings_source"] == "finnhub"
    assert bnp["badge"] == "📅 Earnings dans 5 j"


def test_my_portfolio_risk_fields_default_when_no_snapshot_computed(monkeypatch, tmp_path):
    """Upgrade 1 : avant tout run `--recompute-portfolio-risk`, aucun fichier
    `my_portfolio_risk.json` n'existe → chaque ligne reçoit les valeurs par
    défaut fail-open (jamais de beta/corrélation halluciné), et le bloc
    racine `risk_snapshot` est `null`."""
    monkeypatch.setattr(risk_mod, "_STATE_PATH", tmp_path / "my_portfolio_risk.json")
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))

    r = _client(monkeypatch).get("/api/my_portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["risk_snapshot"] is None
    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["beta_recalculated"] is None
    assert bnp["beta_flag"] is False
    assert bnp["avg_correlation"] is None
    assert bnp["correlation_alert_triggered"] is False
    assert bnp["correlation_streak_weeks"] == 0
    assert bnp["data_quality"] is None


def test_my_portfolio_risk_live_roundtrip_via_disk_state(monkeypatch, tmp_path):
    """Vérification live (Upgrade 1, même définition que Upgrade 2/3 — voir
    docs/UPGRADE_PROGRESS.md) : TestClient(api.app) bout-en-bout SANS mock
    du router pour la partie risque — l'état disque produit par
    `refresh_portfolio_risk`/`compute_portfolio_risk` est réellement écrit
    puis relu par `routers/my_portfolio.py`, prouvant le pipeline complet
    (pas seulement `compute_portfolio_risk` isolé, déjà testé dans
    `test_portfolio_risk.py`)."""
    monkeypatch.setattr(risk_mod, "_STATE_PATH", tmp_path / "my_portfolio_risk.json")
    risk_mod._save_state({
        "schema_version": risk_mod.SCHEMA_VERSION,
        "risk_snapshot": {
            "portfolio_beta": 0.653,
            "avg_weighted_correlation": 0.11,
            "diversification_ratio": 2.15,
            "most_correlated_pairs": [{"a": "MU", "b": "ERO", "corr": 0.43}],
            "last_recalc_date": "2026-08-17",
            "next_recalc_date": "2026-08-24",
            "n_tickers_ok": 10,
            "n_tickers_missing": 0,
        },
        "tickers": {
            "BNP.PA": {
                "beta_recalculated": 0.42, "beta_diff_pct": 16.7, "beta_flag": False,
                "avg_correlation": 0.09, "correlation_vs_ref": None,
                "correlation_alert_triggered": False, "correlation_streak_weeks": 1,
                "data_quality": "ok",
            },
            "ERO": {
                "beta_recalculated": 1.8, "beta_diff_pct": 10.4, "beta_flag": False,
                "avg_correlation": 0.31, "correlation_vs_ref": 0.55,
                "correlation_alert_triggered": True, "correlation_streak_weeks": 1,
                "data_quality": "ok",
            },
        },
        "fetched_at": time.time(),
    })
    monkeypatch.setattr(my_portfolio_router, "_safe_price", _flat_price(100.0))

    r = _client(monkeypatch).get("/api/my_portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["risk_snapshot"]["portfolio_beta"] == 0.653
    assert body["risk_snapshot"]["most_correlated_pairs"] == [{"a": "MU", "b": "ERO", "corr": 0.43}]

    bnp = next(p for p in body["positions"] if p["ticker"] == "BNP.PA")
    assert bnp["beta_recalculated"] == 0.42
    assert bnp["correlation_alert_triggered"] is False

    ero = next(p for p in body["positions"] if p["ticker"] == "ERO")
    assert ero["correlation_vs_ref"] == 0.55
    assert ero["correlation_alert_triggered"] is True

    # Ticker sans entrée dans l'état persisté (ex : ajouté au book après le
    # dernier run hebdo) → valeurs par défaut, jamais de KeyError.
    fmx = next(p for p in body["positions"] if p["ticker"] == "FMX")
    assert fmx["beta_recalculated"] is None
    assert fmx["data_quality"] is None


# ─────────────────────────────────────────────────────────────────
# GET /api/my_portfolio/{ticker}/price_history — page détail ticker
# ─────────────────────────────────────────────────────────────────

def test_price_history_happy_path(monkeypatch):
    monkeypatch.setattr(
        my_portfolio_router, "get_price_history",
        lambda price_ticker, currency, period: [{"date": "2026-08-01", "price": 100.0}],
    )
    r = _client(monkeypatch).get("/api/my_portfolio/MU/price_history")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "MU"
    assert body["period"] == "1y"
    assert body["history"] == [{"date": "2026-08-01", "price": 100.0}]


def test_price_history_case_insensitive_ticker_lookup(monkeypatch):
    captured = {}

    def _fake(price_ticker, currency, period):
        captured["args"] = (price_ticker, currency, period)
        return []
    monkeypatch.setattr(my_portfolio_router, "get_price_history", _fake)
    r = _client(monkeypatch).get("/api/my_portfolio/mu/price_history")
    assert r.status_code == 200
    assert captured["args"] == ("MU", "USD", "1y")


def test_price_history_resolves_price_ticker_and_currency_for_lnvgy(monkeypatch):
    captured = {}

    def _fake(price_ticker, currency, period):
        captured["args"] = (price_ticker, currency, period)
        return []
    monkeypatch.setattr(my_portfolio_router, "get_price_history", _fake)
    r = _client(monkeypatch).get("/api/my_portfolio/LNVGY/price_history?period=5y")
    assert r.status_code == 200
    assert captured["args"] == ("0992.HK", "HKD", "5y")


def test_price_history_unknown_ticker_404(monkeypatch):
    r = _client(monkeypatch).get("/api/my_portfolio/NOPE/price_history")
    assert r.status_code == 404


def test_price_history_invalid_period_400(monkeypatch):
    r = _client(monkeypatch).get("/api/my_portfolio/MU/price_history?period=1d")
    assert r.status_code == 400


def test_price_history_null_when_fetch_unavailable(monkeypatch):
    monkeypatch.setattr(my_portfolio_router, "get_price_history", lambda *a, **kw: None)
    r = _client(monkeypatch).get("/api/my_portfolio/MU/price_history")
    assert r.status_code == 200
    assert r.json()["history"] is None


def test_price_history_requires_auth_when_configured(monkeypatch):
    monkeypatch.setattr(api_core, "API_TOKEN", "secret-token")
    monkeypatch.setattr(api_core, "ALLOW_UNAUTH", False)
    client = TestClient(api.app)
    r = client.get("/api/my_portfolio/MU/price_history")
    assert r.status_code == 401
