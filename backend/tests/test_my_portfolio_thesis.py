"""Tests unitaires — modules/my_portfolio_thesis.py.

Isolation stricte du store disque (`STORE_PATH`/`_LOCK_PATH` redirigés vers
tmp_path, autouse) — même précaution que `test_portfolio_risk.py` :
ne JAMAIS lire/écrire le vrai `data/my_portfolio_thesis.json` de prod
pendant les tests (voir memory `project_test_leaks_prod_duckdb_2026-08-14` —
régression déjà survenue avec un autre store, on ne la répète pas ici).
"""
from __future__ import annotations

import pytest

from modules import my_portfolio_thesis as mpt


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(mpt, "STORE_PATH", tmp_path / "my_portfolio_thesis.json")
    monkeypatch.setattr(mpt, "_LOCK_PATH", tmp_path / "my_portfolio_thesis.json.lock")


# ─────────────────────────────────────────────────────────────────
# get_thesis / get_all_theses — lecture pure
# ─────────────────────────────────────────────────────────────────

def test_get_thesis_unknown_ticker_returns_empty_skeleton():
    thesis = mpt.get_thesis("NOPE")
    assert thesis["why_bought"] == {"catalyseurs": [], "valorisation": None, "role_portefeuille": None}
    assert thesis["sell_signals"] == []
    assert thesis["verification"] == {
        "derniere_verification": None, "verdict": None, "historique_verifications": [],
    }
    assert thesis["updated_at"] is None


def test_get_all_theses_empty_store_returns_empty_dict():
    assert mpt.get_all_theses() == {}


# ─────────────────────────────────────────────────────────────────
# update_thesis — bloc A (why_bought), mise à jour partielle
# ─────────────────────────────────────────────────────────────────

def test_update_why_bought_partial_only_sets_provided_fields():
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["Pari NAND"]})
    thesis = mpt.get_thesis("MU")
    assert thesis["why_bought"]["catalyseurs"] == ["Pari NAND"]
    assert thesis["why_bought"]["valorisation"] is None
    assert thesis["why_bought"]["role_portefeuille"] is None

    # 2e écriture ne touchant que valorisation -> catalyseurs préservé.
    mpt.update_thesis("MU", why_bought={"valorisation": "P/E 12x, décote vs historique"})
    thesis2 = mpt.get_thesis("MU")
    assert thesis2["why_bought"]["catalyseurs"] == ["Pari NAND"]
    assert thesis2["why_bought"]["valorisation"] == "P/E 12x, décote vs historique"


def test_update_why_bought_empty_string_clears_to_none():
    mpt.update_thesis("MU", why_bought={"role_portefeuille": "Diversification"})
    mpt.update_thesis("MU", why_bought={"role_portefeuille": ""})
    assert mpt.get_thesis("MU")["why_bought"]["role_portefeuille"] is None


def test_update_why_bought_catalyseurs_strips_blank_entries():
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["  Vrai catalyseur  ", "", "   "]})
    assert mpt.get_thesis("MU")["why_bought"]["catalyseurs"] == ["Vrai catalyseur"]


# ─────────────────────────────────────────────────────────────────
# update_thesis — bloc B (sell_signals), 0/1/N sans cas particulier
# ─────────────────────────────────────────────────────────────────

def test_sell_signals_zero_items_allowed():
    mpt.update_thesis("MU", sell_signals=[])
    assert mpt.get_thesis("MU")["sell_signals"] == []


def test_sell_signals_single_item():
    mpt.update_thesis("MU", sell_signals=[
        {"libelle": "Utilisation capacité < 90%", "statut": "intact"},
    ])
    signals = mpt.get_thesis("MU")["sell_signals"]
    assert len(signals) == 1
    assert signals[0]["libelle"] == "Utilisation capacité < 90%"
    assert signals[0]["statut"] == "intact"
    assert signals[0]["id"].startswith("sig_")


def test_sell_signals_five_items():
    items = [{"libelle": f"Signal {i}", "statut": "a_surveiller"} for i in range(5)]
    mpt.update_thesis("MU", sell_signals=items)
    signals = mpt.get_thesis("MU")["sell_signals"]
    assert len(signals) == 5
    assert len({s["id"] for s in signals}) == 5  # ids tous uniques


def test_sell_signals_replaces_whole_list_not_merge():
    mpt.update_thesis("MU", sell_signals=[{"libelle": "A", "statut": "intact"}])
    mpt.update_thesis("MU", sell_signals=[{"libelle": "B", "statut": "declenche"}])
    signals = mpt.get_thesis("MU")["sell_signals"]
    assert len(signals) == 1
    assert signals[0]["libelle"] == "B"


def test_sell_signals_preserves_existing_id_when_provided():
    mpt.update_thesis("MU", sell_signals=[{"libelle": "A", "statut": "intact"}])
    existing_id = mpt.get_thesis("MU")["sell_signals"][0]["id"]
    mpt.update_thesis("MU", sell_signals=[{"id": existing_id, "libelle": "A modifié", "statut": "a_surveiller"}])
    signals = mpt.get_thesis("MU")["sell_signals"]
    assert signals[0]["id"] == existing_id
    assert signals[0]["libelle"] == "A modifié"


def test_sell_signals_invalid_statut_raises():
    with pytest.raises(ValueError, match="statut invalide"):
        mpt.update_thesis("MU", sell_signals=[{"libelle": "A", "statut": "bullish"}])


def test_sell_signals_missing_libelle_raises():
    with pytest.raises(ValueError, match="libelle requis"):
        mpt.update_thesis("MU", sell_signals=[{"libelle": "  ", "statut": "intact"}])


