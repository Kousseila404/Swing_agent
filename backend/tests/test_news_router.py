"""Tests routers/news.py — GET /api/news/portfolio/firehose (Étape 12).

Focus : 2e passe de dédup après agrégation multi-tickers (deux tickers
peuvent partager un article macro identique, chacun déjà dédupliqué par
finnhub_news.fetch_news mais pas l'un contre l'autre).
"""
from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

import api
import routers.news as news_router
from modules import watchlist as wl_mod


def _client() -> TestClient:
    return TestClient(api.app)


def test_firehose_dedups_shared_article_across_tickers(monkeypatch):
    monkeypatch.setattr(news_router, "read_journal_df", lambda: pd.DataFrame())
    monkeypatch.setattr(wl_mod, "list_watchlist", lambda: [
        {"ticker": "AAA"}, {"ticker": "BBB"},
    ])

    shared = {
        "headline": "Fed Holds Rates Steady", "summary": "", "source": "Reuters",
        "url": "https://x/1", "image": "", "category": "", "datetime": "2026-07-21T10:00:00Z",
        "id": "shared",
    }
    unique_aaa = {
        "headline": "AAA Beats Estimates", "summary": "", "source": "Zacks",
        "url": "https://x/2", "image": "", "category": "", "datetime": "2026-07-21T09:00:00Z",
        "id": "aaa1",
    }

    def fake_fetch_news(ticker, days=7, max_items=5):
        if ticker == "AAA":
            return {"ticker": "AAA", "articles": [shared, unique_aaa], "n_articles": 2, "error": None}
        if ticker == "BBB":
            return {"ticker": "BBB", "articles": [dict(shared)], "n_articles": 1, "error": None}
        return {"ticker": ticker, "articles": [], "n_articles": 0, "error": None}

    monkeypatch.setattr(news_router.finnhub_news, "fetch_news", fake_fetch_news)

    r = _client().get("/api/news/portfolio/firehose")
    assert r.status_code == 200
    body = r.json()
    headlines = [item["headline"] for item in body["items"]]
    assert headlines.count("Fed Holds Rates Steady") == 1
    assert "AAA Beats Estimates" in headlines
    assert body["n_items"] == len(body["items"])


def test_firehose_no_dedup_across_distinct_articles(monkeypatch):
    monkeypatch.setattr(news_router, "read_journal_df", lambda: pd.DataFrame())
    monkeypatch.setattr(wl_mod, "list_watchlist", lambda: [{"ticker": "AAA"}])

    articles = [
        {"headline": "AAA News One", "url": "https://x/1", "datetime": "2026-07-21T10:00:00Z", "id": "1"},
        {"headline": "AAA News Two", "url": "https://x/2", "datetime": "2026-07-21T09:00:00Z", "id": "2"},
    ]
    monkeypatch.setattr(
        news_router.finnhub_news, "fetch_news",
        lambda ticker, days=7, max_items=5: {"ticker": ticker, "articles": articles, "n_articles": 2, "error": None},
    )

    r = _client().get("/api/news/portfolio/firehose")
    assert r.status_code == 200
    body = r.json()
    assert body["n_items"] == 2
