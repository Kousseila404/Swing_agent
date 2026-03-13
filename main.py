#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  SWING QUANT V8/V11 — LE TITAN — POINT D'ENTRÉE                ║
║                                                                  ║
║  [LIVE]                                                          ║
║    python main.py                         Scan + Telegram        ║
║    python main.py --dry-run               Scan sans Telegram     ║
║    python main.py --ticker NVDA           Un seul ticker (live)  ║
║                                                                  ║
║  [PORTEFEUILLE — V8 End-of-Day]                                 ║
║    python main.py --portfolio             Top 3 RS + scan Daily  ║
║    python main.py --portfolio NVDA,TSLA,AAPL,MSFT  Liste custom ║
║    python main.py --portfolio --dry-run   Sans Telegram          ║
║                                                                  ║
║  [MASSIVE SCANNER — S&P500]                                     ║
║    python main.py --scan-sp500            Top 10 RS S&P500 complet║
║    python main.py --scan-sp500 --dry-run  Sans Telegram          ║
║                                                                  ║
║  [MISE À JOUR DU CACHE — Anti-ban]                              ║
║    python main.py --update-cache          S&P500 complet (lots/20)║
║    python main.py --update-cache --force-refresh  Force re-DL   ║
║                                                                  ║
║  [BACKTEST CLASSIQUE — V8]                                      ║
║    python main.py --backtest              Tous (5 ans)           ║
║    python main.py --backtest --ticker NVDA  Un seul ticker       ║
║    python main.py --backtest --years 3    N années               ║
║                                                                  ║
║  [BACKTEST VECTORISÉ + OPTIMISATION — V11]                      ║
║    python main.py --vector-backtest       Optuna 20 trials       ║
║    python main.py --vector-backtest --trials 50  50 trials       ║
║                                                                  ║
║  [OPTIMISATION WALK-FORWARD]                                     ║
║    python main.py --optimize              Tous les tickers       ║
║    python main.py --optimize --ticker TSLA  Un seul ticker       ║
║    python main.py --optimize --no-ai      Sans appel IA          ║
║                                                                  ║
║  [RAPPORT]                                                       ║
║    python main.py --report                Console + HTML         ║
║    python main.py --report --no-html      Console seulement      ║
║                                                                  ║
║  [TIERS]                                                         ║
║    python main.py --tiers                 Classement par tiers   ║
║                                                                  ║
║  [MACRO]                                                         ║
║    python main.py --macro                 Régime macro actuel    ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys
import os
import argparse
from datetime import datetime
from typing import List, Optional

from modules.log import logger
from modules.scanner import scan_universe, _analyze_ticker, ScanResult
from modules.news_fetcher import fetch_news, format_news_for_llm
from modules.ia_brain import analyze_signal
from modules.alerter import send_alert, send_summary
import config


