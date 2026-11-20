"""Tests modules/sec_edgar.py — focus pillar score + parsing logic."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from modules import sec_edgar
from modules.sec_edgar import (
    InsiderActivity,
    compute_insider_pillar_score,
    fetch_insider_activity,
    fetch_recent_filings,
)


def test_compute_pillar_score_neutral_on_empty():
    a = InsiderActivity(ticker="AAA")
    out = compute_insider_pillar_score(a)
    assert out["score"] == 50.0
    assert out["data_quality"] == 0.0


def test_compute_pillar_score_with_error():
    a = InsiderActivity(ticker="AAA", error="cik_unknown")
    out = compute_insider_pillar_score(a)
    assert out["score"] == 50.0
    assert out["reason"] == "cik_unknown"


def test_pillar_score_bullish_signals_boost():
    """Calibration audit 2026-11-18 : +15 (≥3 acheteurs) +10 (cluster)
    +5 (achat net ≥ 500 k$ / 90 j) = +30 → 80."""
    a = InsiderActivity(
        ticker="BBB",
        buy_count_30d=10,
        distinct_insiders_buying_30d=8,
        cluster_buying=True,
        buy_count_90d=15,
        net_buys_90d_usd=1_000_000.0,
        n_filings_scanned=15,
    )
    out = compute_insider_pillar_score(a)
    assert out["score"] == 80.0
    assert out["data_quality"] == 1.0


def test_pillar_score_moderate_distinct():
    """2 acheteurs distincts → +8 ; 1 seul → +4."""
    a = InsiderActivity(ticker="MMM", buy_count_30d=2, distinct_insiders_buying_30d=2,
                        buy_count_90d=2, n_filings_scanned=8)
    assert compute_insider_pillar_score(a)["score"] == 58.0
    a1 = InsiderActivity(ticker="MMM", buy_count_30d=1, distinct_insiders_buying_30d=1,
                         buy_count_90d=1, n_filings_scanned=8)
    assert compute_insider_pillar_score(a1)["score"] == 54.0


def test_pillar_score_caps_at_100():
    a = InsiderActivity(
        ticker="CCC",
        buy_count_30d=30,
        distinct_insiders_buying_30d=15,
        cluster_buying=True,
        buy_count_90d=50,
        n_filings_scanned=80,
    )
    out = compute_insider_pillar_score(a)
    assert out["score"] <= 100.0


def test_pillar_score_selling_is_neutral():
    """Les ventes (souvent routinières : 10b5-1, impôts RSU) ne pénalisent plus."""
    a = InsiderActivity(
        ticker="DDD",
        buy_count_30d=0,
        sell_count_30d=10,
        n_filings_scanned=11,
    )
    out = compute_insider_pillar_score(a)
    assert out["score"] == 50.0


def test_pillar_score_components_exposed():
    a = InsiderActivity(
        ticker="EEE",
        buy_count_30d=3,
        sell_count_30d=1,
        distinct_insiders_buying_30d=2,
        cluster_buying=False,
        n_filings_scanned=5,
    )
    out = compute_insider_pillar_score(a)
    c = out["components"]
    assert c["buy_count_30d"] == 3
    assert c["sell_count_30d"] == 1
    assert c["distinct_insiders_30d"] == 2
    assert c["cluster_buying"] is False


# ─────────────────────────────────────────────────────────────────
# fetch_insider_activity — extraction 8-K (Étape 9 roadmap)
# ─────────────────────────────────────────────────────────────────

def _mock_submissions_payload(forms, dates, accessions):
    return {"filings": {"recent": {
        "form": forms, "filingDate": dates, "accessionNumber": accessions,
    }}}


def test_fetch_insider_activity_extracts_most_recent_8k():
    payload = _mock_submissions_payload(
        forms=["8-K", "4", "8-K", "10-Q"],
        dates=["2026-06-01", "2026-06-15", "2026-07-10", "2026-05-01"],
        accessions=["0001-26-000001", "0001-26-000002", "0001-26-000003", "0001-26-000004"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("AAA", use_cache=False)
    # Le 8-K du 2026-07-10 est plus récent que celui du 2026-06-01.
    assert activity.most_recent_8k_date == "2026-07-10"
    assert activity.error is None


def test_fetch_insider_activity_no_8k_present():
    payload = _mock_submissions_payload(
        forms=["4", "10-Q"],
        dates=["2026-06-15", "2026-05-01"],
        accessions=["0001-26-000002", "0001-26-000004"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("BBB", use_cache=False)
    assert activity.most_recent_8k_date is None


def test_fetch_insider_activity_8ka_amendment_also_counted():
    payload = _mock_submissions_payload(
        forms=["8-K/A", "4"],
        dates=["2026-07-05", "2026-06-15"],
        accessions=["0001-26-000005", "0001-26-000002"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("CCC", use_cache=False)
    assert activity.most_recent_8k_date == "2026-07-05"


# ─────────────────────────────────────────────────────────────────
# fetch_insider_activity — extraction 10-K/10-Q (Étape 11 roadmap)
# ─────────────────────────────────────────────────────────────────

def test_fetch_insider_activity_extracts_most_recent_10k_and_10q():
    payload = _mock_submissions_payload(
        forms=["10-K", "4", "10-Q", "10-Q", "8-K"],
        dates=["2026-02-01", "2026-06-15", "2026-05-01", "2026-07-10", "2026-04-01"],
        accessions=[
            "0001-26-000001", "0001-26-000002", "0001-26-000003",
            "0001-26-000004", "0001-26-000005",
        ],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("AAA", use_cache=False)
    assert activity.most_recent_10k_date == "2026-02-01"
    # Le 10-Q du 2026-07-10 est plus récent que celui du 2026-05-01.
    assert activity.most_recent_10q_date == "2026-07-10"
    assert activity.most_recent_8k_date == "2026-04-01"


def test_fetch_insider_activity_no_10k_10q_present():
    payload = _mock_submissions_payload(
        forms=["4", "8-K"],
        dates=["2026-06-15", "2026-04-01"],
        accessions=["0001-26-000002", "0001-26-000005"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("BBB", use_cache=False)
    assert activity.most_recent_10k_date is None
    assert activity.most_recent_10q_date is None


def test_fetch_insider_activity_10k_10q_amendments_also_counted():
    payload = _mock_submissions_payload(
        forms=["10-K/A", "10-Q/A", "4"],
        dates=["2026-03-01", "2026-06-01", "2026-06-15"],
        accessions=["0001-26-000006", "0001-26-000007", "0001-26-000002"],
    )
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload), \
         patch.object(sec_edgar, "_read_cache", return_value=None), \
         patch.object(sec_edgar, "_write_cache"):
        activity = fetch_insider_activity("CCC", use_cache=False)
    assert activity.most_recent_10k_date == "2026-03-01"
    assert activity.most_recent_10q_date == "2026-06-01"


# ─────────────────────────────────────────────────────────────────
# Persistance du signal d'échec transitoire `sec_fetch_failed`
# (Étape 16 roadmap) — jusqu'ici seul `cik_unknown` était persisté,
# une panne réseau/HTTP transitoire (payload non-dict) ne l'était pas.
# ─────────────────────────────────────────────────────────────────

def test_fetch_insider_activity_persists_sec_fetch_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_CACHE_DIR", tmp_path)
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=None):
        activity = fetch_insider_activity("AAA", use_cache=False)

    assert activity.error == "sec_fetch_failed"
    # Le cache disque doit avoir été écrit — sinon dir_cache_stats() (utilisé
    # par /api/data_health) ne peut jamais remonter n_errors > 0 pour une
    # panne réseau transitoire (contrairement à cik_unknown, déjà persisté).
    cached = sec_edgar._read_cache("AAA")
    assert cached is not None
    assert cached["error"] == "sec_fetch_failed"


def test_fetch_insider_activity_sec_fetch_failed_heals_on_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_CACHE_DIR", tmp_path)
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=None):
        fetch_insider_activity("AAA", use_cache=False)
    assert sec_edgar._read_cache("AAA")["error"] == "sec_fetch_failed"

    payload = _mock_submissions_payload(forms=["4"], dates=["2026-06-15"], accessions=["0001-26-000002"])
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload):
        activity = fetch_insider_activity("AAA", use_cache=False)

    # Le run suivant réussi écrase l'entrée en erreur — pas d'accumulation.
    assert activity.error is None
    assert sec_edgar._read_cache("AAA")["error"] is None


def test_fetch_recent_filings_persists_sec_fetch_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_FILINGS_CACHE_DIR", tmp_path)
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=None):
        out = fetch_recent_filings("BBB", use_cache=False)

    assert out["error"] == "sec_fetch_failed"
    cached = sec_edgar._read_filings_cache("BBB")
    assert cached is not None
    assert cached["error"] == "sec_fetch_failed"


def test_fetch_recent_filings_sec_fetch_failed_heals_on_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_FILINGS_CACHE_DIR", tmp_path)
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=None):
        fetch_recent_filings("BBB", use_cache=False)
    assert sec_edgar._read_filings_cache("BBB")["error"] == "sec_fetch_failed"

    payload = {"filings": {"recent": {
        "form": ["10-K"], "filingDate": ["2026-06-01"],
        "accessionNumber": ["0001-26-000001"], "primaryDocument": ["doc.htm"],
    }}}
    with patch.object(sec_edgar, "ticker_to_cik", return_value="0000320193"), \
         patch.object(sec_edgar, "_fetch_json", return_value=payload):
        out = fetch_recent_filings("BBB", use_cache=False)

    # Le run suivant réussi écrase l'entrée en erreur — pas d'accumulation.
    assert out["error"] is None
    assert sec_edgar._read_filings_cache("BBB")["error"] is None


# ─────────────────────────────────────────────────────────────────
# filings_error_tickers() — liste {ticker, error} par ticker (Étape 18)
# ─────────────────────────────────────────────────────────────────

def test_filings_error_tickers_lists_only_failing_tickers(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_FILINGS_CACHE_DIR", tmp_path)
    sec_edgar._write_filings_cache("BBB", {"error": "sec_fetch_failed", "filings": []})
    sec_edgar._write_filings_cache("CCC", {"error": None, "filings": []})

    assert sec_edgar.filings_error_tickers() == [{"ticker": "BBB", "error": "sec_fetch_failed"}]


def test_filings_error_tickers_empty_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_FILINGS_CACHE_DIR", tmp_path / "missing")
    assert sec_edgar.filings_error_tickers() == []


# ─── Audit 2026-11-18 P0 #3 — parsing XML Form 4 ─────────────────────────

_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner><reportingOwnerId><rptOwnerCik>0001780525</rptOwnerCik></reportingOwnerId></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50.5</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_purchase():
    d = sec_edgar.parse_form4_xml(_FORM4_XML.format(code="P"))
    assert d is not None
    assert d["owners"] == ["1780525"]
    assert d["buy_shares"] == 1000 and d["buy_usd"] == 50500.0
    assert d["sell_shares"] == 0


def test_parse_form4_grant_is_neither_buy_nor_sell():
    d = sec_edgar.parse_form4_xml(_FORM4_XML.format(code="A"))
    assert d is not None and d["buy_shares"] == 0 and d["sell_shares"] == 0


def test_parse_form4_garbage_returns_none():
    assert sec_edgar.parse_form4_xml("<not-xml") is None


def test_fetch_insider_activity_counts_only_real_purchases(tmp_path, monkeypatch):
    """Avant : chaque Form 4 = un achat. Maintenant : seuls les codes P comptent,
    et l'insider est le rptOwnerCik (pas le préfixe d'accession)."""
    today = date.today().isoformat()
    payload = {"filings": {"recent": {
        "form": ["4", "4", "4"],
        "filingDate": [today, today, today],
        "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002", "0000000001-26-000003"],
        "primaryDocument": ["xsl/a.xml", "xsl/b.xml", "xsl/c.xml"],
    }}}
    docs = {
        "a.xml": _FORM4_XML.format(code="S"),
        "b.xml": _FORM4_XML.format(code="M"),
        "c.xml": _FORM4_XML.format(code="P"),
    }
    monkeypatch.setattr(sec_edgar, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(sec_edgar, "_FORM4_CACHE_DIR", tmp_path / "form4")
    monkeypatch.setattr(sec_edgar, "ticker_to_cik", lambda t: "0000320193")
    monkeypatch.setattr(sec_edgar, "_fetch_json", lambda url: payload)
    monkeypatch.setattr(sec_edgar, "_fetch_text", lambda url: docs[url.rsplit("/", 1)[-1]])
    a = fetch_insider_activity("ZZZ", use_cache=False)
    assert a.buy_count_90d == 1
    assert a.sell_count_90d == 1
    assert a.distinct_insiders_buying_30d == 1
    assert a.cluster_buying is False
    assert a.n_form4_parsed_90d == 3
    assert a.net_buys_90d_usd == 0.0  # +50 500 (P) − 50 500 (S)
