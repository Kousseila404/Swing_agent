"""SEC EDGAR scraper — Insider transactions (Form 4) + 13F holdings.

Lot 17 — gratuit, illimité, légal. SEC EDGAR expose des endpoints JSON :
  • https://data.sec.gov/submissions/CIK{cik}.json    : tous les filings d'un ticker
  • https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4
  • https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/...

Limites politesse :
  • SEC impose un User-Agent identifiable (email contact).
  • Rate limit officiel : 10 req/sec (très large pour notre usage).
  • Données 100% officielles (10 jours retard maximum).

Ce module produit deux objets :
  • InsiderActivity (par ticker) : net_buys_30d_usd, buy_count_30d, etc.
  • SmartMoney13F (par ticker) : top holders trimestriels.

Cache disque 24h via fundamentals_cache pattern (réutilisable).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request

from data_providers._disk_cache import (
    dir_cache_error_entries,
    dir_cache_stats,
    read_json_cache,
    write_json_cache,
)
from modules.log import logger

_USER_AGENT = "SwingQuant TITAN research@swingquant.local"
_HTTP_TIMEOUT = 10.0
# SEC autorise 10 req/sec. On est très conservateur : 5/sec = 12s pour scanner 60 tickers.
_MIN_DELAY_SECONDS = 0.2

# Cache disque 24h pour insider data (les 10 jours retard SEC autorisent un cache long).
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "sec_cache"
_CACHE_TTL_SECONDS = 24 * 3600

# Mapping ticker → CIK (Central Index Key SEC). On télécharge la table une fois
# par jour depuis l'endpoint officiel SEC.
_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_CIK_MAP_CACHE = _CACHE_DIR / "ticker_to_cik.json"
_CIK_MAP_TTL_SECONDS = 7 * 24 * 3600   # 1 semaine — la map évolue lentement

# Pondération qualitative dans `compute_insider_pillar_score()`.
# Net buys ≥ +500k$ = signal fort, ≥ +2M$ = signal très fort.
_INSIDER_BUY_THRESHOLDS = (
    (5_000_000,  100.0),  # ≥ 5M$ → score 100
    (2_000_000,  85.0),
    (500_000,    70.0),
    (100_000,    55.0),
    (0,          50.0),   # neutre
    (-100_000,   45.0),
    (-500_000,   30.0),
    (-2_000_000, 15.0),
    (-5_000_000, 0.0),    # ≤ -5M$ → score 0
)


_last_call: float = 0.0


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _throttle(min_delay: float = _MIN_DELAY_SECONDS) -> None:
    """Politesse vs SEC rate limit (10 req/sec)."""
    global _last_call
    now = time.time()
    wait = min_delay - (now - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _fetch_json(url: str) -> dict[str, Any] | None:
    """GET JSON SEC. None sur HTTP error / timeout / parse fail."""
    _throttle()
    req = request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = resp.read().decode("utf-8", errors="ignore")
            return json.loads(data)
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError, OSError, ValueError) as e:
        logger.debug(f"[sec_edgar] fetch failed {url[:80]}: {e}")
        return None


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"insider_{ticker.upper()}.json"


def _read_cache(ticker: str) -> dict[str, Any] | None:
    return read_json_cache(_cache_path(ticker), _CACHE_TTL_SECONDS, label="sec_edgar")


def _write_cache(ticker: str, payload: dict[str, Any]) -> None:
    write_json_cache(_cache_path(ticker), payload, label="sec_edgar")


def insider_cache_stats() -> dict[str, Any]:
    """Stats diagnostiques du cache disque insider (Form 4) — pour `/api/data_health`."""
    return dir_cache_stats(_CACHE_DIR, pattern="insider_*.json")


def cik_map_age_sec() -> float | None:
    """Âge en secondes du cache CIK map, ou None si absent."""
    if not _CIK_MAP_CACHE.exists():
        return None
    try:
        return round(time.time() - _CIK_MAP_CACHE.stat().st_mtime, 1)
    except OSError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CIK MAP — ticker → CIK (Central Index Key)
# ─────────────────────────────────────────────────────────────────────────────

def _load_cik_map() -> dict[str, str]:
    """Retourne {TICKER_UPPER: cik_str_padded_10_digits}.

    Cache disque 7 jours. La table SEC pèse ~500 KB → load instantané.
    Fail-open : retourne {} si SEC indisponible.
    """
    cached = read_json_cache(_CIK_MAP_CACHE, _CIK_MAP_TTL_SECONDS, label="sec_edgar")
    if cached is not None:
        return cached.get("map") or {}

    raw = _fetch_json(_CIK_MAP_URL)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    # SEC format : {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        t = (entry.get("ticker") or "").strip().upper()
        cik = entry.get("cik_str")
        if t and cik is not None:
            out[t] = str(cik).zfill(10)
    write_json_cache(_CIK_MAP_CACHE, {"map": out}, label="sec_edgar")
    return out


def ticker_to_cik(ticker: str) -> str | None:
    """Retourne le CIK 10-digit padded ou None si inconnu / SEC down."""
    cik_map = _load_cik_map()
    return cik_map.get(ticker.upper())


# ─────────────────────────────────────────────────────────────────────────────
# Insider activity (Form 4)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InsiderActivity:
    """Snapshot insider activity sur 30/90j pour un ticker."""
    ticker: str
    cik: str | None = None
    net_buys_30d_usd: float = 0.0
    net_buys_90d_usd: float = 0.0
    buy_count_30d: int = 0
    sell_count_30d: int = 0
    buy_count_90d: int = 0
    sell_count_90d: int = 0
    distinct_insiders_buying_30d: int = 0
    cluster_buying: bool = False  # 3+ insiders bullish dans 7j
    most_recent_filing_date: str | None = None
    most_recent_8k_date: str | None = None  # dernier événement matériel SEC
    most_recent_10k_date: str | None = None  # dernier rapport annuel SEC
    most_recent_10q_date: str | None = None  # dernier rapport trimestriel SEC
    n_filings_scanned: int = 0
    n_form4_parsed_90d: int = 0     # Form 4 dont le XML a été lu
    n_form4_unparsed_90d: int = 0   # échecs réseau / budget → non comptés
    parser_version: int = 2         # 1 = heuristique « tout Form 4 = achat »
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_form4_value(filing_summary: dict[str, Any]) -> tuple[float, str | None]:
    """Heuristique extraction du montant net buy/sell d'un Form 4.

    SEC EDGAR `submissions/CIK{cik}.json` ne donne PAS le détail des
    transactions Form 4 (faut parser le XML brut). Pour un compromis vitesse/
    couverture acceptable, on lit l'index `recent.primaryDocument` puis on
    classe par "form" et "isXBRL".

    Pour la prod réelle, on utilisera l'API SEC `/api/xbrl/companyfacts` ou
    le service tiers OpenInsider (scraping pré-parsé). Ici on retourne une
    structure minimale "filing existed at this date" pour chaque ligne.
    Enrichissement profond dans une 2ème itération.
    """
    # Stub : retourne (0.0, None). Le vrai parsing Form 4 XML est trop
    # lourd pour ce module ; on s'appuie sur les compteurs simples.
    return 0.0, None


_FILINGS_CACHE_DIR = _CACHE_DIR / "filings"
_FILINGS_CACHE_TTL = 6 * 3600  # 6h — les 10-K/Q/8-K sont moins volatils


def _filings_cache_path(ticker: str) -> Path:
    return _FILINGS_CACHE_DIR / f"{ticker.upper()}.json"


def _read_filings_cache(ticker: str) -> dict[str, Any] | None:
    return read_json_cache(
        _filings_cache_path(ticker), _FILINGS_CACHE_TTL,
        use_mtime=True, label="sec_edgar",
    )


def _write_filings_cache(ticker: str, payload: dict[str, Any]) -> None:
    write_json_cache(
        _filings_cache_path(ticker), payload, stamp=False, label="sec_edgar",
    )


def filings_cache_stats() -> dict[str, Any]:
    """Stats diagnostiques du cache disque filings — pour `/api/data_health`."""
    return dir_cache_stats(_FILINGS_CACHE_DIR)


def filings_error_tickers() -> list[dict[str, Any]]:
    """Liste `{ticker, error}` des tickers en échec du cache filings (signal
    `sec_fetch_failed`/`cik_unknown` persisté depuis l'Étape 16) — pour
    `/api/data_health` (Étape 18). Nom de fichier `{TICKER}.json`.
    """
    return dir_cache_error_entries(_FILINGS_CACHE_DIR)


def _archive_url(cik: str, accession: str, primary_doc: str | None) -> str:
    """Build canonical SEC EDGAR archive URL.

    accession format : 0000320193-25-000001 → folder = 000032019325000001
    """
    folder = (accession or "").replace("-", "")
    base = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40"
    if folder and primary_doc:
        # Direct doc URL.
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{primary_doc}"
    if folder:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/"
    return base


# Forms d'intérêt long-terme : annuels/trimestriels + 8-K (événements
# matériels) + Form 4 (insider) + 13F (smart money).
_DEFAULT_FORMS = (
    "10-K", "10-K/A",
    "10-Q", "10-Q/A",
    "8-K",  "8-K/A",
    "4",    "4/A",
    "13F-HR",
    "DEF 14A",  # proxy statement (gouvernance)
    "S-1",      # IPO
    "20-F",     # foreign annual
)

_FORM_LABELS = {
    "10-K":   "Rapport annuel",
    "10-K/A": "Rapport annuel (amendé)",
    "10-Q":   "Rapport trimestriel",
    "10-Q/A": "Rapport trimestriel (amendé)",
    "8-K":    "Événement matériel",
    "8-K/A":  "Événement matériel (amendé)",
    "4":      "Insider transaction",
    "4/A":    "Insider transaction (amendée)",
    "13F-HR": "Smart money holdings",
    "DEF 14A":"Proxy statement",
    "S-1":    "IPO prospectus",
    "20-F":   "Annual report (foreign)",
}


def fetch_recent_filings(
    ticker: str,
    *,
    forms: tuple[str, ...] = _DEFAULT_FORMS,
    limit: int = 30,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Liste les N derniers filings SEC d'un ticker, filtrés par forme.

    Output :
      {
        "ticker": "AAPL",
        "cik":    "0000320193",
        "filings": [
          {form, form_label, date, accession, url, days_ago}, ...
        ],
        "n_filings": int,
        "error": str | None,
      }

    Cache disque 6h (cache séparé du cache insider).
    """
    t = (ticker or "").upper().strip()
    if not t:
        return {"ticker": "", "filings": [], "n_filings": 0, "error": "empty ticker"}

    if use_cache:
        cached = _read_filings_cache(t)
        if cached is not None:
            return cached

    cik = ticker_to_cik(t)
    if not cik:
        out: dict[str, Any] = {"ticker": t, "cik": None, "filings": [],
               "n_filings": 0, "error": "cik_unknown"}
        _write_filings_cache(t, out)
        return out

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    payload = _fetch_json(url)
    if not isinstance(payload, dict):
        out = {"ticker": t, "cik": cik, "filings": [],
               "n_filings": 0, "error": "sec_fetch_failed"}
        _write_filings_cache(t, out)
        return out

    recent = payload.get("filings", {}).get("recent") or {}
    rec_forms     = recent.get("form")             or []
    rec_dates     = recent.get("filingDate")       or []
    rec_acc       = recent.get("accessionNumber")  or []
    rec_primary   = recent.get("primaryDocument")  or []

    today = datetime.utcnow().date()
    out_filings: list[dict[str, Any]] = []

    forms_set = set(forms)
    for form, dstr, acc, primary in zip(
        rec_forms, rec_dates, rec_acc, rec_primary, strict=False,
    ):
        if form not in forms_set:
            continue
        try:
            d = datetime.strptime(dstr, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        out_filings.append({
            "form":       form,
            "form_label": _FORM_LABELS.get(form, form),
            "date":       dstr,
            "accession":  acc,
            "url":        _archive_url(cik, acc, primary),
            "days_ago":   (today - d).days,
        })
        if len(out_filings) >= limit:
            break

    out = {
        "ticker":    t,
        "cik":       cik,
        "filings":   out_filings,
        "n_filings": len(out_filings),
        "error":     None,
    }
    _write_filings_cache(t, out)
    return out



# ─────────────────────────────────────────────────────────────────────────────
# Form 4 — parsing XML (audit 2026-11-18 P0 #3)
# ─────────────────────────────────────────────────────────────────────────────
# Avant : chaque Form 4 était compté comme un ACHAT et le « filer_id » était
# le préfixe de l'accession (= agent de dépôt, pas l'insider) → 26 % de
# l'univers en « cluster buying ». On lit désormais le XML : seules les
# transactions codées « P » (achat sur le marché) sont des achats, « S » des
# ventes ; l'insider est identifié par `rptOwnerCik`. Les octrois (A), RSU
# (M), impôts (F), dons (G)… ne comptent ni comme achat ni comme vente.
# Un Form 4 publié est immuable → cache disque permanent par accession.

_FORM4_CACHE_DIR = _CACHE_DIR / "form4"
_FORM4_MAX_PER_TICKER = 60  # borne réseau par appel (les plus récents d'abord)


def _fetch_text(url: str) -> str | None:
    _throttle()
    req = request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="ignore")
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError, OSError, ValueError) as e:
        logger.debug(f"[sec_edgar] fetch failed {url[:80]}: {e}")
        return None