# ─────────────────────────────────────────────────────────────────
# PIPELINE LIVE (scan → news → IA → Telegram)
# ─────────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False, single_ticker: Optional[str] = None) -> None:
    """
    Orchestre le pipeline live complet V5 :
      1. Vérification du régime macro (MacroEngine)
      2. Scan de l'univers multi-actifs (US Tech + Crypto)
      3. Enrichissement par les actualités (NewsAPI)
      4. Analyse IA (Claude / LLM)
      5. Envoi des alertes Telegram

    Args:
        dry_run:       Si True, affiche les résultats sans envoyer Telegram.
        single_ticker: Si fourni, analyse ce seul ticker.
    """
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"🚀 SWING QUANT V5 — Lancement {start_time:%Y-%m-%d %H:%M:%S}")
    logger.info(f"   Mode : {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 60)

    # ── Affichage du régime macro en début de pipeline ────────────
    try:
        from modules.macro_engine import get_market_regime
        regime = get_market_regime()
        regime_icons = {
            "BULL_MARKET":  "🟢 BULL MARKET  — Signaux LONG en recherche",
            "BEAR_MARKET":  "🔴 BEAR MARKET  — Signaux SHORT en recherche",
            "CRASH_PANIC":  "🚨 CRASH/PANIC  — Trading BLOQUÉ (VIX ≥ 35)",
        }
        logger.info(f"📊 Régime Macro : {regime_icons.get(regime, regime)}")
    except Exception as e:
        logger.warning(f"MacroEngine indisponible ({e})")
        regime = "UNKNOWN"

    logger.info("📡 ÉTAPE 1/4 — Scan de l'univers d'actions...")

    if single_ticker:
        end = datetime.now()
        result = _analyze_ticker(single_ticker, end)
        signals = [result] if result else []
        if not signals:
            logger.info(f"[{single_ticker}] Aucun signal (conditions non réunies ou régime défavorable)")
    else:
        signals = scan_universe()

    total_scanned = 1 if single_ticker else len(config.TICKERS_FLAT)

    if not signals:
        logger.info("💤 Aucun signal détecté. Fin du pipeline.")
        if not dry_run:
            send_summary(total_scanned=total_scanned, signals_found=0, alerts_sent=0)
        return

    alerts_sent = 0
    for i, scan in enumerate(signals, 1):
        logger.info(f"\n{'─' * 40}")
        logger.info(
            f"📌 Signal {i}/{len(signals)} : {scan.ticker} "
            f"[{'🟢 LONG' if scan.direction == 'LONG' else '🔴 SHORT'}]"
        )

        logger.info("📰 ÉTAPE 2/4 — Récupération des actualités...")
        news_items = fetch_news(scan.ticker)
        news_text  = format_news_for_llm(scan.ticker, news_items)

        logger.info("🧠 ÉTAPE 3/4 — Analyse par le cerveau IA...")
        verdict = analyze_signal(scan, news_text)

        if dry_run:
            logger.info("📋 ÉTAPE 4/4 — Mode DRY RUN — affichage console uniquement")
            _print_console_alert(scan, verdict)
        else:
            logger.info("📲 ÉTAPE 4/4 — Envoi de l'alerte Telegram...")
            if send_alert(scan, verdict):
                alerts_sent += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\n{'=' * 60}")
    logger.info(
        f"✅ Pipeline terminé en {elapsed:.1f}s — "
        f"{len(signals)} signal(aux), {alerts_sent} alerte(s) envoyée(s)"
    )
    logger.info("=" * 60)

    if not dry_run:
        send_summary(
            total_scanned=total_scanned,
            signals_found=len(signals),
            alerts_sent=alerts_sent,
        )


def _print_console_alert(scan: ScanResult, verdict) -> None:
    """Affiche un résumé dans les logs (mode dry-run)."""
    dir_icon = "🟢" if scan.direction == "LONG" else "🔴"
    logger.info("━" * 50)
    logger.info(f"  🏷️  {scan.ticker} — {scan.name}")
    logger.info(f"  {dir_icon} Direction : {scan.direction}")
    logger.info(f"  💰  Prix : {scan.price} ({scan.change_pct:+.2f}%)")
    logger.info(f"  📊  Volume : {scan.volume:,} ({scan.volume_ratio}x moyenne)")
    logger.info(f"  📈  RSI : {scan.rsi} → Signal : {scan.signal}")
    if verdict:
        logger.info(f"  🧠  IA : {verdict.sentiment} (confiance {verdict.confidence}%)")
        logger.info(f"  🎯  Action : {verdict.action} | Risque : {verdict.risk_level}")
        logger.info(f"  💬  {verdict.reasoning}")
    else:
        logger.info("  🧠  IA : Analyse indisponible")
    logger.info("━" * 50)


# ─────────────────────────────────────────────────────────────────
# MODE MACRO (NOUVEAU V5)
# ─────────────────────────────────────────────────────────────────

def run_macro_mode() -> None:
    """
    Affiche le régime macro-économique actuel avec toutes les métriques :
    S&P500, EMA200, VIX, régime, directions autorisées.
    """
    from modules.macro_engine import get_regime_details

    logger.info("=" * 60)
    logger.info("📊 MODE MACRO — Régime Macro-Économique Actuel")
    logger.info("=" * 60)

    try:
        details = get_regime_details()
        regime  = details.get("regime", "INCONNU")
        emoji   = details.get("emoji", "⚪")

        delta   = details.get("sp500_vs_ema200_pct")
        allowed = details.get("allowed_directions", [])
        logger.info("═" * 55)
        logger.info(f"  {emoji}  RÉGIME MACRO : {regime}")
        logger.info("═" * 55)
        logger.info(f"  S&P 500    : {details.get('sp500', 'N/A'):>12}")
        logger.info(f"  EMA 200j   : {details.get('ema200', 'N/A'):>12}")
        logger.info(f"  Δ EMA200   : {f'{delta:+.1f}%' if delta is not None else 'N/A':>12}")
        logger.info(f"  VIX        : {details.get('vix', 'N/A'):>12}")
        logger.info(f"  Directions : {', '.join(allowed) if allowed else 'AUCUNE (CRASH PANIC)':>12}")
        logger.info(f"  ℹ️  {details.get('description', '')}")
        logger.info("═" * 55)

    except Exception as e:
        logger.error(f"Erreur MacroEngine : {e}")


# ─────────────────────────────────────────────────────────────────
# MODE PORTEFEUILLE (V8 — End-of-Day + Relative Strength)
# ─────────────────────────────────────────────────────────────────

def run_portfolio_mode(
    tickers: Optional[List[str]] = None,
    dry_run: bool = False,
    top_n: int = 3,
) -> None:
    """
    Moteur de portefeuille End-of-Day V8 :
      1. Calcul de la Force Relative vs S&P500 (6 mois) pour chaque ticker
      2. Sélection du Top N les plus forts
      3. Scan Daily avec routage ADX (Chandelier Momentum / Mean Reversion)
      4. Enrichissement news + analyse IA + alertes Telegram

    Args:
        tickers:  Liste de tickers à analyser. Si None, utilise DEFAULT_PORTFOLIO.
        dry_run:  Si True, affiche les résultats sans envoyer Telegram.
        top_n:    Nombre de tickers retenus après filtre Relative Strength.
    """
    from modules.portfolio_scanner import scan_portfolio, print_portfolio_ranking, DEFAULT_PORTFOLIO
    from modules.scanner import _analyze_ticker, ScanResult
    from modules.macro_engine import get_market_regime

    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"🏆 SWING QUANT V8 — MODE PORTEFEUILLE — {start_time:%Y-%m-%d %H:%M:%S}")
    logger.info(f"   Mode : {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 60)

    # ── Régime macro ──────────────────────────────────────────────
    try:
        regime = get_market_regime()
        regime_icons = {
            "BULL_MARKET":  "🟢 BULL MARKET",
            "BEAR_MARKET":  "🔴 BEAR MARKET",
            "CRASH_PANIC":  "🚨 CRASH/PANIC — Trading BLOQUÉ",
        }
        logger.info(f"📊 Régime Macro : {regime_icons.get(regime, regime)}")
        if regime == "CRASH_PANIC":
            logger.critical("🚨 CRASH_PANIC — Portfolio mode annulé. VIX ≥ 35.")
            return
    except Exception as e:
        logger.warning(f"MacroEngine indisponible ({e})")
        regime = "BULL_MARKET"

    universe = tickers if tickers else DEFAULT_PORTFOLIO
    logger.info(f"📋 Univers : {len(universe)} tickers — {', '.join(universe)}")

    # ── Étape 1 : Force Relative → Top N ─────────────────────────
    logger.info("📡 ÉTAPE 1/4 — Calcul Force Relative vs S&P500 (6 mois)...")
    all_rs   = scan_portfolio(universe, top_n=len(universe))   # tous classés
    top_rs   = all_rs[:top_n]
    print_portfolio_ranking(top_rs, all_rs)

    if not top_rs:
        logger.warning("⚠️  Aucun ticker valide — fin du pipeline.")
        return

    top_tickers = [r["ticker"] for r in top_rs]
    logger.info(f"✅ Top {top_n} retenus : {', '.join(top_tickers)}")

    # ── Étape 2 : Scan Daily ADX sur le Top N ────────────────────
    logger.info(f"📡 ÉTAPE 2/4 — Scan Daily (ADX routing) sur {len(top_tickers)} tickers...")
    end_date = datetime.now()
    signals: list[ScanResult] = []

    for t in top_tickers:
        try:
            result = _analyze_ticker(t, end_date, regime=regime)
            if result:
                signals.append(result)
            else:
                logger.info(f"[{t}] Aucun signal (conditions non réunies)")
        except Exception as e:
            logger.warning(f"[{t}] Erreur scan : {e}")

    if not signals:
        logger.info("💤 Aucun signal détecté dans le Top portefeuille.")
        if not dry_run:
            send_summary(
                total_scanned=len(top_tickers),
                signals_found=0,
                alerts_sent=0,
            )
        return

    # ── Étapes 3 & 4 : News + IA + Alertes ───────────────────────
    alerts_sent = 0
    for i, scan in enumerate(signals, 1):
        logger.info(f"\n{'─' * 40}")
        logger.info(
            f"📌 Signal {i}/{len(signals)} : {scan.ticker} "
            f"[{'🟢 LONG' if scan.direction == 'LONG' else '🔴 SHORT'}] "
            f"[{scan.signal}]"
        )

        logger.info("📰 ÉTAPE 3/4 — Récupération des actualités...")
        news_items = fetch_news(scan.ticker)
        news_text  = format_news_for_llm(scan.ticker, news_items)

        logger.info("🧠 ÉTAPE 4/4 — Analyse IA...")
        verdict = analyze_signal(scan, news_text)

        if dry_run:
            _print_console_alert(scan, verdict)
        else:
            if send_alert(scan, verdict):
                alerts_sent += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\n{'=' * 60}")
    logger.info(
        f"✅ Portfolio pipeline terminé en {elapsed:.1f}s — "
        f"Top {top_n} | {len(signals)} signal(aux) | {alerts_sent} alerte(s)"
    )
    logger.info("=" * 60)

    if not dry_run:
        send_summary(
            total_scanned=len(top_tickers),
            signals_found=len(signals),
            alerts_sent=alerts_sent,
        )


# ─────────────────────────────────────────────────────────────────
# MODE MASSIVE SCANNER — S&P500 COMPLET (V8.5)
# ─────────────────────────────────────────────────────────────────

def run_scan_sp500_mode(dry_run: bool = False, top_n: int = 10) -> None:
    """
    Massive Market Scanner V15 — S&P500 complet (~503 tickers) :
      1. Scraping Wikipedia → liste S&P500 à jour
      2. Lecture seule du cache local data/market_cache/*.csv
         (0 appel yfinance en masse — 0 ban IP — 0 SQLite lock)
      3. Filtre liquidité institutionnelle (Close > $10, AvgVol20j > 2M)
      4. Classement Force Relative vs S&P500 (6 mois) → Top 10
      5. Scan Daily ADX (Chandelier Momentum / Mean Reversion) sur le Top 10
      6. Enrichissement news + analyse IA + alertes Telegram

    Pour mettre à jour le cache avant le scan :
        python main.py --update-cache

    Args:
        dry_run: Si True, affiche les résultats sans envoyer Telegram.
        top_n:   Nombre d'actions à retenir après classement RS (défaut : 10).
    """
    from modules.portfolio_scanner import scan_sp500, print_sp500_ranking
    from modules.scanner import _analyze_ticker, ScanResult
    from modules.macro_engine import get_market_regime

    start_time = datetime.now()
    logger.info("=" * 65)
    logger.info(f"🔍 SWING QUANT V8.5 — MASSIVE SCANNER S&P500 — {start_time:%Y-%m-%d %H:%M:%S}")
    logger.info(f"   Mode : {'DRY RUN' if dry_run else 'LIVE'}  |  Top N : {top_n}")
    logger.info("=" * 65)

    # ── Régime macro ──────────────────────────────────────────────
    try:
        regime = get_market_regime()
        regime_icons = {
            "BULL_MARKET":  "🟢 BULL MARKET — Signaux LONG en recherche",
            "BEAR_MARKET":  "🔴 BEAR MARKET — Top 10 RS calculé (SHORT & valeurs défensives)",
            "CRASH_PANIC":  "🚨 CRASH/PANIC — Top 10 RS calculé (alertes informationnelles)",
        }
        logger.info(f"📊 Régime Macro : {regime_icons.get(regime, regime)}")
        # Note : le Massive Scanner tourne dans TOUS les régimes, y compris BEAR et CRASH.
        # En BEAR : permet de trouver les rares valeurs qui surperforment (SHORT ou défensif).
        # En CRASH_PANIC : mode informatif — pas d'envoi Telegram si dry_run forcé.
    except Exception as e:
        logger.warning(f"MacroEngine indisponible ({e})")
        regime = "BULL_MARKET"

    # ── Étape 1 : Massive Scanner → Top N ────────────────────────
    logger.info("📡 ÉTAPE 1/4 — Massive Scanner : S&P500 complet → Top RS...")
    top_rs, market_data = scan_sp500(top_n=top_n)
    print_sp500_ranking(top_rs)

    if not top_rs:
        logger.warning("⚠️  Aucun ticker retenu — fin du pipeline.")
        return

    top_tickers = [r["ticker"] for r in top_rs]
    logger.info(f"✅ Top {top_n} retenus : {', '.join(top_tickers)}")

    # ── Étape 2 : Scan Daily ADX sur le Top N ────────────────────
    # Les DataFrames sont réutilisés depuis market_data → 0 re-téléchargement
    logger.info(f"📡 ÉTAPE 2/4 — Scan Daily ADX sur {len(top_tickers)} tickers (données réutilisées)...")
    end_date = datetime.now()
    signals: list[ScanResult] = []

    for t in top_tickers:
        try:
            result = _analyze_ticker(t, end_date, regime=regime, df=market_data.get(t))
            if result:
                signals.append(result)
            else:
                logger.info(f"[{t}] Aucun signal (conditions non réunies)")
        except Exception as e:
            logger.warning(f"[{t}] Erreur scan : {e}")

    if not signals:
        logger.info("💤 Aucun signal détecté dans le Top S&P500.")
        if not dry_run:
            send_summary(
                total_scanned=len(top_tickers),
                signals_found=0,
                alerts_sent=0,
            )
        return

    # ── Étapes 3 & 4 : News + IA + Alertes ───────────────────────
    alerts_sent = 0
    for i, scan in enumerate(signals, 1):
        logger.info(f"\n{'─' * 40}")
        logger.info(
            f"📌 Signal {i}/{len(signals)} : {scan.ticker} "
            f"[{'🟢 LONG' if scan.direction == 'LONG' else '🔴 SHORT'}] "
            f"[{scan.signal}]"
        )

        logger.info("📰 ÉTAPE 3/4 — Récupération des actualités...")
        news_items = fetch_news(scan.ticker)
        news_text  = format_news_for_llm(scan.ticker, news_items)

        logger.info("🧠 ÉTAPE 4/4 — Analyse IA...")
        verdict = analyze_signal(scan, news_text)

        if dry_run:
            _print_console_alert(scan, verdict)
        else:
            if send_alert(scan, verdict):
                alerts_sent += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\n{'=' * 65}")
    logger.info(
        f"✅ Massive Scanner terminé en {elapsed:.1f}s — "
        f"Top {top_n} S&P500 | {len(signals)} signal(aux) | {alerts_sent} alerte(s)"
    )
    logger.info("=" * 65)

    if not dry_run:
        send_summary(
            total_scanned=len(top_tickers),
            signals_found=len(signals),
            alerts_sent=alerts_sent,
        )


# ─────────────────────────────────────────────────────────────────
# MODE MISE À JOUR DU CACHE (anti-ban — V15)
# ─────────────────────────────────────────────────────────────────

def run_update_cache_mode(force_refresh: bool = False) -> None:
    """
    Télécharge les données 2 ans pour le S&P500 complet (~503 tickers + ^GSPC)
    et les sauvegarde dans data/market_cache/<TICKER>.csv.

    Stratégie anti-ban :
      - User-Agent Chrome macOS via requests.Session
      - Lots de 20 tickers avec yf.download(threads=False)
      - Jitter aléatoire 1.0-2.5 s entre chaque lot

    Args:
        force_refresh: Si True, re-télécharge même si le fichier CSV existe déjà.
    """
    from modules.portfolio_scanner import update_market_cache

    logger.info("=" * 65)
    logger.info(
        f"🔄 MISE À JOUR CACHE — "
        f"{'FORCE REFRESH' if force_refresh else 'Nouveaux tickers uniquement'}"
    )
    logger.info("=" * 65)

    update_market_cache(force_refresh=force_refresh)


# ─────────────────────────────────────────────────────────────────
# MODE BACKTEST
# ─────────────────────────────────────────────────────────────────

def run_backtest_mode(single_ticker: Optional[str] = None, years: int = 3) -> None:
    """
    Backteste un ou tous les tickers avec leurs paramètres (globaux ou profils).
    """
    from modules.backtester import run_backtest
    from modules.backtest_report import print_ticker_report, print_portfolio_report, generate_html_report
    from modules.ticker_profiles import get_backtest_params

    # Utilise TICKERS_FLAT pour itérer sur la liste aplatie de l'univers multi-actifs
    tickers = [single_ticker] if single_ticker else config.TICKERS_FLAT

    logger.info("=" * 60)
    logger.info(f"📊 MODE BACKTEST — {len(tickers)} ticker(s) — {years} an(s) d'historique")
    logger.info("=" * 60)

    results = []
    t0 = datetime.now()

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{len(tickers)}] {ticker} — backtest en cours...")
        try:
            params = get_backtest_params(ticker)
            result = run_backtest(ticker=ticker, params=params, years=years, keep_df=False)
            results.append(result)

            if result.error:
                logger.warning(f"  → {result.error}")
            elif result.stats and result.stats.total_trades > 0:
                s = result.stats
                dir_info = f" | Régime : {result.regime}" if result.regime != "UNKNOWN" else ""
                logger.info(
                    f"  → {s.total_trades} trades | WR {s.win_rate:.0%} | "
                    f"Return {s.total_return_pct:+.1f}% | DD {s.max_drawdown_pct:.1f}%"
                    f"{dir_info}"
                )
            else:
                logger.info(f"  → Aucun signal généré (Régime : {result.regime})")

        except Exception as e:
            logger.error(f"[{ticker}] Erreur backtest : {e}")

    logger.info(f"\nBacktest terminé en {(datetime.now() - t0).total_seconds():.1f}s")

    if not results:
        logger.warning("Aucun résultat à afficher.")
        return

    if single_ticker:
        print_ticker_report(results[0], show_trades=True)
    else:
        print_portfolio_report(results)
        html_path = generate_html_report(results)
        logger.info(f"📄 Rapport HTML : {html_path}")


