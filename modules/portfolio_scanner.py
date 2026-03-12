"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — PORTFOLIO SCANNER V3 (RELATIVE STRENGTH)             ║
║  Sélection des meilleures actions par Force Relative vs S&P500 ║
║                                                                  ║
║  Architecture V15 — Ségrégation Lecture / Écriture :            ║
║    1. scan_portfolio()      — Liste custom (yfinance direct)    ║
║    2. scan_sp500()          — LECTURE SEULE depuis market_cache ║
║         → 0 appel réseau | 0 SQLite lock | instantané          ║
║    3. update_market_cache() — Mise à jour contrôlée (anti-ban)  ║
║         → User-Agent Chrome + lots de 20 + threads=False        ║
║         → Jitter 1.0-2.5 s entre lots | CSV par ticker         ║
║                                                                  ║
║  Usage CLI :                                                     ║
║    python main.py --scan-sp500       → scan (cache seul)        ║
║    python main.py --update-cache     → mise à jour anti-ban     ║
║    python main.py --update-cache --force-refresh  → force DL   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

os.environ["YF_NOCACHE"] = "1"   # Désactive le cache SQLite yfinance (évite les erreurs DB)

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from modules.log import logger


# ─────────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────────

BENCHMARK        = "^GSPC"       # S&P500 — Référence de marché
RS_PERIOD_DAYS   = 126           # 6 mois ≈ 126 jours de trading
DEFAULT_TOP_N    = 3             # Top 3 par défaut (scan_portfolio)

DEFAULT_PORTFOLIO: list[str] = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN",
    "META", "GOOGL", "PLTR", "COIN", "MSTR",
]

# ── Massive Scanner (scan_sp500) ──────────────────────────────────
SP500_WIKI_URL       = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_TOP_N          = 10          # Top 10 sur ~503 tickers
LIQUIDITY_MIN_PRICE  = 10.0        # Close > $10
LIQUIDITY_MIN_VOL    = 2_000_000   # Volume moyen 20j > 2M
LIQUIDITY_WINDOW     = 20          # Fenêtre liquidité (jours)
CACHE_DIR            = Path("data/market_cache")   # Cache CSV journalier anti-ban

# ── User-Agent anti-ban ────────────────────────────────────────────
_UA_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────────
# UTILITAIRES CACHE CSV
# ─────────────────────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    """Retourne le chemin du fichier CSV cache pour un ticker."""
    safe = ticker.replace("^", "").replace("/", "_")
    return CACHE_DIR / f"{safe}.csv"


def _load_cache(ticker: str) -> Optional[pd.DataFrame]:
    """
    Charge les données OHLCV d'un ticker depuis le cache CSV local.

    Returns:
        DataFrame avec DatetimeIndex, ou None si absent / données insuffisantes.
    """
    fp = _cache_path(ticker)
    if not fp.exists():
        return None
    try:
        df = pd.read_csv(fp, index_col=0, parse_dates=True)
        if df.empty or len(df) < 10:
            return None
        # Garantir un DatetimeIndex valide
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# CALCUL RENDEMENT / FORCE RELATIVE (utilitaires partagés)
# ─────────────────────────────────────────────────────────────────

def _compute_return(ticker: str, start: datetime, end: datetime) -> Optional[float]:
    """
    Télécharge les données Daily et calcule le rendement total sur la période.

    Utilisé uniquement par scan_portfolio() (liste custom, faible volume de requêtes).

    Returns:
        Rendement en décimal (ex : 0.35 = +35 %), ou None si données insuffisantes.
    """
    try:
        df = yf.Ticker(ticker).history(
            start=start, end=end, interval="1d", auto_adjust=True
        )
        if df is None or len(df) < 10:
            logger.warning(
                f"[PortfolioScanner] {ticker} : données insuffisantes "
                f"({len(df) if df is not None else 0} barres)"
            )
            return None

        df = df.dropna(subset=["Close"])
        if len(df) < 2:
            return None

        first_close = float(df["Close"].iloc[0])
        last_close  = float(df["Close"].iloc[-1])

        if first_close <= 0:
            return None

        return (last_close / first_close) - 1.0

    except Exception as e:
        logger.error(f"[PortfolioScanner] Erreur téléchargement {ticker} : {e}")
        return None


