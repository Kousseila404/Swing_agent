"""Module — cache disk fundamentals (Lot 11 hardening data).

Cache mémoire + disque thread-safe pour les `FinancialRatios` retournés par
les providers. **Évite de gaspiller les quotas FMP/yfinance** quand un même
ticker est demandé plusieurs fois sur une fenêtre courte (cron + restart API
+ tests = facilement 3-5 calls/ticker/jour qui retombent dans la quota).

Architecture :

    YFinanceProvider / FMPProvider / FallbackFundamentalProvider
                ↓ (déléguer si miss/expired)
        CachedFundamentalProvider
                ↓ (cache check disk + RAM)
        get_financial_ratios(ticker)

Stockage :
  • Index `data/.fundamentals_cache/index.json.gz` — 1 fichier compact pour
    500 tickers (atomic tmp+rename).
  • TTL 24h par défaut, override possible via constructor.
  • Stale fallback : si un nouveau fetch fail (quota/breaker), on retourne
    le cache même expiré, avec `error=stale_fallback_after_fail`.

Pas de pyarrow / parquet : json.gz suffit pour ~500 tickers × ~600 bytes
~= 300 KB compressés. Ouvert/lu via Read tool si besoin de debug.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from dataclasses import asdict, fields
from typing import Any

from data_providers.base import (
    FinancialRatios,
    FundamentalProviderBase,
    ProviderError,
    ProviderQuotaExceeded,
)
from modules import api_core
from modules.data_validation import sanitize_ratios
from modules.log import logger

# ─────────────────────────────────────────────────────────────────
# CHEMINS + CONSTANTES
# ─────────────────────────────────────────────────────────────────
CACHE_DIR  = api_core.BASE / "data" / ".fundamentals_cache"
CACHE_PATH = CACHE_DIR / "index.json.gz"
CACHE_LOCK_PATH = CACHE_DIR / "index.json.gz.lock"

# TTL par défaut : 24 h. Les fundamentals tournent slowly (quarterly TTM update),
# 1 fetch/jour suffit largement. Cap haut pour le stale fallback : 7 jours.
DEFAULT_TTL_SECONDS = 86_400
STALE_FALLBACK_MAX_AGE_SECONDS = 7 * 86_400

# Bug #8 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-8) — bornes dures sur l'âge du *report fiscal*.
# Le TTL de cache (7j max) protège contre un fetch trop ancien, mais pas contre
# un report fiscal périmé : on peut fetcher AUJOURD'HUI un EPS Q3-2024 et le
# considérer "frais" alors qu'il a 240j. Conséquence : Piotroski Y/Y, Quality,
# Value reposent sur des comparables stale sans warning.
#   • Q-latest > 200 j  (~2 trimestres) → flag REPORT_STALE
#   • Y-1     > 900 j  (publication 10-K + 1 année fiscale) → flag YOY_STALE
# Ces flags sont injectés dans `error` pour propagation au pipeline data_confidence.
#
# Calibration Y-1 (2026-07-23) — 450j déclenchait 482/489 tickers (99 %) de
# l'univers en continu : par construction du cycle annuel, Y-1 (colonne
# annuelle n-1) reste fixe pendant ~12-14 mois entre deux publications de
# Y0, donc son âge oscille naturellement entre ~365j (juste après le
# roll de Y0) et ~820j (juste avant le prochain roll) — jamais sous 450j
# la majeure partie de l'année. Mesuré en prod : p10=569j, max=783j pour
# tout l'univers. 900j laisse une marge de sécurité (~2,5 ans) au-delà du
# cycle annuel normal et ne cible plus que les Y-1 réellement bloqués
# (ticker dont les annuels ne roulent plus du tout, données provider mortes).
PERIOD_END_MAX_AGE_DAYS = 200
PERIOD_END_Y1_MAX_AGE_DAYS = 900


def _now() -> float:
    return time.time()


# ─────────────────────────────────────────────────────────────────
# DISK IO — atomic + RAM mirror
# ─────────────────────────────────────────────────────────────────

class _CacheStore:
    """Storage thread-safe du cache. Mirror RAM + persistance disque atomic."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _load_unlocked(self) -> None:
        """Charge le snapshot disque dans le mirror RAM. Idempotent."""
        if self._loaded:
            return
        if not CACHE_PATH.exists():
            self._loaded = True
            return
        try:
            raw = gzip.decompress(CACHE_PATH.read_bytes())
            data = json.loads(raw)
            if isinstance(data, dict):
                self._mem = data
        except (OSError, json.JSONDecodeError, EOFError) as e:
            logger.warning(
                f"[FundamentalsCache] index corrupted ({e}) — partir d'un cache vide."
            )
            self._mem = {}
        self._loaded = True

    def _persist_unlocked(self) -> None:
        """Écrit le mirror RAM sur disque. Atomic via tmp+rename."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
            tmp.write_bytes(gzip.compress(
                json.dumps(self._mem, ensure_ascii=False).encode("utf-8")
            ))
            tmp.replace(CACHE_PATH)
        except OSError as e:
            logger.warning(f"[FundamentalsCache] persist failed: {e}")

    def get(self, ticker: str) -> dict[str, Any] | None:
        """Retourne l'entrée brute {ratios: {...}, cached_at_epoch: ...} ou None."""
        with self._lock:
            self._load_unlocked()
            return self._mem.get(ticker.upper().strip())

    def put(self, ticker: str, ratios: FinancialRatios) -> None:
        """Met en cache + persist atomic."""
        key = ticker.upper().strip()
        entry = {
            "ratios":          asdict(ratios),
            "cached_at_epoch": _now(),
        }
        with self._lock:
            self._load_unlocked()
            self._mem[key] = entry
            self._persist_unlocked()

    def stats(self) -> dict[str, Any]:
        """Stats diagnostiques pour /api/data_health."""
        now = _now()
        with self._lock:
            self._load_unlocked()
            n = len(self._mem)
            ages = [now - e.get("cached_at_epoch", now) for e in self._mem.values()]
            return {
                "n_cached":         n,
                "oldest_age_sec":   round(max(ages), 1) if ages else None,
                "youngest_age_sec": round(min(ages), 1) if ages else None,
                "median_age_sec":   round(sorted(ages)[len(ages)//2], 1)
                                    if ages else None,
                "cache_path":       str(CACHE_PATH),
            }

    def clear(self) -> None:
        """Reset complet — usage tests."""
        with self._lock:
            self._mem = {}
            self._loaded = True
            try:
                if CACHE_PATH.exists():
                    CACHE_PATH.unlink()
            except OSError:
                pass


# Instance singleton process-wide (= une seule source de vérité par run).
_store = _CacheStore()


def _entry_to_ratios(entry: dict[str, Any]) -> FinancialRatios | None:
    """Désérialise un entry {ratios: {...}} en FinancialRatios.
    Tolère les champs obsolètes (dataclass évolue : on ignore extras silencieusement).
    """
    raw = entry.get("ratios") or {}
    if not isinstance(raw, dict) or not raw.get("ticker"):
        return None
    valid_fields = {f.name for f in fields(FinancialRatios)}
    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    try:
        return FinancialRatios(**filtered)
    except (TypeError, ValueError) as e:
        logger.warning(f"[FundamentalsCache] cannot deserialize entry: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# CACHED PROVIDER WRAPPER
# ─────────────────────────────────────────────────────────────────

class CachedFundamentalProvider(FundamentalProviderBase):
    """Wrapper qui cache les résultats du provider sous-jacent.

    Politique :
      1. Hit cache frais (< ttl) → return cached, source_provider tagged "{name}+cached".
      2. Miss / expired → fetch upstream, met en cache, return.
      3. Upstream raise ProviderError ou crash → return cached SI dispo et < stale_max,
         avec error="stale_fallback_after_fail" → empêche d'écraser universe.json
         avec des None lors d'un breaker temporaire.
    """

    def __init__(
        self,
        upstream: FundamentalProviderBase,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        stale_max_seconds: int = STALE_FALLBACK_MAX_AGE_SECONDS,
    ) -> None:
        self._upstream = upstream
        self._ttl = ttl_seconds
        self._stale_max = stale_max_seconds
        self.name = f"{upstream.name}+cached"

    def get_financial_ratios(self, ticker: str) -> FinancialRatios:
        # 1. Cache hit (frais)
        entry = _store.get(ticker)
        if entry is not None:
            age = _now() - entry.get("cached_at_epoch", 0)
            if age < self._ttl:
                cached = _entry_to_ratios(entry)
                if cached is not None:
                    return cached

        # 2. Tentative fresh fetch
        try:
            fresh = self._upstream.get_financial_ratios(ticker)
        except ProviderQuotaExceeded as e:
            # Quota épuisée → si on a un cache même expiré, le servir en stale.
            return self._stale_or_raise(ticker, e)
        except ProviderError as e:
            return self._stale_or_raise(ticker, e)
        except Exception as e:
            # Crash inattendu → stale plutôt que rien.
            return self._stale_or_raise(ticker, e)

        # Sanitize avant cache : retire les valeurs out-of-bounds (EV/EBITDA
        # négatifs, ROE > 500 %, dividend yield > 25 %, etc.) + cross-check
        # market_cap vs price × shares_outstanding. Évite de polluer le cache
        # 24h avec du bruit yfinance.
        fresh, flags = sanitize_ratios(fresh)
        if flags:
            logger.info(
                f"[FundamentalsCache] {ticker} sanitize flags: {','.join(flags)}"
            )

        # Phase 1 audit (2026-05-06) — PIT consistency check : period_end ne
        # peut pas être dans le futur. Détecte un cache corrompu, un provider
        # qui retourne une date erronée, ou un fuseau horaire bogué.
        _validate_period_end_not_future(ticker, fresh)

        # Fresh OK → cache + return. On NE cache PAS les payloads "vraiment vides"
        # (error set + tous les champs critiques None) — sinon on bloquerait un
        # ticker légitime sur une mauvaise réponse à 24h.
        if not _is_empty_payload(fresh):
            _store.put(ticker, fresh)
        return fresh

    def _stale_or_raise(
        self, ticker: str, original_exc: BaseException,
    ) -> FinancialRatios:
        """Si on a un cache < stale_max, le sert avec flag stale ; sinon raise."""
        entry = _store.get(ticker)
        if entry is None:
            raise original_exc
        age = _now() - entry.get("cached_at_epoch", 0)
        if age >= self._stale_max:
            raise original_exc
        cached = _entry_to_ratios(entry)
        if cached is None:
            raise original_exc
        # Tag explicite : la donnée vient du cache après échec fresh, l'utilisateur
        # doit savoir qu'elle est potentiellement stale.
        cached.source_provider = f"{self.name}/stale"
        cached.error = (
            f"stale_fallback_after_fail (age={int(age/3600)}h, "
            f"reason={type(original_exc).__name__}: {str(original_exc)[:80]})"
        )
        logger.info(
            f"[FundamentalsCache] {ticker} → stale fallback "
            f"(age {age/3600:.1f}h, reason: {type(original_exc).__name__})"
        )
        return cached


def _is_empty_payload(r: FinancialRatios) -> bool:
    """True si toutes les métriques scoring critiques sont None ET error set.
    Évite de mettre en cache des réponses provider entièrement vides."""
    if not r.error:
        return False
    critical = (
        "return_on_equity", "operating_margin", "ev_to_ebitda",
        "free_cash_flow", "debt_to_equity", "current_ratio",
        "recommendation_mean", "price_target_mean",
    )
    return all(getattr(r, f) is None for f in critical)


def _validate_period_end_not_future(ticker: str, r: FinancialRatios) -> None:
    """Filet PIT : si `fundamentals_period_end[_y1]` est dans le futur, on
    annule ces dates (le scoring fallbackera sur le snapshot Y-1) et on log un
    WARNING — c'est typiquement un cache corrompu, un provider buggé ou un
    fuseau horaire mal géré. On ne supprime PAS les ratios eux-mêmes (ils
    peuvent être valides côté provider même si la date métadonnée déraille)
    mais on coupe le levier de Y-1 pour éviter un look-ahead silencieux.

    Bug #8 fix (audit 2026-05-07 — cf. backend/docs/titan/audit_2026-05-07.md#bug-8) — ajoute une borne dure sur l'âge maximum
    du report fiscal (PERIOD_END_MAX_AGE_DAYS / PERIOD_END_Y1_MAX_AGE_DAYS).
    Au-delà → tag dans `error` (propagé à data_confidence.freshness via le
    suffixe "/stale_report" sur source_provider).
    """
    from datetime import date as _date
    today = _date.today()
    stale_flags: list[str] = []
    for field_name, max_age in (
        ("fundamentals_period_end", PERIOD_END_MAX_AGE_DAYS),
        ("fundamentals_period_end_y1", PERIOD_END_Y1_MAX_AGE_DAYS),
    ):
        val = getattr(r, field_name, None)
        if not isinstance(val, str):
            continue
        try:
            pe = _date.fromisoformat(val[:10])
        except ValueError:
            logger.warning(
                f"[FundamentalsCache] {ticker} {field_name}={val!r} unparseable, "
                f"clearing"
            )
            setattr(r, field_name, None)
            continue
        if pe > today:
            logger.warning(
                f"[FundamentalsCache] {ticker} {field_name}={val} is FUTURE "
                f"(today={today.isoformat()}) — clearing to prevent lookahead"
            )
            setattr(r, field_name, None)
            continue
        age_days = (today - pe).days
        if age_days > max_age:
            stale_flags.append(f"{field_name}={age_days}d")
    if stale_flags:
        # Tag explicite : data_confidence + scoring sauront que le report est
        # stale même si le cache est récent. On préserve l'error existante.
        tag = f"report_stale={','.join(stale_flags)}"
        existing = (r.error or "").strip()
        r.error = f"{existing}; {tag}".lstrip("; ") if existing else tag
        # Suffix source_provider pour cohérence avec le motif "/stale" déjà
        # consommé par modules.data_confidence._freshness_factor.
        sp = (r.source_provider or "").strip()
        if "/stale_report" not in sp:
            r.source_provider = f"{sp}/stale_report".lstrip("/") if sp else "stale_report"
        logger.info(
            f"[FundamentalsCache] {ticker} report_stale flagged: {','.join(stale_flags)}"
        )


# ─────────────────────────────────────────────────────────────────
# DIAGNOSTICS PUBLICS — pour /api/data_health
# ─────────────────────────────────────────────────────────────────

def cache_stats() -> dict[str, Any]:
    return _store.stats()


def cache_clear() -> None:
    """Tests + admin manuel : reset complet du cache."""
    _store.clear()


def list_flagged_tickers() -> list[str]:
    """Retourne la liste (triée) des tickers du cache avec au moins un flag
    dq_sanitize=. Utilisé par l'endpoint refresh_flagged.
    """
    with _store._lock:  # noqa: SLF001
        _store._load_unlocked()  # noqa: SLF001
        entries = dict(_store._mem)  # noqa: SLF001
    flagged: list[str] = []
    for ticker, entry in entries.items():
        err = (entry.get("ratios") or {}).get("error") or ""
        if "dq_sanitize=" in err:
            flagged.append(ticker)
    return sorted(flagged)


def list_flagged_tickers_with_tags() -> dict[str, list[str]]:
    """Comme `list_flagged_tickers()` mais garde le détail des tags par
    ticker (ex. {"AAPL": ["out_of_bounds:ev_to_ebitda", "mcap_mismatch"]}).

    Réutilise exactement le même parsing que `cache_sanitize_stats()`
    (`dq_sanitize=tag1|tag2`), juste associé au ticker plutôt que sommé
    globalement. Pour /api/data_health : détail par ticker (Étape 8).
    """
    with _store._lock:  # noqa: SLF001
        _store._load_unlocked()  # noqa: SLF001
        entries = dict(_store._mem)  # noqa: SLF001
    result: dict[str, list[str]] = {}
    for ticker, entry in entries.items():
        err = (entry.get("ratios") or {}).get("error") or ""
        if "dq_sanitize=" not in err:
            continue
        _, _, tag_part = err.partition("dq_sanitize=")
        tag_part = tag_part.split(";", 1)[0]
        tags = [tag.strip() for tag in tag_part.split("|") if tag.strip()]
        if tags:
            result[ticker] = tags
    return dict(sorted(result.items()))


def cache_sanitize_stats() -> dict[str, Any]:
    """Compte les flags dq_sanitize dans les entrées cachées.

    Pour /api/data_health : mesure l'ampleur des valeurs aberrantes rejetées
    en amont. `flags` = {flag_name: count}, `n_tickers_flagged` = tickers
    avec au moins un flag.
    """
    with _store._lock:  # noqa: SLF001  — même module, coupling OK
        _store._load_unlocked()  # noqa: SLF001
        entries = dict(_store._mem)  # noqa: SLF001  — snapshot pour éviter hold
    flags_count: dict[str, int] = {}
    n_flagged = 0
    for entry in entries.values():
        err = (entry.get("ratios") or {}).get("error") or ""
        if "dq_sanitize=" not in err:
            continue
        n_flagged += 1
        # Parse "dq_sanitize=out_of_bounds:ev_to_ebitda|mcap_mismatch"
        _, _, tag_part = err.partition("dq_sanitize=")
        # Coupe au premier ; si l'error a été concaténé avec d'autres messages.
        tag_part = tag_part.split(";", 1)[0]
        for tag in tag_part.split("|"):
            tag = tag.strip()
            if tag:
                flags_count[tag] = flags_count.get(tag, 0) + 1
    return {
        "n_tickers_flagged": n_flagged,
        "flags": dict(sorted(flags_count.items(), key=lambda kv: -kv[1])),
    }


def invalidate_tickers(tickers: list[str]) -> int:
    """Supprime les entrées du cache pour la liste de tickers fournie.
    Le prochain get_financial_ratios refetch fresh depuis l'upstream.

    Retourne le nombre d'entrées effectivement supprimées.
    """
    if not tickers:
        return 0
    wanted = {t.upper().strip() for t in tickers if t}
    removed = 0
    with _store._lock:  # noqa: SLF001
        _store._load_unlocked()  # noqa: SLF001
        for key in list(_store._mem.keys()):  # noqa: SLF001
            if key in wanted:
                del _store._mem[key]  # noqa: SLF001
                removed += 1
        if removed:
            _store._persist_unlocked()  # noqa: SLF001
    return removed