def parse_form4_xml(xml_text: str) -> dict[str, Any] | None:
    """Extrait {owners, buy_shares, buy_usd, sell_shares, sell_usd, codes} d'un
    Form 4 XML. None si le document est illisible."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8"))
    except ET.ParseError:
        return None

    def _txt(node: Any, path: str) -> str | None:
        el = node.find(path)
        return el.text.strip() if el is not None and el.text else None

    owners = sorted({
        (_txt(o, "reportingOwnerId/rptOwnerCik") or "").lstrip("0")
        for o in root.findall("reportingOwner")
    } - {""})
    out: dict[str, Any] = {
        "owners": owners, "buy_shares": 0.0, "buy_usd": 0.0,
        "sell_shares": 0.0, "sell_usd": 0.0, "codes": [],
    }
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _txt(tx, "transactionCoding/transactionCode")
        if not code:
            continue
        out["codes"].append(code)
        sh = _safe_float(_txt(tx, "transactionAmounts/transactionShares/value")) or 0.0
        px = _safe_float(_txt(tx, "transactionAmounts/transactionPricePerShare/value")) or 0.0
        if code == "P":
            out["buy_shares"] += sh
            out["buy_usd"] += sh * px
        elif code == "S":
            out["sell_shares"] += sh
            out["sell_usd"] += sh * px
    return out


def _form4_detail(cik: str, accession: str, primary_doc: str | None) -> dict[str, Any] | None:
    """Détail d'un Form 4 (cache permanent par accession). None si échec réseau."""
    if not accession or not primary_doc:
        return None
    cache = _FORM4_CACHE_DIR / f"{accession}.json"
    try:
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    # `primaryDocument` = « xslF345X06/form4.xml » (rendu HTML) → le XML brut
    # est le même nom de fichier à la racine du dossier d'accession.
    doc = primary_doc.split("/")[-1]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"
    xml_text = _fetch_text(url)
    if xml_text is None:
        return None
    detail = parse_form4_xml(xml_text)
    if detail is None:
        detail = {"owners": [], "buy_shares": 0.0, "buy_usd": 0.0,
                  "sell_shares": 0.0, "sell_usd": 0.0, "codes": [], "unparsable": True}
    try:
        _FORM4_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(detail), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        pass
    return detail


