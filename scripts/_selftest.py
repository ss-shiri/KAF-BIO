#!/usr/bin/env python3
"""Offline pipeline test. Replaces the network layer with fixtures so the
normalise -> dedup -> archive -> latest chain can be verified in CI or locally
without touching an upstream API. Not part of the scheduled run."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c
import ingest_news as N
import ingest_papers as P

FAIL = []


def check(label, cond):
    print("  %-52s %s" % (label, "ok" if cond else "FAIL"))
    if not cond:
        FAIL.append(label)


GDELT_FIXTURE = {"articles": [
    {"url": "https://ex1.test/a", "title": "Panel reviews gain of function oversight",
     "domain": "ex1.test", "language": "English", "sourcecountry": "United States",
     "seendate": "20260722T101500Z"},
    {"url": "https://ex2.test/b", "title": "New biosecurity rules take effect",
     "domain": "ex2.test", "language": "English", "sourcecountry": "Netherlands",
     "seendate": "20260722T090000Z"},
    {"url": "", "title": "dropped, no url", "domain": "x.test", "seendate": "20260722T090000Z"},
]}

EPMC_FIXTURE = {"resultList": {"result": [
    {"id": "40000001", "source": "MED", "doi": "10.1000/aaa",
     "title": "Metagenomic surveillance of wastewater", "journalTitle": "J Test Micro",
     "firstPublicationDate": "2026-07-20", "pubType": "journal article"},
    {"id": "PPR900001", "source": "PPR", "doi": "10.1101/bbb",
     "title": "Synthetic biology chassis engineering", "journalTitle": "",
     "firstPublicationDate": "2026-07-21", "pubType": "preprint"},
    {"id": "40000002", "source": "MED", "doi": "10.1000/ccc",
     "title": "Retracted study on viral transmissibility", "journalTitle": "J Test Viro",
     "firstPublicationDate": "2026-07-19", "pubType": "retracted publication"},
]}, "nextCursorMark": "*"}

RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Ministry updates biosafety guidance</title>
<link>https://feed.test/1</link><pubDate>Tue, 21 Jul 2026 08:00:00 +0000</pubDate></item>
<item><title>Unrelated sports result</title>
<link>https://feed.test/2</link><pubDate>Tue, 21 Jul 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""


def main():
    root = c.ROOT
    print("root:", root)

    # --- unit level -------------------------------------------------
    print("\n[unit]")
    check("gdelt timestamp parses to iso",
          N.gdelt_time_to_iso("20260722T101500Z") == "2026-07-22T10:15:00Z")
    check("bad gdelt timestamp returns None", N.gdelt_time_to_iso("junk") is None)
    check("rfc822 date parses", N.rss_date_to_iso("Tue, 21 Jul 2026 08:00:00 +0000") is not None)
    check("classify preprint", P.classify({"source": "PPR", "pubType": "preprint"}) == "pre")
    check("classify retraction",
          P.classify({"source": "MED", "pubType": "Retracted Publication"}) == "ret")
    check("classify journal", P.classify({"source": "MED", "pubType": "journal article"}) == "jrn")
    check("doi wins over epmc link",
          P.build_link({"doi": "10.1/x", "source": "MED", "id": "1"}) == "https://doi.org/10.1/x")
    check("stable id is deterministic", c.sid("https://a/b") == c.sid("https://A/B "))
    check("keyword match is case insensitive",
          c.match_keywords("A Gain Of Function study", ["gain of function"]) == ["gain of function"])
    check("clean collapses whitespace", c.clean("  a\n\n b  ") == "a b")

    # --- patch the network ------------------------------------------
    c.http_get_json = lambda url, **kw: GDELT_FIXTURE if "gdelt" in url else EPMC_FIXTURE
    N.c.http_get_json = c.http_get_json
    P.c.http_get_json = c.http_get_json
    N.c.http_get = lambda url, **kw: RSS_FIXTURE

    # --- run 1 ------------------------------------------------------
    print("\n[run 1]")
    N.main()
    P.main()
    latest = c.load_json("data/latest.json", default={})
    news1, papers1 = len(latest.get("news", [])), len(latest.get("papers", []))
    check("news ingested", news1 > 0)
    check("papers ingested", papers1 > 0)
    check("record without url dropped", all(r["url"] for r in latest["news"]))
    check("off topic rss item filtered out",
          not any("sports" in r["title"].lower() for r in latest["news"]))
    check("on topic rss item kept",
          any("biosafety guidance" in r["title"] for r in latest["news"]))
    types = {r["type"] for r in latest["papers"]}
    check("all three paper types present", types == {"pre", "jrn", "ret"})
    check("retraction retained not dropped",
          any(r["type"] == "ret" for r in latest["papers"]))
    check("every record carries epistemic status",
          all(r.get("epistemic_status") == "unverified_signal"
              for r in latest["news"] + latest["papers"]))
    check("every record carries first_seen_utc",
          all(r.get("first_seen_utc") for r in latest["news"] + latest["papers"]))

    stamps = {r["id"]: r["first_seen_utc"] for r in latest["news"] + latest["papers"]}

    # --- run 2, identical upstream ----------------------------------
    print("\n[run 2, same upstream data]")
    N.main()
    P.main()
    latest2 = c.load_json("data/latest.json", default={})
    check("no duplicates created",
          len(latest2.get("news", [])) == news1 and len(latest2.get("papers", [])) == papers1)
    stamps2 = {r["id"]: r["first_seen_utc"] for r in latest2["news"] + latest2["papers"]}
    check("first_seen_utc never rewritten", stamps == stamps2)

    ids = [r["id"] for r in latest2["news"]]
    check("ids unique within channel", len(ids) == len(set(ids)))
    order = [r["first_seen_utc"] for r in latest2["papers"]]
    check("latest sorted newest first", order == sorted(order, reverse=True))

    man = c.load_json("data/index.json", default={})
    check("manifest lists both channels",
          "news" in man.get("channels", {}) and "papers" in man.get("channels", {}))
    check("manifest totals non zero", man["channels"]["news_total"] > 0)

    # --- upstream outage --------------------------------------------
    print("\n[run 3, upstream returns nothing]")
    c.http_get_json = lambda url, **kw: None
    N.c.http_get_json = c.http_get_json
    P.c.http_get_json = c.http_get_json
    N.c.http_get = lambda url, **kw: None
    rc = N.main() + P.main()
    latest3 = c.load_json("data/latest.json", default={})
    check("exit code still clean on outage", rc == 0)
    check("existing archive not wiped by an outage",
          len(latest3.get("news", [])) == news1)

    print("\n%s  (%d checks failed)" % ("PASS" if not FAIL else "FAILURES", len(FAIL)))
    for f in FAIL:
        print("   -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