# ─────────────────────────────────────────────────────────────────
# MODE OPTIMISATION
# ─────────────────────────────────────────────────────────────────

def run_optimize_mode(single_ticker: Optional[str] = None, use_ai: bool = True) -> None:
    """
    Optimisation walk-forward sur un ou tous les tickers.
    """
    from modules.strategy_optimizer import optimize_ticker
    from modules.ai_strategy_advisor import advise_ticker
    from modules.ticker_profiles import save_profile

    tickers = [single_ticker] if single_ticker else config.TICKERS_FLAT

    logger.info("=" * 60)
    logger.info(f"⚙️  MODE OPTIMISATION — {len(tickers)} ticker(s)")
    logger.info(f"   IA advisor : {'Activé' if use_ai else 'Désactivé'}")
    logger.info("=" * 60)

    profiles_saved = 0
    t0 = datetime.now()

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"\n[{i}/{len(tickers)}] ── {ticker} {'─' * 40}")
        try:
            opt_result = optimize_ticker(ticker=ticker, verbose=True)

            if opt_result.error:
                logger.warning(f"[{ticker}] Erreur optimisation : {opt_result.error}")
                continue

            profile = advise_ticker(opt_result, use_ai=use_ai)

            if opt_result.avg_val_stats:
                profile["avg_val_stats"] = opt_result.avg_val_stats.to_dict()

            if save_profile(ticker, profile):
                profiles_saved += 1
                logger.info(
                    f"[{ticker}] ✅ Profil sauvegardé — "
                    f"Tier {profile.get('tier', 'N/A')} | "
                    f"Stratégie : {profile.get('strategy_type', 'N/A')} | "
                    f"Confiance : {profile.get('confidence_score', 0)}%"
                )

        except KeyboardInterrupt:
            logger.info("\n⚠️  Optimisation interrompue (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"[{ticker}] Erreur optimisation : {e}")

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"\n{'=' * 60}")
    logger.info(
        f"⚙️  Terminé en {elapsed:.1f}s — "
        f"{profiles_saved}/{len(tickers)} profils sauvegardés dans data/profiles.json"
    )
    logger.info("=" * 60)


