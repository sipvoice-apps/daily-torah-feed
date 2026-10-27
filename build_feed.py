#!/usr/bin/env python3
"""Build a daily learning feed (JSON Feed 1.1 + RSS 2.0).

Sources
  - Kitzur Shulchan Aruch  : he.wikisource.org, one siman per day, cycling 1..221
  - Rambam (1 chapter/day) : chabad.org daily study (Sefaria calendar as fallback for the title)
  - CCHF Daily Companion   : cchf.global WordPress REST API, category "Daily Companion"

Every run upserts today's items into docs/items.json (the rolling history), then
regenerates docs/feed.json, docs/feed.xml and docs/index.html. If a source fails, the
run still succeeds and the previous items are kept.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
UA = "DailyLearningFeed/1.0 (personal RSS feed; +https://github.com/) python-requests"
TIMEOUT = 30

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,he;q=0.8"})


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- helpers
def hebrew_numeral(n: int) -> str:
    """1 -> א, 15 -> טו, 16 -> טז, 221 -> רכא (no geresh, as used in Wikisource page names)."""
    if not 1 <= n <= 999:
        raise ValueError(n)
    hundreds = ["", "ק", "ר", "ש", "ת", "תק", "תר", "תש", "תת", "תתק"]
    tens = ["", "י", "כ", "ל", "מ", "נ", "ס", "ע", "פ", "צ"]
    ones = ["", "א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט"]
    h, rest = divmod(n, 100)
    if rest == 15:
        return hundreds[h] + "טו"
    if rest == 16:
        return hundreds[h] + "טז"
    t, o = divmod(rest, 10)
    return hundreds[h] + tens[t] + ones[o]


def siman_for(day: date, cfg: dict) -> int:
    start = date.fromisoformat(cfg["start_date"])
    total = int(cfg.get("total_simanim", 221))
    offset = (day - start).days
    return (int(cfg.get("start_siman", 1)) - 1 + offset) % total + 1


def truncate(text: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=tz)


# ---------------------------------------------------------------- Kitzur Shulchan Aruch
def fetch_kitzur(day: date, cfg: dict, tz: ZoneInfo) -> list[dict]:
    n = siman_for(day, cfg)
    heb = hebrew_numeral(n)
    page = f"קיצור שולחן ערוך {heb}"
    url = "https://he.wikisource.org/wiki/" + urllib.parse.quote(page.replace(" ", "_"))
    r = session.get(
        "https://he.wikisource.org/w/api.php",
        params={"action": "parse", "page": page, "prop": "text|displaytitle",
                "format": "json", "formatversion": "2", "redirects": "1"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("info", "wikisource error"))
    title_extra, paragraphs = parse_wikisource_html(data["parse"]["text"])
    title = f"קיצור שולחן ערוך – סימן {heb}"
    if title_extra:
        title += f" – {title_extra}"
    body = "".join(f'<p dir="rtl">{html.escape(p)}</p>' for p in paragraphs)
    return [{
        "id": f"kitzur-{day.isoformat()}",
        "source": "Kitzur Shulchan Aruch",
        "title": title,
        "url": url,
        "date_published": day_start(day, tz).isoformat(),
        "summary": truncate(" ".join(paragraphs)) or f"סימן {heb} ({n} of {cfg.get('total_simanim', 221)})",
        "content_html": body or f'<p><a href="{url}">{html.escape(title)}</a></p>',
        "language": "he",
    }]


def parse_wikisource_html(fragment: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(fragment, "html.parser")
    for sel in ["table", ".navbox", ".mw-editsection", "sup.reference", ".reference",
                "ol.references", ".noprint", "style", "script", ".mw-empty-elt"]:
        for el in soup.select(sel):
            el.decompose()
    # Siman subject: first heading or leading bold text, if present
    subject = ""
    head = soup.find(["h2", "h3", "h4"])
    if head:
        subject = head.get_text(" ", strip=True)
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 1]
    return truncate(subject, 80), paragraphs


# ---------------------------------------------------------------- Rambam
CHAPTER_RE = re.compile(r"([A-Z][A-Za-z'’\-\. ]{2,60}?\s*-\s*Chapter\s+\d+)")


def fetch_rambam(day: date, cfg: dict, tz: ZoneInfo) -> list[dict]:
    tdate = f"{day.month}/{day.day}/{day.year}"
    url = f"https://www.chabad.org/dailystudy/rambam.asp?tdate={tdate}&rambamChapters=1"
    title, summary = "", ""
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        title, summary = parse_chabad_rambam(r.text)
    except Exception as e:  # chabad.org sometimes blocks datacenter IPs
        log(f"  rambam: chabad.org failed ({e}); trying Sefaria calendar")
    if not title:
        title = sefaria_rambam_title(day)
    if not title:
        raise RuntimeError("could not determine today's Rambam chapter")
    return [{
        "id": f"rambam-{day.isoformat()}",
        "source": "Rambam – 1 Chapter a Day",
        "title": f"Rambam: {title}",
        "url": url,
        "date_published": day_start(day, tz).isoformat(),
        "summary": summary or f"Today's chapter of Mishneh Torah: {title}",
        "content_html": (f"<p>{html.escape(summary)}</p>" if summary else "")
                        + f'<p><a href="{html.escape(url)}">Read {html.escape(title)} on Chabad.org</a></p>',
        "language": "en",
    }]


def parse_chabad_rambam(page: str) -> tuple[str, str]:
    soup = BeautifulSoup(page, "html.parser")
    candidates = []
    for sel in ["h1", "h2", "meta[property='og:title']", "title"]:
        for el in soup.select(sel):
            candidates.append(el.get("content") if el.name == "meta" else el.get_text(" ", strip=True))
    title = ""
    for c in candidates:
        m = CHAPTER_RE.search(c or "")
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            break
    summary = ""
    for p in soup.find_all(["p", "div"], recursive=True):
        if p.find(["p", "div"]):
            continue
        t = p.get_text(" ", strip=True)
        if len(t) > 120 and not re.search(r"cookie|subscribe|donat|copyright|©", t, re.I):
            summary = truncate(t)
            break
    return title, summary


def sefaria_rambam_title(day: date) -> str:
    try:
        r = session.get("https://www.sefaria.org/api/calendars",
                        params={"year": day.year, "month": day.month, "day": day.day,
                                "diaspora": 1}, timeout=TIMEOUT)
        r.raise_for_status()
        for item in r.json().get("calendar_items", []):
            if item.get("title", {}).get("en") == "Daily Rambam":
                return item.get("displayValue", {}).get("en", "")
    except Exception as e:
        log(f"  rambam: Sefaria fallback failed ({e})")
    return ""


# ---------------------------------------------------------------- CCHF Daily Companion
def fetch_cchf(day: date, cfg: dict, tz: ZoneInfo) -> list[dict]:
    r = session.get(
        "https://cchf.global/wp-json/wp/v2/posts",
        params={"categories": cfg.get("category_id", 13), "per_page": cfg.get("posts_per_run", 3),
                "orderby": "date", "order": "desc",
                "_fields": "id,date,link,title,excerpt,content"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    items = []
    for post in r.json():
        published = datetime.fromisoformat(post["date"]).replace(tzinfo=tz)
        if published.date() > day:  # skip scheduled/future-dated posts
            continue
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text().strip()
        excerpt = BeautifulSoup(post.get("excerpt", {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
        content = clean_wp_html(post.get("content", {}).get("rendered", ""))
        items.append({
            "id": f"cchf-{post['id']}",
            "source": "CCHF Daily Companion",
            "title": f"Daily Companion: {title}",
            "url": post["link"],
            "date_published": published.isoformat(),
            "summary": truncate(excerpt or BeautifulSoup(content, "html.parser").get_text(" ")),
            "content_html": content or f'<p><a href="{post["link"]}">{html.escape(title)}</a></p>',
            "language": "en",
        })
    return items


def clean_wp_html(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    for el in soup.select("script, style, iframe, form, noscript"):
        el.decompose()
    return str(soup).strip()


# ---------------------------------------------------------------- output
def write_json_feed(items: list[dict], cfg: dict) -> None:
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": cfg["feed_title"],
        "description": cfg["feed_description"],
        "home_page_url": cfg["site_url"],
        "feed_url": cfg["site_url"].rstrip("/") + "/feed.json",
        "items": [{
            "id": it["id"], "url": it["url"], "title": it["title"],
            "summary": it["summary"], "content_html": it["content_html"],
            "date_published": it["date_published"], "tags": [it["source"]],
            "language": it.get("language", "en"),
        } for it in items],
    }
    (OUT / "feed.json").write_text(json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8")


def write_rss(items: list[dict], cfg: dict, now: datetime) -> None:
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    C = "{http://purl.org/rss/1.0/modules/content/}"
    A = "{http://www.w3.org/2005/Atom}"
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = cfg["feed_title"]
    ET.SubElement(ch, "link").text = cfg["site_url"]
    ET.SubElement(ch, "description").text = cfg["feed_description"]
    ET.SubElement(ch, A + "link", href=cfg["site_url"].rstrip("/") + "/feed.xml",
                  rel="self", type="application/rss+xml")
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(now)
    ET.SubElement(ch, "ttl").text = "480"
    for it in items:
        el = ET.SubElement(ch, "item")
        ET.SubElement(el, "title").text = it["title"]
        ET.SubElement(el, "link").text = it["url"]
        ET.SubElement(el, "guid", isPermaLink="false").text = it["id"]
        ET.SubElement(el, "category").text = it["source"]
        ET.SubElement(el, "pubDate").text = format_datetime(datetime.fromisoformat(it["date_published"]))
        ET.SubElement(el, "description").text = it["summary"]
        ET.SubElement(el, C + "encoded").text = it["content_html"]
    ET.indent(rss)
    (OUT / "feed.xml").write_bytes(ET.tostring(rss, encoding="utf-8", xml_declaration=True))


def write_index(items: list[dict], cfg: dict, now: datetime) -> None:
    rows = "\n".join(
        f'<li><span class="src">{html.escape(it["source"])}</span> · '
        f'<time>{it["date_published"][:10]}</time><br>'
        f'<a href="{html.escape(it["url"])}" dir="auto">{html.escape(it["title"])}</a>'
        f'<p dir="auto">{html.escape(it["summary"])}</p></li>'
        for it in items[:30])
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(cfg['feed_title'])}</title>
<link rel="alternate" type="application/rss+xml" href="feed.xml">
<link rel="alternate" type="application/feed+json" href="feed.json">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 16px;color:#222}}
li{{margin:0 0 1.25rem;list-style:none}}ul{{padding:0}}.src{{font-size:.8rem;color:#666;text-transform:uppercase}}
time{{font-size:.8rem;color:#666}}a{{font-weight:600}}p{{margin:.25rem 0;color:#444}}</style></head>
<body><h1>{html.escape(cfg['feed_title'])}</h1>
<p>Subscribe: <a href="feed.xml">RSS</a> · <a href="feed.json">JSON Feed</a> · updated {now:%Y-%m-%d %H:%M %Z}</p>
<ul>{rows}</ul></body></html>"""
    (OUT / "index.html").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- main