def _compute_relative_strength(ticker_return: float, benchmark_return: float) -> float:
    """
    Calcule le score de Force Relative par rapport au benchmark.

    Formule :
      - Si les deux rendements positifs : RS = ticker / benchmark (ratio de surperformance)
      - Sinon : RS = ticker - benchmark (différence absolue, marché baissier)
    """
    if benchmark_return > 0 and ticker_return > 0:
        return ticker_return / benchmark_return
    return ticker_return - benchmark_return


# ─────────────────────────────────────────────────────────────────
# SCANNER PORTEFEUILLE (liste custom — yfinance direct)
# ─────────────────────────────────────────────────────────────────

def scan_portfolio(
    tickers: list[str],
    top_n: int = DEFAULT_TOP_N,
    rs_period_days: int = RS_PERIOD_DAYS,
) -> list[dict]:
    """
    Scanne une liste custom de tickers et retourne le Top N par Force Relative.

    Utilise yfinance directement (faible nombre de tickers — acceptable).

    Args:
        tickers:         Liste de symboles yfinance à analyser.
        top_n:           Nombre de tickers à retourner (défaut : 3).
        rs_period_days:  Lookback en jours de trading (défaut : 126 ≈ 6 mois).

    Returns:
        Liste de dicts triée par RS décroissant, longueur max = top_n.
    """
    end   = datetime.now()
    start = end - timedelta(days=rs_period_days + 15)   # +15j de marge yfinance

    logger.info(
        f"[PortfolioScanner] Calcul Force Relative — {len(tickers)} tickers | "
        f"Période : {rs_period_days}j ({start:%Y-%m-%d} → {end:%Y-%m-%d})"
    )

    # ── 1. Rendement du benchmark ──────────────────────────────────
    benchmark_return = _compute_return(BENCHMARK, start, end)
    if benchmark_return is None:
        logger.error("[PortfolioScanner] Impossible de récupérer les données S&P500")
        return []

    logger.info(
        f"[PortfolioScanner] S&P500 rendement ({rs_period_days}j) : "
        f"{benchmark_return:+.1%}"
    )

    # ── 2. Rendement de chaque ticker ─────────────────────────────
    results: list[dict] = []
    for ticker in tickers:
        ticker_return = _compute_return(ticker, start, end)
        if ticker_return is None:
            logger.debug(f"[PortfolioScanner] {ticker} ignoré (données manquantes)")
            continue

        rs_score = _compute_relative_strength(ticker_return, benchmark_return)
        results.append({
            "ticker":               ticker,
            "rs_score":             round(rs_score, 4),
            "ticker_return_pct":    round(ticker_return * 100, 2),
            "benchmark_return_pct": round(benchmark_return * 100, 2),
        })
        logger.debug(
            f"[PortfolioScanner] {ticker:6s} : rendement {ticker_return:+.1%} | RS={rs_score:.3f}"
        )

    if not results:
        logger.warning("[PortfolioScanner] Aucun ticker valide dans le portefeuille")
        return []

    # ── 3. Tri par Force Relative décroissante ─────────────────────
    results.sort(key=lambda x: x["rs_score"], reverse=True)
    top_results = results[:top_n]

    logger.info(f"[PortfolioScanner] ── Top {len(top_results)} sélectionné(s) ──")
    for i, r in enumerate(top_results, 1):
        outperf = r["ticker_return_pct"] - r["benchmark_return_pct"]
        icon    = "🟢" if outperf >= 0 else "🔴"
        logger.info(
            f"[PortfolioScanner]   #{i} {icon} {r['ticker']:6s} | "
            f"Return={r['ticker_return_pct']:+.1f}% | "
            f"vs S&P={outperf:+.1f}% | RS={r['rs_score']:.3f}"
        )

    return top_results