# ─────────────────────────────────────────────────────────────────
# MODE RAPPORT
# ─────────────────────────────────────────────────────────────────

def run_report_mode(generate_html: bool = True) -> None:
    """
    Rejoue un backtest sur tous les tickers et génère un rapport complet.
    """
    from modules.backtester import run_backtest
    from modules.backtest_report import print_portfolio_report, generate_html_report
    from modules.ticker_profiles import get_backtest_params

    logger.info("=" * 60)
    logger.info("📄 MODE RAPPORT — Backtest avec profils optimisés")
    logger.info("=" * 60)

    results = []
    for i, ticker in enumerate(config.TICKERS_FLAT, 1):
        logger.info(f"[{i}/{len(config.TICKERS_FLAT)}] {ticker}...")
        try:
            params = get_backtest_params(ticker)
            result = run_backtest(ticker=ticker, params=params, years=3, keep_df=False)
            results.append(result)
        except Exception as e:
            logger.error(f"[{ticker}] Erreur : {e}")

    if not results:
        logger.warning("Aucun résultat à afficher.")
        return

    print_portfolio_report(results)

    if generate_html:
        html_path = generate_html_report(results)
        logger.info(f"📄 Rapport HTML : {html_path}")


# ─────────────────────────────────────────────────────────────────
# MODE BACKTEST VECTORISÉ V11 (vectorbt + Optuna)
# ─────────────────────────────────────────────────────────────────

