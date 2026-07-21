"""Tests modules/finnhub_news.py — dédup des articles ré-syndiqués (Étape 12).

On mock request.urlopen pour éviter les appels réseau réels.
"""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib import error as urlerror

from modules import finnhub_news


def test_dedup_key_normalizes_headline_case_and_punctuation():
    a = {"headline": "Apple Reports Q3 Earnings!", "url": "https://a.example/1"}
    b = {"headline": "  apple   reports q3 earnings  ", "url": "https://b.example/2"}
    assert finnhub_news.dedup_key(a) == finnhub_news.dedup_key(b)


def test_dedup_key_falls_back_to_url_when_headline_empty():
    a = {"headline": "", "url": "https://a.example/1"}
    assert finnhub_news.dedup_key(a) == "https://a.example/1"


def test_dedup_articles_keeps_first_occurrence():
    articles = [
        {"headline": "Apple Reports Q3 Earnings", "id": "1"},
        {"headline": "Apple reports Q3 earnings!!", "id": "2"},  # doublon ré-syndiqué
        {"headline": "Apple Announces Buyback", "id": "3"},
    ]
    out = finnhub_news.dedup_articles(articles)
    assert [a["id"] for a in out] == ["1", "3"]


def test_dedup_articles_does_not_collapse_distinct_empty_keys():
    # Deux articles sans headline ni url ne doivent pas se faire collapser
    # l'un l'autre (clé vide == pas de dédup possible, pas un faux-doublon).
    articles = [{"headline": "", "url": "", "id": "1"}, {"headline": "", "url": "", "id": "2"}]
    out = finnhub_news.dedup_articles(articles)
    assert [a["id"] for a in out] == ["1", "2"]


def _urlopen_response(payload):
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp()


def test_fetch_news_dedups_before_capping_max_items(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)

    raw = [
        {"id": 1, "headline": "Apple Reports Q3 Earnings", "datetime": 1_700_000_300, "url": "https://a/1"},
        {"id": 2, "headline": "Apple reports Q3 earnings!", "datetime": 1_700_000_200, "url": "https://a/2"},
        {"id": 3, "headline": "Apple Announces New Buyback Plan", "datetime": 1_700_000_100, "url": "https://a/3"},
    ]

    with patch("modules.finnhub_news.request.urlopen", return_value=_urlopen_response(raw)):
        out = finnhub_news.fetch_news("AAPL", days=7, max_items=30)

    assert out["error"] is None
    assert out["n_articles"] == 2
    headlines = [a["headline"] for a in out["articles"]]
    assert headlines == ["Apple Reports Q3 Earnings", "Apple Announces New Buyback Plan"]


def test_fetch_news_dedup_lets_distinct_articles_through_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)

    raw = [
        {"id": 1, "headline": "Apple Reports Q3 Earnings", "datetime": 1_700_000_300},
        {"id": 2, "headline": "Apple reports Q3 earnings!", "datetime": 1_700_000_200},  # doublon
        {"id": 3, "headline": "Apple Announces New Buyback Plan", "datetime": 1_700_000_100},
    ]

    with patch("modules.finnhub_news.request.urlopen", return_value=_urlopen_response(raw)):
        out = finnhub_news.fetch_news("AAPL", days=7, max_items=2)

    # Sans dédup, le cap à 2 aurait coupé "Buyback" au profit du doublon.
    assert out["n_articles"] == 2
    headlines = {a["headline"] for a in out["articles"]}
    assert headlines == {"Apple Reports Q3 Earnings", "Apple Announces New Buyback Plan"}


# ─────────────────────────────────────────────────────────────────
# fetch_news — persistance du signal d'échec (Étape 16 roadmap)
# ─────────────────────────────────────────────────────────────────

def test_fetch_news_persists_error_on_http_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)
    exc = urlerror.HTTPError("https://x", 429, "Too Many Requests", None, None)

    with patch("modules.finnhub_news.request.urlopen", side_effect=exc):
        out = finnhub_news.fetch_news("AAPL", days=7)

    assert out["error"] == "HTTP 429"
    # Le cache disque doit avoir été écrit avec le signal d'échec — sinon
    # dir_cache_stats() (utilisé par /api/data_health) ne peut jamais
    # remonter n_errors > 0 pour cette source (Étape 16).
    cached = finnhub_news._read_cache("AAPL", 7)
    assert cached is not None
    assert cached["error"] == "HTTP 429"


def test_fetch_news_persists_error_on_network_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)

    with patch("modules.finnhub_news.request.urlopen", side_effect=TimeoutError("timed out")):
        out = finnhub_news.fetch_news("AAPL", days=7)

    assert out["error"] == "timed out"
    cached = finnhub_news._read_cache("AAPL", 7)
    assert cached is not None
    assert cached["error"] == "timed out"


def test_fetch_news_persists_error_on_unexpected_response_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)

    with patch("modules.finnhub_news.request.urlopen", return_value=_urlopen_response({"not": "a list"})):
        out = finnhub_news.fetch_news("AAPL", days=7)

    assert out["error"] == "unexpected response shape"
    cached = finnhub_news._read_cache("AAPL", 7)
    assert cached is not None
    assert cached["error"] == "unexpected response shape"


def test_fetch_news_does_not_persist_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)

    out = finnhub_news.fetch_news("AAPL", days=7)

    assert out["error"] == "FINNHUB_API_KEY not configured"
    # Absence de clé API n'est pas une panne de service à observer dans le
    # temps (config locale, pas un incident réseau) — ne doit rien écrire.
    assert finnhub_news._read_cache("AAPL", 7) is None


def test_fetch_news_error_cache_heals_on_next_successful_run(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    monkeypatch.setattr(finnhub_news, "_CACHE_DIR", tmp_path)
    exc = urlerror.HTTPError("https://x", 500, "Server Error", None, None)

    with patch("modules.finnhub_news.request.urlopen", side_effect=exc):
        finnhub_news.fetch_news("AAPL", days=7)
    assert finnhub_news._read_cache("AAPL", 7)["error"] == "HTTP 500"

    # Simule l'expiration du TTL (1h) pour forcer un vrai re-fetch, comme un
    # run suivant du cron — sinon le 2e appel lirait juste le cache d'erreur.
    monkeypatch.setattr(finnhub_news, "_CACHE_TTL_SECONDS", 0)
    raw = [{"id": 1, "headline": "Apple Reports Q3 Earnings", "datetime": 1_700_000_300}]
    with patch("modules.finnhub_news.request.urlopen", return_value=_urlopen_response(raw)):
        out = finnhub_news.fetch_news("AAPL", days=7)

    # Le run suivant réussi écrase l'entrée en erreur — pas d'accumulation.
    assert out["error"] is None
    monkeypatch.setattr(finnhub_news, "_CACHE_TTL_SECONDS", 3600)
    cached = finnhub_news._read_cache("AAPL", 7)
    assert cached["error"] is None
    assert cached["n_articles"] == 1