SOURCES = {"kitzur": fetch_kitzur, "rambam": fetch_rambam, "cchf": fetch_cchf}


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = datetime.now(tz)
    today = now.date()
    OUT.mkdir(exist_ok=True)
    store_path = OUT / "items.json"
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}

    ok = 0
    for key, fn in SOURCES.items():
        if not cfg.get(key, {}).get("enabled", True):
            continue
        try:
            new = fn(today, cfg.get(key, {}), tz)
            for it in new:
                store[it["id"]] = it
            ok += 1
            log(f"{key}: {len(new)} item(s) — " + "; ".join(i["title"] for i in new))
        except Exception as e:
            log(f"{key}: FAILED — {e} (keeping previous items)")

    cutoff = now - timedelta(days=int(cfg.get("history_days", 30)))
    items = [it for it in store.values() if datetime.fromisoformat(it["date_published"]) >= cutoff]
    order = {"Kitzur Shulchan Aruch": 0, "Rambam – 1 Chapter a Day": 1, "CCHF Daily Companion": 2}
    items.sort(key=lambda it: (-datetime.fromisoformat(it["date_published"]).date().toordinal(),
                               order.get(it["source"], 9)))

    store_path.write_text(json.dumps({it["id"]: it for it in items}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_json_feed(items, cfg)
    write_rss(items, cfg, now)
    write_index(items, cfg, now)
    log(f"wrote {len(items)} items; {ok} source(s) succeeded")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