def fetch_insider_activity(ticker: str, *, use_cache: bool = True) -> InsiderActivity:
    """Récupère l'activité insider depuis SEC EDGAR submissions API.

    Depuis l'audit 2026-11-18 (P0 #3), chaque Form 4 des 90 derniers jours
    est lu (XML, cache permanent par accession) : achats = code « P »,
    ventes = code « S », insiders distincts = `rptOwnerCik`, montants nets
    en USD. Un Form 4 illisible n'est compté ni comme achat ni comme vente.

    Args:
      ticker: ticker upper.
      use_cache: si True, lit le cache 24h.

    Returns:
      InsiderActivity (jamais None ; .error renseigné si SEC fail).
    """
    ticker = ticker.upper()
    if use_cache:
        cached = _read_cache(ticker)
        if cached is not None:
            try:
                cached.pop("_cached_at", None)
                if int(cached.get("parser_version") or 1) >= 2:
                    return InsiderActivity(**cached)
            except TypeError:
                pass  # schéma changé → re-fetch

    cik = ticker_to_cik(ticker)
    if not cik:
        result = InsiderActivity(ticker=ticker, error="cik_unknown")
        _write_cache(ticker, result.to_dict())
        return result

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    payload = _fetch_json(url)
    if not isinstance(payload, dict):
        result = InsiderActivity(ticker=ticker, cik=cik, error="sec_fetch_failed")
        _write_cache(ticker, result.to_dict())
        return result

    recent = payload.get("filings", {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accession_nums = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or [None] * len(forms)

    today = datetime.utcnow().date()
    cutoff_30 = today - timedelta(days=30)
    cutoff_90 = today - timedelta(days=90)

    buy_30 = sell_30 = 0
    buy_90 = sell_90 = 0
    insiders_30: set[str] = set()
    insiders_90: set[str] = set()
    # Pour cluster : on track [(date, filer_id)] sur 90j, et on cherche
    # une fenêtre 7j contenant ≥ 3 filers DISTINCTS (pas 3 filings).
    filings_with_filer: list[tuple[datetime, str]] = []
    net_usd_30 = net_usd_90 = 0.0
    n_parsed = n_unparsed = 0
    most_recent: str | None = None
    most_recent_8k: str | None = None
    most_recent_10k: str | None = None
    most_recent_10q: str | None = None
    n_form4 = 0

    # SEC retourne tous les filings, déjà en mémoire pour ce payload — on
    # extrait aussi la date du 8-K/10-K/10-Q le plus récent (événement
    # matériel / rapports périodiques) sans requête réseau supplémentaire,
    # en plus du filtre Form 4 (transactions).
    for form, dstr, acc, pdoc in zip(forms, dates, accession_nums, primary_docs, strict=False):
        if form in ("8-K", "8-K/A"):
            try:
                datetime.strptime(dstr, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if most_recent_8k is None or dstr > most_recent_8k:
                most_recent_8k = dstr
            continue
        if form in ("10-K", "10-K/A"):
            try:
                datetime.strptime(dstr, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if most_recent_10k is None or dstr > most_recent_10k:
                most_recent_10k = dstr
            continue
        if form in ("10-Q", "10-Q/A"):
            try:
                datetime.strptime(dstr, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if most_recent_10q is None or dstr > most_recent_10q:
                most_recent_10q = dstr
            continue
        if form not in ("4", "4/A"):
            continue
        try:
            d = datetime.strptime(dstr, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        n_form4 += 1
        if most_recent is None or dstr > most_recent:
            most_recent = dstr
        if d >= cutoff_90:
            # Audit 2026-11-18 P0 #3 : on lit le XML (cf. parse_form4_xml).
            # SEC renvoie les filings du plus récent au plus ancien → le
            # budget _FORM4_MAX_PER_TICKER garde les plus récents.
            if n_parsed + n_unparsed >= _FORM4_MAX_PER_TICKER:
                n_unparsed += 1
                continue
            detail = _form4_detail(cik, str(acc), pdoc)
            if detail is None or detail.get("unparsable"):
                n_unparsed += 1
                continue
            n_parsed += 1
            owners = detail.get("owners") or []
            bought = float(detail.get("buy_shares") or 0) > 0
            sold = float(detail.get("sell_shares") or 0) > 0
            net = float(detail.get("buy_usd") or 0) - float(detail.get("sell_usd") or 0)
            net_usd_90 += net
            if bought:
                buy_90 += 1
                for o in owners:
                    insiders_90.add(o)
                    filings_with_filer.append((datetime.combine(d, datetime.min.time()), o))
            if sold:
                sell_90 += 1
            if d >= cutoff_30:
                net_usd_30 += net
                if bought:
                    buy_30 += 1
                    insiders_30.update(owners)
                if sold:
                    sell_30 += 1

    # Détection cluster RIGOUREUSE : ≥ 3 INSIDERS DISTINCTS dans une fenêtre 7j
    # roulante. Évite le faux positif "1 insider exerce 5 options en 1 semaine".
    cluster = False
    if len(filings_with_filer) >= 3:
        filings_with_filer.sort(key=lambda x: x[0])
        for i, (anchor_dt, _) in enumerate(filings_with_filer):
            window_end = anchor_dt + timedelta(days=7)
            distinct_filers_in_window = {
                fid for dt, fid in filings_with_filer[i:] if dt <= window_end
            }
            if len(distinct_filers_in_window) >= 3:
                cluster = True
                break

    result = InsiderActivity(
        ticker=ticker,
        cik=cik,
        net_buys_30d_usd=round(net_usd_30, 2),
        net_buys_90d_usd=round(net_usd_90, 2),
        buy_count_30d=buy_30,
        sell_count_30d=sell_30,
        buy_count_90d=buy_90,
        sell_count_90d=sell_90,
        distinct_insiders_buying_30d=len(insiders_30),
        cluster_buying=cluster,
        most_recent_filing_date=most_recent,
        most_recent_8k_date=most_recent_8k,
        most_recent_10k_date=most_recent_10k,
        most_recent_10q_date=most_recent_10q,
        n_filings_scanned=n_form4,
        n_form4_parsed_90d=n_parsed,
        n_form4_unparsed_90d=n_unparsed,
    )
    _write_cache(ticker, result.to_dict())
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Pilier Insider — scoring 0-100
# ─────────────────────────────────────────────────────────────────────────────

def compute_insider_pillar_score(activity: InsiderActivity) -> dict[str, Any]:
    """Convertit InsiderActivity → score 0-100 + components diag.

    Heuristique sans montants USD (en attendant parsing Form 4 XML / OpenInsider) :
      • base = 50 (neutre)
      • +10 si buy_count_30d ≥ 3
      • +15 si distinct_insiders_buying_30d ≥ 3
      • +10 si cluster_buying
      • +5 si buy_count_90d ≥ 8
      • -10 si sell_count_30d > buy_count_30d × 2 (heavy selling)

    Le score reste dans [0, 100].
    """
    if activity.error:
        return {
            "score": 50.0,
            "components": {},
            "data_quality": 0.0,
            "reason": activity.error,
        }

    # Audit 2026-11-18 P0 #3 — recalibré sur les vrais achats (code P du
    # Form 4). Les achats sur le marché par plusieurs dirigeants sont le seul
    # signal insider documenté ; les ventes sont surtout routinières (plans
    # 10b5-1, impôts sur RSU) → aucune pénalité. Sans achat : neutre (50).
    score = 50.0
    components: dict[str, Any] = {
        "buy_count_30d": activity.buy_count_30d,
        "sell_count_30d": activity.sell_count_30d,
        "buy_count_90d": activity.buy_count_90d,
        "distinct_insiders_30d": activity.distinct_insiders_buying_30d,
        "cluster_buying": activity.cluster_buying,
        "net_buys_90d_usd": activity.net_buys_90d_usd,
        "parser_version": activity.parser_version,
    }
    n_buyers = activity.distinct_insiders_buying_30d
    if n_buyers >= 3:
        score += 15.0
    elif n_buyers == 2:
        score += 8.0
    elif n_buyers == 1:
        score += 4.0
    if activity.cluster_buying:
        score += 10.0
    if activity.buy_count_90d >= 1 and activity.net_buys_90d_usd >= 500_000:
        score += 5.0

    score = max(0.0, min(100.0, score))
    n_tried = activity.n_form4_parsed_90d + activity.n_form4_unparsed_90d
    if n_tried > 0:
        dq = activity.n_form4_parsed_90d / n_tried
    else:
        dq = min(1.0, activity.n_filings_scanned / 5.0)
    return {
        "score": round(score, 2),
        "components": components,
        "data_quality": round(dq, 3),
        "n_filings": activity.n_filings_scanned,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 13F Holdings (smart money — Berkshire, Burry, etc.)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SmartMoney13F:
    """Snapshot 13F holders pour un ticker — top institutional positions."""
    ticker: str
    top_holders: list[dict[str, Any]] = field(default_factory=list)
    most_recent_quarter: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Hedge funds "smart money" qu'on tracke. CIK officiels SEC.
# (À étendre par config / .env si besoin — ici un panier de référence.)
SMART_MONEY_FUNDS: dict[str, str] = {
    "Berkshire Hathaway":        "0001067983",
    "Scion Asset Management":    "0001649339",  # Michael Burry
    "Pershing Square":           "0001336528",  # Bill Ackman
    "Greenlight Capital":        "0001079114",  # David Einhorn
    "Baupost Group":             "0001061768",  # Seth Klarman
    "Appaloosa Management":      "0001656456",  # David Tepper
    "Third Point":               "0001040273",  # Daniel Loeb
    "Renaissance Technologies":  "0001037389",
}


def fetch_13f_holdings_for_fund(fund_cik: str) -> dict[str, Any] | None:
    """Récupère le dernier 13F-HR d'un fund. Retourne le filing JSON brut.

    SEC EDGAR `submissions/CIK{cik}.json` liste les filings ; on cherche le
    plus récent type "13F-HR" et on retourne son metadata.

    Note : le CONTENU détaillé (positions par ticker) n'est pas dans le JSON
    submissions — il faut parser le XML/HTML du filing primaryDocument.
    Pour une 1ère version on retourne juste la liste des filings 13F dispo.
    Le parsing détaillé sera ajouté quand on aura besoin (UI Smart Money).
    """
    url = f"https://data.sec.gov/submissions/CIK{fund_cik}.json"
    payload = _fetch_json(url)
    if not isinstance(payload, dict):
        return None
    recent = payload.get("filings", {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    for form, dstr, acc, doc in zip(forms, dates, accs, docs, strict=False):
        if form == "13F-HR":
            return {
                "form": form,
                "filing_date": dstr,
                "accession_number": acc,
                "primary_document": doc,
                "cik": fund_cik,
            }
    return None