def test_sell_signals_auto_metric_defaults_to_none():
    mpt.update_thesis("MU", sell_signals=[{"libelle": "A", "statut": "intact"}])
    assert mpt.get_thesis("MU")["sell_signals"][0]["auto_metric"] is None


def test_sell_signals_auto_metric_valid_value_persists():
    mpt.update_thesis("BNP.PA", sell_signals=[
        {"libelle": "Beta >0.8 durable ou corrélation >0.40", "statut": "a_surveiller",
         "auto_metric": "stabilizer_beta_correlation"},
    ])
    assert mpt.get_thesis("BNP.PA")["sell_signals"][0]["auto_metric"] == "stabilizer_beta_correlation"


def test_sell_signals_auto_metric_invalid_value_raises():
    with pytest.raises(ValueError, match="auto_metric invalide"):
        mpt.update_thesis("MU", sell_signals=[
            {"libelle": "A", "statut": "intact", "auto_metric": "not_a_real_metric"},
        ])


# ─────────────────────────────────────────────────────────────────
# update_thesis — bloc C (verification) — auto-push vers l'historique
# ─────────────────────────────────────────────────────────────────

def test_first_verification_does_not_push_to_history():
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-08-01", "verdict": "thèse intacte"})
    v = mpt.get_thesis("MU")["verification"]
    assert v["derniere_verification"] == "2026-08-01"
    assert v["verdict"] == "thèse intacte"
    assert v["historique_verifications"] == []


def test_second_verification_pushes_previous_into_history():
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-08-01", "verdict": "thèse intacte"})
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-09-01", "verdict": "T2 en dessous des attentes"})
    v = mpt.get_thesis("MU")["verification"]
    assert v["derniere_verification"] == "2026-09-01"
    assert v["verdict"] == "T2 en dessous des attentes"
    assert v["historique_verifications"] == [{"date": "2026-08-01", "verdict": "thèse intacte"}]


def test_third_verification_accumulates_history_in_order():
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-07-01", "verdict": "v1"})
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-08-01", "verdict": "v2"})
    mpt.update_thesis("MU", verification={"derniere_verification": "2026-09-01", "verdict": "v3"})
    v = mpt.get_thesis("MU")["verification"]
    assert v["derniere_verification"] == "2026-09-01"
    assert v["historique_verifications"] == [
        {"date": "2026-07-01", "verdict": "v1"},
        {"date": "2026-08-01", "verdict": "v2"},
    ]


def test_verification_invalid_date_format_raises():
    with pytest.raises(ValueError):
        mpt.update_thesis("MU", verification={"derniere_verification": "01/08/2026", "verdict": "x"})


def test_verification_empty_verdict_raises():
    with pytest.raises(ValueError, match="verification requiert"):
        mpt.update_thesis("MU", verification={"derniere_verification": "2026-08-01", "verdict": "   "})


def test_verification_never_computed_automatically_stays_none():
    """Contrainte non négociable : sans écriture explicite via update_thesis,
    verification reste None — jamais déduit d'une autre donnée."""
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["x"]})
    v = mpt.get_thesis("MU")["verification"]
    assert v["derniere_verification"] is None
    assert v["verdict"] is None


# ─────────────────────────────────────────────────────────────────
# update_thesis — updated_at
# ─────────────────────────────────────────────────────────────────

def test_updated_at_stamped_on_write():
    assert mpt.get_thesis("MU")["updated_at"] is None
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["x"]})
    assert mpt.get_thesis("MU")["updated_at"] is not None


# ─────────────────────────────────────────────────────────────────
# seed_if_empty — idempotent, jamais d'écrasement
# ─────────────────────────────────────────────────────────────────

def test_seed_if_empty_adds_missing_tickers():
    added = mpt.seed_if_empty({
        "MU": {
            "why_bought": {"catalyseurs": ["Pari NAND"], "valorisation": None, "role_portefeuille": None},
            "sell_signals": [], "verification": {"derniere_verification": None, "verdict": None, "historique_verifications": []},
            "updated_at": "2026-09-03T00:00:00+00:00",
        },
    })
    assert added == 1
    assert mpt.get_thesis("MU")["why_bought"]["catalyseurs"] == ["Pari NAND"]


def test_seed_if_empty_never_overwrites_existing_ticker():
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["Déjà édité par l'utilisateur"]})
    added = mpt.seed_if_empty({
        "MU": {
            "why_bought": {"catalyseurs": ["Contenu de seed"], "valorisation": None, "role_portefeuille": None},
            "sell_signals": [], "verification": {"derniere_verification": None, "verdict": None, "historique_verifications": []},
            "updated_at": "2026-09-03T00:00:00+00:00",
        },
    })
    assert added == 0
    assert mpt.get_thesis("MU")["why_bought"]["catalyseurs"] == ["Déjà édité par l'utilisateur"]


def test_seed_if_empty_multiple_tickers_mixed():
    mpt.update_thesis("MU", why_bought={"catalyseurs": ["existant"]})
    added = mpt.seed_if_empty({
        "MU": {"why_bought": {"catalyseurs": ["seed"], "valorisation": None, "role_portefeuille": None},
               "sell_signals": [], "verification": {"derniere_verification": None, "verdict": None, "historique_verifications": []},
               "updated_at": "x"},
        "ERO": {"why_bought": {"catalyseurs": ["seed ERO"], "valorisation": None, "role_portefeuille": None},
                "sell_signals": [], "verification": {"derniere_verification": None, "verdict": None, "historique_verifications": []},
                "updated_at": "x"},
    })
    assert added == 1
    assert mpt.get_thesis("MU")["why_bought"]["catalyseurs"] == ["existant"]
    assert mpt.get_thesis("ERO")["why_bought"]["catalyseurs"] == ["seed ERO"]