def run_vector_backtest_mode(n_trials: int = 20) -> None:
    """
    Moteur de Backtest Vectorisé V11 — MOMENTUM_DIP sur S&P500 complet.

    Strictement séparé de la logique Live :
      - Aucun appel à scanner, alerter, ia_brain, telegram.
      - Lit uniquement les données du market_cache local (fichiers .pkl).

    Pipeline :
      1. Chargement des DataFrames OHLCV depuis data/market_cache/
      2. Optimisation Optuna (n_trials) : ADX ∈ [15,30], RSI ∈ [30,50]
      3. Backtest vectorbt sur les meilleurs paramètres
      4. Métriques QuantStats (pas PyFolio, incompatible Python 3.12)
      5. Rapport HTML QuantStats → data/reports/v11_backtest_YYYYMMDD.html

    Garanties anti-look-ahead bias :
      - Signaux décalés de +1 barre (shift(1)) → exécution simulée à J+1
      - Aucune donnée future utilisée dans le calcul des indicateurs

    Args:
        n_trials: Nombre d'essais Optuna pour l'optimisation (défaut: 20)
    """
    import quantstats as qs
    import warnings
    warnings.filterwarnings("ignore")

    from modules.backtester.vector_engine import load_cache_data, run_optimization
    from datetime import datetime

    start_time = datetime.now()

    logger.info("═" * 65)
    logger.info("  SWING QUANT V11 — BACKTEST VECTORISÉ + OPTIMISATION OPTUNA")
    logger.info(f"  Démarrage   : {start_time:%Y-%m-%d %H:%M:%S}")
    logger.info("  Mode        : Isolation totale (AUCUN appel Live / Telegram)")
    logger.info(f"  Trials      : {n_trials} essais Optuna (TPE Sampler, seed=42)")
    logger.info("  Reporting   : QuantStats (pas PyFolio — compatible Python 3.12)")
    logger.info("═" * 65)

    # ── 1. Chargement du cache ──────────────────────────────────────
    logger.info("[1/4] Chargement du market_cache local...")
    try:
        data = load_cache_data()
    except FileNotFoundError as exc:
        logger.error(f"Cache introuvable : {exc}")
        return

    if not data:
        logger.error("Aucun ticker valide trouvé dans le cache. Lancez --scan-sp500 d'abord.")
        return

    # ── 2. Optimisation Optuna ──────────────────────────────────────
    logger.info(f"[2/4] Optimisation Optuna ({n_trials} trials)...")
    logger.info("      Objectif : Maximiser le Sharpe Ratio QuantStats (S&P500 complet)")

    try:
        best_metrics, study = run_optimization(data=data, n_trials=n_trials)
    except Exception as exc:
        logger.error(f"Erreur lors de l'optimisation : {exc}", exc_info=True)
        return

    elapsed_opt = (datetime.now() - start_time).total_seconds()

    # ── 3. Affichage console des résultats ─────────────────────────
    logger.info("[3/4] Résultats — Backtest MOMENTUM_DIP V11 (QuantStats)")

    def _fmt(v, pct=False, sign=False) -> str:
        if v != v or not (v == v):     # NaN check
            return "N/A"
        if pct:
            return f"{v * 100:+.2f}%" if sign else f"{v * 100:.2f}%"
        return f"{v:+.3f}" if sign else f"{v:.3f}"

    bm = best_metrics

    logger.info("╔" + "═" * 57 + "╗")
    logger.info("║  MEILLEURS PARAMÈTRES TROUVÉS PAR OPTUNA (7D)           ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info(f"║  param_ema       (période EMA tendance) : {bm['param_ema']:>5}           ║")
    logger.info(f"║  param_adx       (seuil ADX)            : {bm['param_adx']:>8.2f}        ║")
    logger.info(f"║  param_rsi       (seuil RSI pullback)   : {bm['param_rsi']:>8.2f}        ║")
    logger.info(f"║  param_sl        (stop loss trailing %) : {bm['param_sl']:>8.2%}        ║")
    logger.info(f"║  param_tp        (take profit %)        : {bm['param_tp']:>8.2%}        ║")
    logger.info(f"║  param_time_stop (barres avant sortie)  : {bm['param_time_stop']:>5}           ║")
    logger.info(f"║  param_pos_size  (% cash par trade)     : {bm['param_pos_size']:>8.2%}        ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info("║  MÉTRIQUES QUANTSTATS — Portefeuille equal-weight       ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info(f"║  Sharpe Ratio  (annualisé)      : {_fmt(bm['sharpe'], sign=True):>10}           ║")
    logger.info(f"║  Sortino Ratio (annualisé)      : {_fmt(bm['sortino'], sign=True):>10}           ║")
    logger.info(f"║  CAGR          (annualisé)      : {_fmt(bm['cagr'], pct=True, sign=True):>10}           ║")
    logger.info(f"║  Total Return                   : {_fmt(bm['total_return'], pct=True, sign=True):>10}           ║")
    logger.info(f"║  Max Drawdown                   : {_fmt(bm['max_drawdown'], pct=True):>10}           ║")
    logger.info(f"║  Calmar Ratio                   : {_fmt(bm['calmar'], sign=True):>10}           ║")
    logger.info(f"║  Profit Factor                  : {_fmt(bm['profit_factor']):>10}           ║")
    logger.info(f"║  Omega Ratio                    : {_fmt(bm['omega']):>10}           ║")
    logger.info(f"║  Volatilité   (annualisée)      : {_fmt(bm['volatility'], pct=True):>10}           ║")
    logger.info(f"║  Win Rate     (% jours positifs): {_fmt(bm['win_rate'], pct=True):>10}           ║")
    logger.info(f"║  Win Rate     (% trades gagnants): {_fmt(bm['trade_wr'], pct=True):>9}           ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info("║  PARAMÈTRES DE SIMULATION                               ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info(f"║  Tickers analysés               : {bm['n_tickers']:>10}           ║")
    logger.info(f"║  Trades exécutés                : {bm['n_trades']:>10}           ║")
    logger.info("║  Frais de courtage              :      0.1% / ordre           ║")
    logger.info("║  Anti-Look-Ahead                :  shift(1) → exécution J+1  ║")
    logger.info(f"║  Durée optimisation             : {elapsed_opt:>8.1f}s                 ║")
    logger.info("╚" + "═" * 57 + "╝")

    # ── Top 5 trials Optuna (God Fitness) ──────────────────────────
    logger.info("  TOP 5 TRIALS OPTUNA (God Fitness = CAGR² / |MaxDD|) :")
    logger.info(f"  {'#':>3}  {'EMA':>4}  {'ADX':>5}  {'RSI':>5}  {'SL':>5}  {'TP':>5}  {'T':>3}  {'POS':>5}  {'GodFit':>8}")
    logger.info("  " + "─" * 62)
    top_trials = sorted(study.trials, key=lambda t: t.value or -999, reverse=True)[:5]
    for i, trial in enumerate(top_trials, 1):
        v = trial.value if trial.value is not None else float("nan")
        p = trial.params
        v_s = f"{v:.4f}" if v == v and abs(v) < 1e6 else " -1.000"
        logger.info(
            f"  {i:>3}  "
            f"{p.get('param_ema', 0):>4}  "
            f"{p.get('param_adx', 0):>5.1f}  "
            f"{p.get('param_rsi', 0):>5.1f}  "
            f"{p.get('param_sl', 0):>5.2f}  "
            f"{p.get('param_tp', 0):>5.2f}  "
            f"{p.get('param_time_stop', 0):>3}  "
            f"{p.get('param_pos_size', 0):>5.2f}  "
            f"{v_s:>8}"
        )

    # ── 4. Rapport HTML QuantStats ─────────────────────────────────
    logger.info("[4/4] Génération du rapport HTML QuantStats...")
    portfolio_returns = best_metrics.get("portfolio_returns")

    html_path = None
    if portfolio_returns is not None and len(portfolio_returns) > 10:
        try:
            reports_dir = os.path.join(os.path.dirname(__file__), "data", "reports")
            os.makedirs(reports_dir, exist_ok=True)
            html_filename = f"v11_backtest_{start_time:%Y%m%d_%H%M%S}.html"
            html_path = os.path.join(reports_dir, html_filename)

            title = (
                f"MOMENTUM_DIP V11 | EMA={bm['param_ema']} "
                f"ADX={bm['param_adx']:.1f} RSI={bm['param_rsi']:.1f} "
                f"SL={bm['param_sl']:.0%} TP={bm['param_tp']:.0%} "
                f"T={bm['param_time_stop']} | {start_time:%Y-%m-%d}"
            )
            qs.reports.html(
                portfolio_returns,
                output    = html_path,
                title     = title,
                download_filename = html_filename,
            )
            logger.info(f"  Rapport HTML sauvegardé → {html_path}")
        except Exception as exc:
            logger.warning(f"  Impossible de générer le rapport HTML : {exc}")
    else:
        logger.info("  (Pas assez de données pour le rapport HTML)")

    # ── Métriques QuantStats complètes dans la console ─────────────
    if portfolio_returns is not None and len(portfolio_returns) > 10:
        logger.info("─── RAPPORT QUANTSTATS COMPLET ──────────────────────────────")
        try:
            qs.reports.metrics(portfolio_returns, display=True, mode="full")
        except Exception as exc:
            logger.warning(f"  qs.reports.metrics() échoué : {exc}")

    elapsed_total = (datetime.now() - start_time).total_seconds()
    logger.info(f"  Durée totale : {elapsed_total:.1f}s")


# ─────────────────────────────────────────────────────────────────
# MODE TIERS
# ─────────────────────────────────────────────────────────────────

def run_tiers_mode() -> None:
    """
    Affiche le classement par Tiers de tous les tickers profilés.
    """
    from modules.backtest_report import print_tiers_report
    logger.info("=" * 60)
    logger.info("🏆 MODE TIERS — Classement des tickers profilés")
    logger.info("=" * 60)
    print_tiers_report()


# ─────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE CLI
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Swing Quant V5 — Le Titan | Scanner IA + Moteur Long/Short + Filtre Macro",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  ── MACRO ──────────────────────────────────────────────────────
  python main.py --macro                  Affiche le régime macro actuel

  ── PORTEFEUILLE (V8 End-of-Day) ───────────────────────────────
  python main.py --portfolio              Top 3 RS par défaut + scan Daily
  python main.py --portfolio NVDA,TSLA,AAPL,MSFT  Liste custom (Top 3 RS)
  python main.py --portfolio --dry-run    Sans Telegram

  ── MISE À JOUR DU CACHE (à faire en premier) ──────────────────
  python main.py --update-cache           Télécharge S&P500 complet (anti-ban)
  python main.py --update-cache --force-refresh  Force re-téléchargement intégral

  ── MASSIVE SCANNER S&P500 (V15 — lecture seule) ───────────────
  python main.py --scan-sp500             Cache → filtre liq. → Top 10 RS → ADX → IA
  python main.py --scan-sp500 --dry-run   Sans Telegram

  ── LIVE ───────────────────────────────────────────────────────
  python main.py                          Scan complet + alertes Telegram
  python main.py --dry-run                Scan complet sans Telegram
  python main.py --ticker NVDA            Analyse un seul ticker

  ── BACKTEST CLASSIQUE (V8) ────────────────────────────────────
  python main.py --backtest               Backtest tous les tickers (5 ans)
  python main.py --backtest --ticker TSLA Backtest un seul ticker
  python main.py --backtest --years 3     Backtest sur 3 ans

  ── BACKTEST VECTORISÉ + OPTUNA (V11) ─────────────────────────
  python main.py --vector-backtest        Optimise ADX/RSI (20 trials)
  python main.py --vector-backtest --trials 50  Plus précis (50 trials)

  ── OPTIMISATION ───────────────────────────────────────────────
  python main.py --optimize               Optimise + IA pour tous les tickers
  python main.py --optimize --ticker MSTR Optimise un seul ticker
  python main.py --optimize --no-ai       Optimise sans appel IA (plus rapide)

  ── RAPPORT ────────────────────────────────────────────────────
  python main.py --report                 Rapport console + HTML
  python main.py --report --no-html       Rapport console seulement
        """,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--portfolio", nargs="?", const="DEFAULT", metavar="TICKERS",
        help=(
            "Mode portefeuille V8 End-of-Day. "
            "Sans argument → utilise DEFAULT_PORTFOLIO (Top 3 RS). "
            "Avec liste comma-séparée → ex: --portfolio NVDA,TSLA,AAPL,MSFT,AMZN"
        ),
    )
    mode_group.add_argument(
        "--scan-sp500", action="store_true",
        help=(
            "Massive Scanner V8.5 : S&P500 complet (~503 tickers) → "
            "Filtre liquidité → Top 10 RS → Scan ADX → IA → Telegram"
        ),
    )
    mode_group.add_argument(
        "--backtest", action="store_true",
        help="Lance le backtesting (params actuels ou profils optimisés)",
    )
    mode_group.add_argument(
        "--optimize", action="store_true",
        help="Lance l'optimisation walk-forward et génère les profils IA",
    )
    mode_group.add_argument(
        "--report", action="store_true",
        help="Génère un rapport de backtest global (console + HTML)",
    )
    mode_group.add_argument(
        "--vector-backtest", action="store_true",
        help=(
            "Backtest Vectorisé V11 : charge le market_cache, optimise ADX/RSI "
            "via Optuna (--trials N), affiche Sharpe / Drawdown / Win Rate"
        ),
    )
    mode_group.add_argument(
        "--tiers", action="store_true",
        help="Affiche le classement des tickers par Tier (A/B/C/UNTRADABLE)",
    )
    mode_group.add_argument(
        "--macro", action="store_true",
        help="Affiche le régime macro-économique actuel (S&P500 / VIX)",
    )
    mode_group.add_argument(
        "--update-cache", action="store_true",
        help=(
            "Mise à jour anti-ban du cache marché (data/market_cache/*.csv) : "
            "S&P500 complet + ^GSPC | lots de 20 | threads=False | jitter 1.0-2.5s. "
            "Utilisez --force-refresh pour re-télécharger même si le cache existe."
        ),
    )

    parser.add_argument("--ticker", type=str, default=None,
                        help="Cible un seul ticker (ex: NVDA, BTC-USD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="(Live) Affiche sans envoyer sur Telegram")
    parser.add_argument("--years", type=int, default=5,
                        help="(Backtest) Nombre d'années d'historique (défaut: 5)")
    parser.add_argument("--no-ai", action="store_true",
                        help="(Optimisation) Sans appel à l'IA Anthropic")
    parser.add_argument("--no-html", action="store_true",
                        help="(Rapport) Désactive la génération HTML")
    parser.add_argument("--force-refresh", action="store_true",
                        help="(update-cache) Force le re-téléchargement même si le fichier CSV cache existe déjà")
    parser.add_argument("--trials", type=int, default=20,
                        help="(vector-backtest) Nombre de trials Optuna (défaut: 20)")

    args = parser.parse_args()

    try:
        if args.portfolio is not None:
            # Décodage de la liste de tickers (ou utilisation du défaut)
            if args.portfolio == "DEFAULT":
                portfolio_tickers = None
            else:
                portfolio_tickers = [t.strip().upper() for t in args.portfolio.split(",") if t.strip()]
            run_portfolio_mode(
                tickers=portfolio_tickers,
                dry_run=args.dry_run,
            )
        elif args.scan_sp500:
            run_scan_sp500_mode(dry_run=args.dry_run)
        elif getattr(args, "update_cache", False):
            run_update_cache_mode(force_refresh=args.force_refresh)
            sys.exit(0)
        elif args.macro:
            run_macro_mode()
        elif args.backtest:
            run_backtest_mode(single_ticker=args.ticker, years=args.years)
        elif getattr(args, "vector_backtest", False):
            run_vector_backtest_mode(n_trials=args.trials)
        elif args.optimize:
            run_optimize_mode(single_ticker=args.ticker, use_ai=not args.no_ai)
        elif args.report:
            run_report_mode(generate_html=not args.no_html)
        elif args.tiers:
            run_tiers_mode()
        else:
            run_pipeline(dry_run=args.dry_run, single_ticker=args.ticker)

    except KeyboardInterrupt:
        logger.info("⚠️  Arrêt demandé par l'utilisateur (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"💀 Erreur fatale non gérée : {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
