"""
Fetch public macro / policy sources (RSS + optional HTML), extract text, optional LLM structuring.

Uses stdlib only for HTTP. Respect ``FORTRESS_AI_DOMAIN_WEB_*`` env toggles and caps.
"""
from __future__ import annotations

import os
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from knowledge.domain_knowledge import _repo_root
from knowledge.learning_engine import LearningEngine


def _web_enabled() -> bool:
    return str(os.environ.get("FORTRESS_AI_DOMAIN_WEB_INGEST", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _web_llm_enabled() -> bool:
    return str(os.environ.get("FORTRESS_AI_DOMAIN_WEB_LLM", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def default_source_urls() -> list[str]:
    raw = (os.environ.get("FORTRESS_AI_DOMAIN_WEB_SOURCES") or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()][:12]
    return [
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://www.sec.gov/news/pressreleases.rss",
    ]


def fetch_url(url: str, *, timeout: float = 22.0) -> tuple[int | None, str, str | None]:
    """Return (status_code, body_text, error)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Fortress-AI-DomainIntel/1.0 (+https://github.com/n8nkho/fortress-ai)",
            "Accept": "application/rss+xml, application/xml, text/xml, text/html, */*;q=0.8",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
            charset = (resp.headers.get_content_charset() or "utf-8") if resp.headers else "utf-8"
            text = raw.decode(charset, errors="replace")
            return int(code or 0), text, None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, str(e)[:200]
    except Exception as e:
        return None, "", str(e)[:240]


def _strip_tags(html: str, max_chars: int = 14000) -> str:
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_chars]


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_rss_items(xml: str, *, limit: int = 6) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out

    def grab_item(el: ET.Element) -> dict[str, str] | None:
        title = link = desc = ""
        for ch in list(el):
            ln = _local_name(ch.tag).lower()
            txt = "".join(ch.itertext()).strip()
            if ln == "title" and txt:
                title = txt
            elif ln == "link" and txt:
                link = txt
            elif ln in ("description", "summary", "content") and txt and not desc:
                desc = _strip_tags(txt, max_chars=1200)
        if not title and not desc:
            return None
        return {"title": title[:500], "link": link[:500], "summary": desc[:1200]}

    for el in root.iter():
        if _local_name(el.tag).lower() == "item":
            row = grab_item(el)
            if row:
                out.append(row)
            if len(out) >= limit:
                break
    return out


def ingest_once(
    *,
    root: Path | None = None,
    max_items_per_source: int = 4,
    max_sources: int = 6,
) -> dict[str, Any]:
    """Fetch configured URLs; RSS items → lessons. HTML → stripped text blob."""
    root_path: Path = root or _repo_root()
    stats: dict[str, Any] = {
        "ok": True,
        "enabled": _web_enabled(),
        "sources_attempted": 0,
        "items_recorded": 0,
        "errors": [],
        "version": 1,
    }
    if not stats["enabled"]:
        return stats

    eng = LearningEngine(root_path)
    urls = default_source_urls()[:max_sources]
    for url in urls:
        stats["sources_attempted"] += 1
        code, body, err = fetch_url(url)
        if err and not body:
            stats["errors"].append({"url": url, "error": err})
            continue
        host = urlparse(url).netloc or url
        low = url.lower()
        snippet = body.strip()
        looks_rss = (
            low.endswith(".xml")
            or "rss" in low
            or "feed" in low
            or "<rss" in snippet[:4000].lower()
            or "<feed" in snippet[:4000].lower()
        )
        items = parse_rss_items(body, limit=max_items_per_source) if looks_rss else []
        if items:
            for it in items:
                commentary = f"{it.get('title', '')}\n{it.get('summary', '')}\n{it.get('link', '')}"
                eng.learn_from_market_commentary(
                    commentary,
                    source_url=it.get("link") or url,
                    source_title=it.get("title") or host,
                    use_llm=_web_llm_enabled(),
                )
                stats["items_recorded"] += 1
            continue
        text = _strip_tags(body)
        if len(text) < 120:
            stats["errors"].append({"url": url, "error": f"short_or_unparsed_http_{code}"})
            continue
        eng.learn_from_market_commentary(
            text[:12000],
            source_url=url,
            source_title=host,
            use_llm=_web_llm_enabled(),
        )
        stats["items_recorded"] += 1
    return stats
