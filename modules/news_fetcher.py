"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE 2 — NEWS FETCHER                                       ║
║  Récupère les dernières actualités d'un ticker via             ║
║  le flux RSS Google News (stdlib uniquement).                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

from modules.log import logger


@dataclass
class NewsItem:
    """Structure propre pour une actualité."""
    title: str
    summary: str
    source: str
    published: str       # Date de publication en texte
    link: str


def fetch_news(ticker_symbol: str, max_items: int = 3) -> list[NewsItem]:
    """
    Récupère les dernières actualités pour un ticker via Google News RSS.
    Retourne les articles de moins de 7 jours (max_items au plus).
    Retourne une liste vide si aucune news n'est trouvée.
    """
    query = urllib.parse.quote_plus(f"{ticker_symbol} stock financial news")
    url = (
        f"https://news.google.com/rss/search"
        f"?q={query}&hl=en-US&gl=US&ceid=US:en"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    news_items: list[NewsItem] = []

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        channel = root.find("channel")
        if channel is None:
            logger.warning(f"[{ticker_symbol}] Flux RSS Google News vide ou malformé")
            return news_items

        for item in channel.findall("item"):
            if len(news_items) >= max_items:
                break

            title = (item.findtext("title") or "").strip()
            if not title:
                continue

            # Filtrage par date (7 jours)
            pub_date_str = item.findtext("pubDate") or ""
            try:
                pub_dt = parsedate_to_datetime(pub_date_str)
                if pub_dt < cutoff:
                    continue
                published = pub_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                published = pub_date_str or "Date inconnue"

            link = (item.findtext("link") or "").strip()

            # La source est dans l'attribut de la balise <source>
            source_el = item.find("source")
            source = (
                source_el.text.strip()
                if source_el is not None and source_el.text
                else "Google News"
            )

            summary = (item.findtext("description") or "").strip()
            # La description Google News peut contenir du HTML — on nettoie sommairement
            if summary.startswith("<"):
                summary = ""

            news_items.append(
                NewsItem(
                    title=title,
                    summary=summary[:500] if summary else "Pas de résumé disponible.",
                    source=source,
                    published=published,
                    link=link,
                )
            )

        if news_items:
            logger.info(
                f"[{ticker_symbol}] {len(news_items)} news récupérée(s) via Google News RSS"
            )
        else:
            logger.info(f"[{ticker_symbol}] Aucune news récente via Google News RSS")

    except Exception as e:
        logger.warning(f"[{ticker_symbol}] Erreur Google News RSS : {e}")

    return news_items


def format_news_for_llm(ticker_symbol: str, news_items: list[NewsItem]) -> str:
    """
    Formate les news en texte lisible pour le prompt LLM.
    """
    if not news_items:
        return "Aucune actualité récente."

    parts = [f"Actualités récentes pour {ticker_symbol} :"]
    for i, news in enumerate(news_items, 1):
        parts.append(
            f"\n--- News {i} ---\n"
            f"Titre : {news.title}\n"
            f"Source : {news.source} ({news.published})\n"
            f"Résumé : {news.summary}"
        )
    return "\n".join(parts)
