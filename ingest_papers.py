#!/usr/bin/env python3
"""
CH.02 literature ingest.

Europe PMC covers PubMed, PMC, Agricola, patents and preprint servers
in one index, needs no API key, and reports enough metadata to type each
record without judging it:

  PRE  source == "PPR"                  not peer reviewed
  RET  pubType mentions retraction      flagged, never removed
  JRN  everything else

That typing is provenance, not assessment. The record is shown either way.
"""

import sys
import urllib.parse

import common as c

CHANNEL = "papers"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def classify(rec):
    pub = (rec.get("pubType") or "").lower()
    if "retract" in pub:
        return "ret"
    if (rec.get("source") or "").upper() == "PPR":
        return "pre"
    return "jrn"


def build_link(rec):
    doi = (rec.get("doi") or "").strip()
    if doi:
        return "https://doi.org/" + doi
    src = (rec.get("source") or "MED").strip()
    rid = (rec.get("id") or "").strip()
    if rid:
        return "https://europepmc.org/article/%s/%s" % (src, rid)
    return ""


def fetch_page(query, page_size, cursor="*"):
    qs = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": str(page_size),
        "sort": "P_PDATE_D desc",
        "resultType": "lite",
        "cursorMark": cursor,
    })
    return c.http_get_json("%s?%s" % (EPMC, qs))


def fetch_keyword(keyword, lookback_days, page_size, max_pages=3):
    """One query per keyword. Narrow queries return better ranked results
    than one giant OR expression, and a failure only loses one keyword."""
    since = c.days_ago(lookback_days)
    until = c.days_ago(-1)
    query = '"%s" AND (FIRST_PDATE:[%s TO %s])' % (keyword, since, until)

    rows, cursor, pages = [], "*", 0
    while pages < max_pages:
        data = fetch_page(query, page_size, cursor)
        if not data:
            break
        result = ((data.get("resultList") or {}).get("result")) or []
        if not result:
            break
        for a in result:
            title = c.clean(a.get("title"))
            link = build_link(a)
            if not title or not link:
                continue
            doi = (a.get("doi") or "").strip()
            venue = c.clean(a.get("journalTitle") or a.get("bookOrReportDetails") or "", 160)
            if not venue and (a.get("source") or "").upper() == "PPR":
                venue = "preprint"
            pub_date = (a.get("firstPublicationDate") or "").strip()
            rows.append({
                "id": c.sid(doi or link),
                "source_name": venue or "unknown venue",
                "title": title,
                "url": link,
                "doi": doi,
                "venue": venue,
                "type": classify(a),
                "pub_date": pub_date,
                "epmc_source": (a.get("source") or "").strip(),
                "ingest": "europepmc",
            })
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor, pages = nxt, pages + 1
    return rows


def main():
    cfg = c.load_json("config/keywords.json", default={})
    keywords = cfg.get("papers") or []
    st = cfg.get("settings") or {}
    if not keywords:
        print("no paper keywords configured, nothing to do")
        return 0

    lookback = int(st.get("papers_lookback_days", 7))
    page_size = int(st.get("papers_page_size", 100))
    window = int(st.get("latest_window_days", 14))
    retention = int(st.get("seen_retention_days", 180))

    collected = {}
    print("europe pmc sweep: %d keywords, lookback=%dd" % (len(keywords), lookback))
    for k in keywords:
        rows = fetch_keyword(k, lookback, page_size)
        print("  %-32s %4d" % (k, len(rows)))
        for r in rows:
            collected.setdefault(r["id"], r)

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
        rec["matched_keywords"] = c.match_keywords(
            rec["title"] + " " + rec.get("venue", ""), keywords)
        rec["epistemic_status"] = "unverified_signal"
        fresh.append(rec)

    written = c.merge_into_day(CHANNEL, fresh)
    kept = c.save_seen(CHANNEL, seen, retention)
    counts = c.rebuild_latest(["news", "papers"], window)
    c.rebuild_manifest(["news", "papers"])

    types = {}
    for r in fresh:
        types[r["type"]] = types.get(r["type"], 0) + 1
    print("collected=%d  new=%d  written=%d  types=%s  seen_index=%d  latest=%s"
          % (len(collected), len(fresh), written, types, kept, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
