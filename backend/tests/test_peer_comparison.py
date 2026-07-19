"""Tests modules/peer_comparison.py."""
from __future__ import annotations

from modules.peer_comparison import build_compare_table, build_peer_table, find_peers


def _ticker(name, sector, industry, mcap, **extra):
    base = {
        "name": name,
        "sector": sector,
        "industry": industry,
        "market_cap": mcap,
        "trailing_pe": 20.0,
        "titan_composite_score": 60.0,
    }
    base.update(extra)
    return base


def test_find_peers_basic_sector_match():
    universe = {
        "AAA": _ticker("Alpha", "Technology", "Software", 1_000_000_000),
        "BBB": _ticker("Beta", "Technology", "Software", 1_200_000_000),
        "CCC": _ticker("Gamma", "Technology", "Hardware", 900_000_000),
        "DDD": _ticker("Delta", "Healthcare", "Pharma", 1_100_000_000),
    }
    peers = find_peers("AAA", universe, n=5)
    tickers = [p["ticker"] for p in peers]
    assert "BBB" in tickers and "CCC" in tickers
    assert "DDD" not in tickers  # secteur différent


def test_find_peers_filters_by_market_cap_band():
    universe = {
        "AAA": _ticker("Alpha", "Tech", "Software", 1e9),
        "BIG": _ticker("Big", "Tech", "Software", 100e9),  # > 3× cap → exclu
        "TINY": _ticker("Tiny", "Tech", "Software", 100e6),  # < 0.3× → exclu
        "PEER": _ticker("Peer", "Tech", "Software", 1.5e9),
    }
    peers = find_peers("AAA", universe, n=5)
    tickers = [p["ticker"] for p in peers]
    assert "PEER" in tickers
    assert "BIG" not in tickers and "TINY" not in tickers


def test_find_peers_unknown_target():
    assert find_peers("XXX", {}, n=5) == []


def test_build_peer_table_structure():
    universe = {
        "AAA": _ticker("Alpha", "Tech", "Software", 1e9, trailing_pe=15.0),
        "BBB": _ticker("Beta", "Tech", "Software", 1.2e9, trailing_pe=25.0),
        "CCC": _ticker("Gamma", "Tech", "Software", 0.8e9, trailing_pe=20.0),
    }
    table = build_peer_table("AAA", universe, n=5)
    assert table["target"]["ticker"] == "AAA"
    assert "peers" in table and len(table["peers"]) >= 1
    assert "sector_median" in table
    assert table["sector_median"]["trailing_pe"] is not None


def test_industry_match_ranks_higher():
    """Same industry should rank closer than same sector but different industry."""
    universe = {
        "AAA": _ticker("Alpha", "Tech", "Software", 1e9),
        "SAME_IND": _ticker("Same", "Tech", "Software", 1.1e9),
        "DIFF_IND": _ticker("Diff", "Tech", "Hardware", 1.05e9),
    }
    peers = find_peers("AAA", universe, n=2)
    assert peers[0]["ticker"] == "SAME_IND"


def test_build_compare_table_basic():
    universe = {
        "AAA": _ticker("Alpha", "Tech", "Software", 1e9, trailing_pe=10.0),
        "BBB": _ticker("Beta", "Healthcare", "Pharma", 5e9, trailing_pe=30.0),
    }
    table = build_compare_table(["aaa", "bbb"], universe)
    assert table["requested"] == ["AAA", "BBB"]
    assert [r["ticker"] for r in table["rows"]] == ["AAA", "BBB"]
    assert table["missing"] == []
    assert table["median"]["trailing_pe"] == 20.0  # médiane de 10 et 30


def test_build_compare_table_dedup_and_order():
    universe = {"AAA": _ticker("Alpha", "Tech", "Software", 1e9)}
    table = build_compare_table(["AAA", "aaa", "AAA"], universe)
    assert table["requested"] == ["AAA"]
    assert len(table["rows"]) == 1


def test_build_compare_table_missing_ticker():
    universe = {"AAA": _ticker("Alpha", "Tech", "Software", 1e9)}
    table = build_compare_table(["AAA", "ZZZ"], universe)
    assert table["missing"] == ["ZZZ"]
    assert [r["ticker"] for r in table["rows"]] == ["AAA"]


def test_build_compare_table_no_matches():
    table = build_compare_table(["ZZZ", "YYY"], {})
    assert table["rows"] == []
    assert table["missing"] == ["ZZZ", "YYY"]
    assert all(v is None for v in table["median"].values())
