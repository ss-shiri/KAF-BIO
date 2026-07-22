#!/usr/bin/env python3
"""
CH.01 news ingest.

Two collectors run into one normalised stream:

  gdelt : broad global sweep, 65+ languages, indexes outlets of every
          credibility level. This is what makes the channel wide.
  rss   : named feeds from config/feeds.json. This is what makes the
          channel yours rather than a GDELT mirror.

Stores headline and link only. No snippet, no summary, no scoring.
"""

import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import common as c

CHANNEL = "news"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


# ------------------------------------------------------------ gdelt

def gdelt_time_to_iso(s):
    s = str(s or "")
    if len(s) >= 15 and s[8] == "T":
        return "%s-%s-%sT%s:%s:%sZ" % (
            s[0:4], s[4:6], s[6:8], s[9:11], s[11:13], s[13:15])
    return None


def fetch_gdelt(keyword, timespan, maxrecords):
    term = '"%s"' % keyword if " " in keyword else keyword
    qs = urllib.parse.urlencode({
        "query": term,
        "mode": "artlist",
        "maxrecords": str(maxrecords),
        "format": "json",
        "sort": "datedesc",
        "timespan": timespan,
    })
    data = c.http_get_json("%s?%s" % (GDELT, qs))
    if not data or not isinstance(data, dict):
        return []
    out = []
    for a in data.get("articles") or []:
        url = (a.get("url") or "").strip()
        title = c.clean(a.get("title"))
        if not url or not title:
            continue
        out.append({
            "id": c.sid(url),
            "source_name": (a.get("domain") or "unknown").strip(),
            "title": title,
            "url": url,
            "lang": (a.get("language") or "").strip()[:24],
            "country": (a.get("sourcecountry") or "").strip()[:48],
            "published_utc": gdelt_time_to_iso(a.get("seendate")),
            "ingest": "gdelt",
        })
    return out


# ------------------------------------------------------------ rss

def _txt(node):
    return c.clean("".join(node.itertext())) if node is not None else ""


def _strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def rss_date_to_iso(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, IndexError):
        pass
    d = c.parse_iso(s)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def fetch_rss(feed):
    raw = c.http_get(feed["url"], retries=2, timeout=35,
                     accept="application/rss+xml, application/xml, text/xml, */*")
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print("  ! unparseable XML: %s" % feed["name"])
        return []

    items = [e for e in root.iter() if _strip_ns(e.tag) in ("item", "entry")]
    out = []
    for it in items:
        title, link, pub = "", "", ""
        for child in it:
            tag = _strip_ns(child.tag)
            if tag == "title" and not title:
                title = _txt(child)
            elif tag == "link" and not link:
                link = (child.get("href") or _txt(child)).strip()
            elif tag in ("pubDate", "published", "updated", "date") and not pub:
                pub = _txt(child)
        title = c.clean(title)
        if not title or not link:
            continue
        out.append({
            "id": c.sid(link),
            "source_name": feed["name"],
            "title": title,
            "url": link,
            "lang": "",
            "country": "",
            "published_utc": rss_date_to_iso(pub),
            "ingest": "rss",
            "tier": feed.get("tier", ""),
        })
    return out


# ------------------------------------------------------------ main

def main():
    cfg = c.load_json("config/keywords.json", default={})
    keywords = cfg.get("news") or []
    st = cfg.get("settings") or {}
    if not keywords:
        print("no news keywords configured, nothing to do")
        return 0

    timespan = st.get("news_timespan", "1d")
    maxrec = int(st.get("news_max_per_query", 75))
    window = int(st.get("latest_window_days", 14))
    retention = int(st.get("seen_retention_days", 180))

    collected = {}

    print("gdelt sweep: %d keywords, timespan=%s" % (len(keywords), timespan))
    for k in keywords:
        rows = fetch_gdelt(k, timespan, maxrec)
        print("  %-28s %3d" % (k, len(rows)))
        for r in rows:
            collected.setdefault(r["id"], r)

    feeds = (c.load_json("config/feeds.json", default={}) or {}).get("feeds") or []
    if feeds:
        print("rss sweep: %d feeds" % len(feeds))
        for f in feeds:
            rows = fetch_rss(f)
            kept = 0
            for r in rows:
                # RSS is a named source, so an item only enters if it
                # actually matches the keyword set. GDELT already filtered.
                if not c.match_keywords(r["title"], keywords):
                    continue
                collected.setdefault(r["id"], r)
                kept += 1
            print("  %-28s %3d/%d" % (f["name"][:28], kept, len(rows)))

    if not collected:
        print("collected nothing this run")
        return 0

    seen = c.load_seen(CHANNEL)
    stamp = c.now_iso()
    fresh = []
    for rid, rec in collected.items():
        if rid in seen:
            continue
        seen[rid] = stamp
        rec["first_seen_utc"] = stamp
        rec["matched_keywords"] = c.match_keywords(rec["title"], keywords)
        rec["type"] = "news"
        rec["epistemic_status"] = "unverified_signal"
        fresh.append(rec)

    written = c.merge_into_day(CHANNEL, fresh)
    kept = c.save_seen(CHANNEL, seen, retention)
    counts = c.rebuild_latest(["news", "papers"], window)
    c.rebuild_manifest(["news", "papers"])

    print("collected=%d  new=%d  written=%d  seen_index=%d  latest=%s"
          % (len(collected), len(fresh), written, kept, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
