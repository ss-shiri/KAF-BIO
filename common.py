"""
Shared helpers for KAF-BIO ingest.

Standard library only, on purpose. No pip install step in CI means fewer
moving parts and nothing to break when an upstream package changes.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "kaf-bio/1.0 (open source aggregator; +https://github.com/)"


# ----------------------------------------------------------------- time

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def parse_iso(s):
    """Tolerant ISO parse. Returns None rather than raising."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ----------------------------------------------------------------- io

def path(*parts):
    return os.path.join(ROOT, *parts)


def load_json(rel, default=None):
    p = path(rel) if not os.path.isabs(rel) else rel
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default if default is not None else {}


def save_json(rel, obj):
    p = path(rel) if not os.path.isabs(rel) else rel
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, p)


# ----------------------------------------------------------------- net

def http_get(url, retries=3, timeout=45, accept="application/json"):
    """GET with linear backoff. Returns bytes, or None after final failure."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": accept}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    print("  ! fetch failed: %s  (%s)" % (url[:110], last))
    return None


def http_get_json(url, retries=3, timeout=45):
    raw = http_get(url, retries=retries, timeout=timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        # Upstreams sometimes answer with an HTML error page and a 200.
        print("  ! response was not valid JSON: %s" % url[:110])
        return None


# ----------------------------------------------------------------- text

def sid(*parts):
    """Stable id from any identifying parts."""
    joined = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def clean(text, limit=400):
    """Collapse whitespace, strip control chars, cap length.

    Titles are stored as the publisher wrote them. This only normalises
    whitespace so the JSON stays tidy. Nothing is rewritten or summarised.
    """
    if not text:
        return ""
    s = " ".join(str(text).split())
    s = "".join(ch for ch in s if ch == "\t" or ord(ch) >= 32)
    return s[:limit].strip()


def match_keywords(text, keywords):
    low = (text or "").lower()
    return [k for k in keywords if k.lower() in low]


# ----------------------------------------------------------------- archive

def merge_into_day(channel, records):
    """Append new records to today's archive file. Append only, never rewrite.

    Returns the count actually written after in-file dedup.
    """
    day = today_str()
    rel = "data/%s/%s.json" % (channel, day)
    existing = load_json(rel, default=[])
    if not isinstance(existing, list):
        existing = []
    have = {r.get("id") for r in existing}
    fresh = [r for r in records if r.get("id") not in have]
    if fresh:
        save_json(rel, existing + fresh)
    return len(fresh)


def rebuild_latest(channels, window_days):
    """Build data/latest.json, the single file the site reads."""
    cutoff = days_ago(window_days)
    out = {"generated_utc": now_iso(), "window_days": window_days}
    for ch in channels:
        rows = []
        folder = path("data", ch)
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".json"):
                    continue
                if name[:-5] < cutoff:
                    continue
                part = load_json("data/%s/%s" % (ch, name), default=[])
                if isinstance(part, list):
                    rows.extend(part)
        rows.sort(key=lambda r: r.get("first_seen_utc", ""), reverse=True)
        out[ch] = rows[:400]
    save_json("data/latest.json", out)
    return {ch: len(out.get(ch, [])) for ch in channels}


def rebuild_manifest(channels):
    """List every archive day so the site can offer historical browsing."""
    man = {"generated_utc": now_iso(), "channels": {}}
    for ch in channels:
        folder = path("data", ch)
        days = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".json"):
                    rows = load_json("data/%s/%s" % (ch, name), default=[])
                    days.append({
                        "date": name[:-5],
                        "count": len(rows) if isinstance(rows, list) else 0,
                    })
        man["channels"][ch] = days
        man["channels"][ch + "_total"] = sum(d["count"] for d in days)
    save_json("data/index.json", man)
    return man


# ----------------------------------------------------------------- seen index

def load_seen(channel):
    return load_json("state/seen-%s.json" % channel, default={})


def save_seen(channel, seen, retention_days):
    """Persist the first-seen index, pruned to a bounded window.

    This index is what makes first_seen_utc stable across runs. A record
    keeps the timestamp of the run that first observed it, permanently.
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    save_json("state/seen-%s.json" % channel, pruned)
    return len(pruned)