# ─────────────────────────────────────────────────────────────────
# MASSIVE SCANNER — S&P500 COMPLET (lecture cache seule — V15)
# ─────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    """
    Scrape la liste actuelle des tickers du S&P500 depuis Wikipedia.

    Nettoie les symboles pour yfinance (BRK.B → BRK-B).

    Returns:
        Liste de ~503 symboles yfinance, ou liste vide en cas d'erreur.
    """
    try:
        import requests
        logger.info(f"[SP500Scanner] Scraping Wikipedia : {SP500_WIKI_URL}")
        response = requests.get(
            SP500_WIKI_URL,
            headers={"User-Agent": _UA_CHROME},
            timeout=15,
        )
        response.raise_for_status()
        tables  = pd.read_html(response.text, header=0)
        df      = tables[0]
        tickers = df["Symbol"].dropna().tolist()
        tickers = [str(t).replace(".", "-").strip().upper() for t in tickers]
        logger.info(f"[SP500Scanner] {len(tickers)} tickers récupérés depuis Wikipedia")
        return tickers
    except Exception as e:
        logger.error(f"[SP500Scanner] Erreur scraping Wikipedia : {e}")
        return []


def _console_progress(current: int, total: int, prefix: str = "", width: int = 40) -> None:
    """Affiche une barre de progression ASCII dans la console (inline)."""
    pct    = current / total if total else 0
    filled = int(width * pct)
    bar    = "█" * filled + "░" * (width - filled)
    print(f"\r  {prefix}[{bar}] {current}/{total} ({pct:.0%})", end="", flush=True)
    if current >= total:
        print()  # Saut de ligne final


def scan_sp500(
    top_n: int = SP500_TOP_N,
) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    """
    Massive Scanner V15 — S&P500 complet par Force Relative.

    MODE LECTURE SEULE : aucun appel réseau.
    Toutes les données sont chargées depuis data/market_cache/<TICKER>.csv.
    Les tickers absents du cache sont ignorés silencieusement.

    Pour peupler ou rafraîchir le cache :
        python main.py --update-cache

    Pipeline :
      1. Scrape la liste S&P500 depuis Wikipedia (1 requête HTTP légère)
      2. Charge le benchmark ^GSPC depuis le cache CSV (GSPC.csv)
      3. Charge chaque ticker depuis data/market_cache/<TICKER>.csv
      4. Filtre de liquidité institutionnelle (Close > $10, AvgVol20j > 2M)
      5. Calcul Force Relative → classement → Top N
      6. Retourne Top N + DataFrames pour le scan ADX (0 re-téléchargement)

    Args:
        top_n: Nombre d'actions à retourner (défaut : 10).

    Returns:
        Tuple (top_list, market_data) :
          - top_list    : liste de dicts triée par RS décroissant
          - market_data : dict {ticker → DataFrame} pour le Top N uniquement
    """
    # ── 1. Récupération de la liste S&P500 ──────────────────────
    sp500_tickers = get_sp500_tickers()
    if not sp500_tickers:
        logger.error("[SP500Scanner] Impossible de récupérer les tickers S&P500")
        return [], {}

    total = len(sp500_tickers)
    print(f"\n{'═' * 65}")
    print(f"  🔍  MASSIVE SCANNER — S&P500 ({total} tickers)")
    print(f"  ⚡  Mode lecture seule — cache local : data/market_cache/")
    print(f"  💡  Données manquantes ? → python main.py --update-cache")
    print(f"{'═' * 65}")

    # ── 2. Benchmark depuis le cache ────────────────────────────
    print(f"  📂  Chargement benchmark ^GSPC depuis le cache...", flush=True)
    bm_df = _load_cache(BENCHMARK)   # lit GSPC.csv
    if bm_df is None or "Close" not in bm_df.columns:
        logger.error(
            "[SP500Scanner] Benchmark ^GSPC absent du cache. "
            "Lancez : python main.py --update-cache"
        )
        return [], {}

    bm_close     = bm_df["Close"].dropna()
    rs_window    = RS_PERIOD_DAYS + 15   # 141 barres ≈ 7 mois
    bm_rs_close  = bm_close.tail(rs_window)

    if len(bm_rs_close) < 10:
        logger.error("[SP500Scanner] Données benchmark insuffisantes dans le cache")
        return [], {}

    benchmark_return = float(bm_rs_close.iloc[-1] / bm_rs_close.iloc[0]) - 1.0
    logger.info(f"[SP500Scanner] S&P500 rendement ~6m (cache) : {benchmark_return:+.1%}")

    # ── 3. Chargement de chaque ticker depuis le cache ───────────
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  ⏳  Chargement {total} tickers depuis le cache...", flush=True)

    market_data:  dict[str, pd.DataFrame] = {}
    cache_hits    = 0
    cache_misses  = 0

    for i, ticker in enumerate(sp500_tickers):
        _console_progress(i + 1, total, prefix="  CACHE ")
        df = _load_cache(ticker)
        if df is not None and len(df) > 0:
            market_data[ticker] = df
            cache_hits += 1
        else:
            cache_misses += 1

    logger.info(
        f"[SP500Scanner] Cache chargé : {cache_hits}/{total} tickers OK "
        f"({cache_misses} absent(s))"
    )
    print(
        f"\n  ✅  {cache_hits} tickers chargés  |  "
        f"⚠️  {cache_misses} absent(s) du cache",
        flush=True,
    )

    if cache_hits == 0:
        logger.error(
            "[SP500Scanner] Cache vide ! "
            "Lancez d'abord : python main.py --update-cache"
        )
        return [], {}

    # ── 4. Filtre liquidité + calcul RS ─────────────────────────
    print(f"\n  📊  Filtre liquidité + calcul Force Relative...", flush=True)

    results:        list[dict] = []
    filtered_price  = 0
    filtered_volume = 0
    errors          = 0

    for i, ticker in enumerate(sp500_tickers):
        _console_progress(i + 1, total, prefix="  RS  ")

        try:
            df = market_data.get(ticker)
            if df is None or len(df) < LIQUIDITY_WINDOW + 5:
                errors += 1
                continue

            close_series  = df["Close"].dropna()
            volume_series = df["Volume"].dropna()

            if len(close_series) < LIQUIDITY_WINDOW + 5 or len(volume_series) < LIQUIDITY_WINDOW:
                errors += 1
                continue

            last_close  = float(close_series.iloc[-1])
            avg_vol_20d = float(volume_series.tail(LIQUIDITY_WINDOW).mean())

            # ── Filtre liquidité institutionnelle ──────────────
            if last_close <= LIQUIDITY_MIN_PRICE:
                filtered_price += 1
                continue

            if avg_vol_20d <= LIQUIDITY_MIN_VOL:
                filtered_volume += 1
                continue

            # ── RS : 7 derniers mois (queue du DataFrame 2 ans) ──
            rs_close    = close_series.tail(rs_window)
            if len(rs_close) < 10:
                errors += 1
                continue

            first_close = float(rs_close.iloc[0])
            if first_close <= 0:
                errors += 1
                continue

            ticker_return = (last_close / first_close) - 1.0
            rs_score      = _compute_relative_strength(ticker_return, benchmark_return)

            results.append({
                "ticker":               ticker,
                "rs_score":             round(rs_score, 4),
                "ticker_return_pct":    round(ticker_return * 100, 2),
                "benchmark_return_pct": round(benchmark_return * 100, 2),
                "last_close":           round(last_close, 2),
                "avg_vol_20d":          int(avg_vol_20d),
            })

        except Exception:
            errors += 1
            continue

    # ── 5. Bilan du filtre ───────────────────────────────────────
    passed = len(results)
    print(f"\n  {'─' * 63}")
    print(f"  📋  Tickers analysés : {total}")
    print(f"  ❌  Filtrés (prix < ${LIQUIDITY_MIN_PRICE:.0f})     : {filtered_price}")
    print(f"  ❌  Filtrés (volume < {LIQUIDITY_MIN_VOL/1e6:.0f}M)   : {filtered_volume}")
    print(f"  ⚠️   Données insuffisantes            : {errors}")
    print(f"  ✅  Éligibles pour classement RS      : {passed}")
    print(f"  {'─' * 63}")

    if not results:
        logger.warning("[SP500Scanner] Aucun ticker éligible après filtrage")
        return [], {}

    # ── 6. Tri par RS décroissant → Top N ────────────────────────
    results.sort(key=lambda x: x["rs_score"], reverse=True)
    top = results[:top_n]

    top_tickers_set = {r["ticker"] for r in top}
    market_data_top = {t: market_data[t] for t in top_tickers_set if t in market_data}

    logger.info(f"[SP500Scanner] Top {top_n} sélectionné(s) sur {passed} éligibles")
    return top, market_data_top


# ─────────────────────────────────────────────────────────────────
# MISE À JOUR DU CACHE MARCHÉ (anti-ban — V15)
# ─────────────────────────────────────────────────────────────────

def update_market_cache(
    tickers: list[str] | None = None,
    period: str = "2y",
    force_refresh: bool = False,
    chunk_size: int = 20,
) -> dict[str, int]:
    """
    Met à jour le cache local data/market_cache/ avec les données yfinance.

    Stratégie anti-ban (2 niveaux de protection) :
      1. Téléchargement par lots de chunk_size tickers via
         yf.download(chunk, threads=False, group_by='ticker')
         → threads=False : 0 conflit SQLite, 0 lock de fichier macOS
      2. Jitter aléatoire time.sleep(random.uniform(1.0, 2.5)) entre lots
         → simule un comportement humain, évite le rate-limiting Yahoo

    Inclut automatiquement le benchmark ^GSPC (nécessaire pour scan_sp500).
    Chaque ticker est sauvegardé en CSV : data/market_cache/<TICKER>.csv.

    Args:
        tickers:       Tickers à mettre à jour. Si None → S&P500 complet + ^GSPC.
        period:        Période yfinance (défaut : "2y" → couvre EMA200 + RS 7 mois).
        force_refresh: Si True, re-télécharge même si le fichier CSV existe déjà.
        chunk_size:    Nombre de tickers par lot (défaut : 20).

    Returns:
        Dict {ticker → nb_barres} pour chaque ticker téléchargé avec succès.
    """
    # ── Récupération de la liste à mettre à jour ─────────────────
    if tickers is None:
        sp500 = get_sp500_tickers()
        if not sp500:
            logger.error("[CacheUpdater] Impossible de récupérer les tickers S&P500")
            return {}
        tickers = [BENCHMARK] + sp500   # ^GSPC en premier pour le benchmark

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    total = len(tickers)

    print(f"\n{'═' * 65}")
    print(f"  🔄  MISE À JOUR DU CACHE — {total} tickers (période : {period})")
    print(f"  🛡️   Anti-ban : lots de {chunk_size} | threads=False | jitter 1.0-2.5s/lot")
    print(f"  📁  Destination : {CACHE_DIR.resolve()}")
    if force_refresh:
        print(f"  ⚡  FORCE REFRESH — tous les fichiers seront re-téléchargés")
    print(f"{'═' * 65}\n")

    summary:        dict[str, int] = {}
    fresh_downloads = 0
    skipped         = 0
    dl_errors       = 0

    # ── Découpe en lots de chunk_size ────────────────────────────
    chunks   = [tickers[i : i + chunk_size] for i in range(0, total, chunk_size)]
    n_chunks = len(chunks)

    for chunk_idx, chunk in enumerate(chunks, 1):

        # Filtrer les tickers déjà en cache (sauf si force_refresh)
        to_download: list[str] = []
        for t in chunk:
            if not force_refresh and _cache_path(t).exists():
                skipped += 1
            else:
                to_download.append(t)

        if not to_download:
            print(
                f"  [{chunk_idx:>3}/{n_chunks}] Lot {chunk_idx:>3} — "
                f"déjà en cache ({len(chunk)} tickers ignorés)",
                flush=True,
            )
            continue

        preview = ", ".join(to_download[:4])
        if len(to_download) > 4:
            preview += f" … (+{len(to_download) - 4})"
        print(
            f"  [{chunk_idx:>3}/{n_chunks}] ⬇️  {len(to_download):>2} tickers : {preview}",
            flush=True,
        )

        try:
            data = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                threads=False,      # IMPÉRATIF : empêche les crashs SQLite macOS
                progress=False,
            )

            if data is None or data.empty:
                logger.warning(f"[CacheUpdater] Lot {chunk_idx} — aucune donnée reçue")
                dl_errors += len(chunk)

            elif isinstance(data.columns, pd.MultiIndex):
                # group_by='ticker' : niveau 0 = ticker, niveau 1 = champ prix
                available = set(data.columns.get_level_values(0).unique())
                for ticker in chunk:
                    if ticker not in available:
                        dl_errors += 1
                        continue
                    try:
                        df_t = data[ticker].dropna(how="all")
                        if df_t is not None and len(df_t) >= 10:
                            df_t.to_csv(_cache_path(ticker))
                            summary[ticker] = len(df_t)
                            fresh_downloads += 1
                        else:
                            dl_errors += 1
                    except Exception:
                        dl_errors += 1

            else:
                # Colonnes plates : lot d'un seul ticker
                if len(chunk) == 1:
                    ticker = chunk[0]
                    df_t   = data.dropna(how="all")
                    if len(df_t) >= 10:
                        df_t.to_csv(_cache_path(ticker))
                        summary[ticker] = len(df_t)
                        fresh_downloads += 1
                    else:
                        dl_errors += 1
                else:
                    logger.warning(
                        f"[CacheUpdater] Lot {chunk_idx} : "
                        f"colonnes plates inattendues pour {len(chunk)} tickers"
                    )
                    dl_errors += len(chunk)

        except Exception as e:
            logger.error(f"[CacheUpdater] Erreur lot {chunk_idx} ({preview}) : {e}")
            dl_errors += len(chunk)

        # ── Jitter anti-ban entre chaque lot ─────────────────────
        if chunk_idx < n_chunks:
            delay = random.uniform(1.0, 2.5)
            print(f"      ⏸️  Pause {delay:.1f}s avant le prochain lot...", flush=True)
            time.sleep(delay)

    # ── Bilan final ───────────────────────────────────────────────
    print(f"\n  {'─' * 63}")
    print(f"  ✅  {fresh_downloads} tickers mis à jour dans le cache")
    print(f"  ⏭️   {skipped} tickers ignorés (cache déjà présent)")
    print(f"  ❌  {dl_errors} erreurs de téléchargement")
    print(f"  {'─' * 63}")
    print(f"  💡  Scan disponible : python main.py --scan-sp500\n")

    logger.info(
        f"[CacheUpdater] Terminé : "
        f"{fresh_downloads} mis à jour | {skipped} ignorés | {dl_errors} erreur(s)"
    )
    return summary


# ─────────────────────────────────────────────────────────────────
# AFFICHAGE CONSOLE — S&P500
# ─────────────────────────────────────────────────────────────────

def print_sp500_ranking(top_results: list[dict]) -> None:
    """
    Affiche le classement Top N du S&P500 Massive Scanner dans la console.

    Args:
        top_results: Sortie de scan_sp500().
    """
    if not top_results:
        print("\n  Aucune action sélectionnée (données insuffisantes).")
        return

    benchmark_pct = top_results[0]["benchmark_return_pct"] if top_results else 0.0

    print(f"\n{'═' * 70}")
    print(f"  🏆  S&P500 MASSIVE SCANNER — TOP {len(top_results)} FORCE RELATIVE (6 MOIS)")
    print(f"  📊  Benchmark S&P500 : {benchmark_pct:+.1f}%  |  "
          f"Filtre : Close > ${LIQUIDITY_MIN_PRICE:.0f} & AvgVol20j > {LIQUIDITY_MIN_VOL/1e6:.0f}M")
    print(f"{'═' * 70}")
    print(f"  {'#':>3}  {'Ticker':<7}  {'Cours':>7}  {'Return':>8}  "
          f"{'vs S&P':>8}  {'RS':>7}  {'AvgVol20j':>12}")
    print(f"  {'─' * 66}")

    for i, r in enumerate(top_results, 1):
        outperf  = r["ticker_return_pct"] - r["benchmark_return_pct"]
        icon     = "🟢" if outperf >= 0 else "🔴"
        vol_str  = f"{r['avg_vol_20d'] / 1_000_000:.1f}M"
        print(
            f"  #{i:2d}  {icon} {r['ticker']:<6}  "
            f"${r['last_close']:>6.2f}  "
            f"{r['ticker_return_pct']:>+7.1f}%  "
            f"{outperf:>+7.1f}%  "
            f"{r['rs_score']:>7.3f}  "
            f"{vol_str:>12}"
        )

    print(f"{'═' * 70}")
    print(
        f"  ✅  Top {len(top_results)} retenus pour scan ADX : "
        f"{', '.join(r['ticker'] for r in top_results)}"
    )
    print()


# ─────────────────────────────────────────────────────────────────
# AFFICHAGE CONSOLE — PORTEFEUILLE CUSTOM
# ─────────────────────────────────────────────────────────────────

def print_portfolio_ranking(results: list[dict], all_results: Optional[list[dict]] = None) -> None:
    """
    Affiche le classement complet du portefeuille dans la console.

    Args:
        results:     Sortie de scan_portfolio() — Top N sélectionnés.
        all_results: Optionnel — tous les tickers classés (pour vue complète).
    """
    display = all_results if all_results else results
    if not display:
        print("\n  Aucune action sélectionnée (données insuffisantes).")
        return

    benchmark_pct = display[0]["benchmark_return_pct"] if display else 0.0
    selected = {r["ticker"] for r in results}

    print(f"\n{'═' * 65}")
    print(f"  🏆  PORTEFEUILLE — CLASSEMENT PAR FORCE RELATIVE (6 MOIS)")
    print(f"  📊  Benchmark S&P500 : {benchmark_pct:+.1f}%")
    print(f"{'═' * 65}")

    for i, r in enumerate(display, 1):
        outperf = r["ticker_return_pct"] - r["benchmark_return_pct"]
        icon    = "🟢" if outperf >= 0 else "🔴"
        sel_tag = " ★ SÉLECTIONNÉ" if r["ticker"] in selected else ""
        print(
            f"  #{i:2d}  {icon} {r['ticker']:6s} | "
            f"Return : {r['ticker_return_pct']:+6.1f}% | "
            f"vs S&P : {outperf:+6.1f}% | "
            f"RS : {r['rs_score']:.3f}"
            f"{sel_tag}"
        )

    print(f"{'═' * 65}")
    if results:
        print(
            f"  ✅  Top {len(results)} retenus pour analyse : "
            f"{', '.join(r['ticker'] for r in results)}"
        )
    print()
