# KAF-BIO

A minimal two-channel aggregator for open source biology signals.
Headline and link only. No verification, no analysis, no ranking.

**CH.01 News** picks up reporting from open sources at every credibility level.
**CH.02 Literature** picks up journal articles and preprints, with retractions flagged rather than removed.

This is an aggregator, not a verifier. Listing a source is not an endorsement of it.

---

## Setup

1. Create the repo and push these files.
2. **Settings → Actions → General → Workflow permissions**, select *Read and write permissions*. Without this the commit step fails.
3. **Settings → Pages**, deploy from branch, root.
4. **Actions → ingest → Run workflow** for the first run. The archive starts from there.

After that it runs on its own every six hours.

## Layout

```
config/keywords.json     single tuning point, both channels
config/feeds.json        named RSS sources, optional
scripts/ingest_news.py   GDELT sweep plus RSS
scripts/ingest_papers.py Europe PMC sweep
scripts/_selftest.py     offline fixture suite, no network needed
data/news/YYYY-MM-DD.json    append only daily archive
data/papers/YYYY-MM-DD.json  append only daily archive
data/latest.json         merged recent window, this is what the page reads
data/index.json          archive manifest
state/seen-*.json        first-seen index, keeps timestamps stable
index.html               the viewer
```

## Retuning

Edit `config/keywords.json`. The `news` and `papers` arrays drive the upstream
queries and the filter chips on the page. Nothing else needs to change.

`settings` controls sweep width:

| key | meaning |
| --- | --- |
| `news_timespan` | GDELT lookback per run, e.g. `1d` |
| `news_max_per_query` | cap per keyword, GDELT allows up to 250 |
| `papers_lookback_days` | Europe PMC publication window |
| `latest_window_days` | how much history `latest.json` carries |
| `seen_retention_days` | dedup memory, prunes older ids |

## Record schema

News:

```json
{
  "id": "sha1 of url, 16 chars",
  "first_seen_utc": "2026-07-22T11:04:43Z",
  "published_utc": "2026-07-22T10:15:00Z",
  "source_name": "example.com",
  "title": "publisher's own headline, unmodified",
  "url": "https://...",
  "lang": "English",
  "country": "Netherlands",
  "matched_keywords": ["gain of function"],
  "type": "news",
  "ingest": "gdelt",
  "epistemic_status": "unverified_signal"
}
```

Literature:

```json
{
  "id": "sha1 of doi, 16 chars",
  "first_seen_utc": "2026-07-22T11:04:43Z",
  "pub_date": "2026-07-19",
  "source_name": "Journal name",
  "title": "article title, unmodified",
  "url": "https://doi.org/...",
  "doi": "10.1000/...",
  "venue": "Journal name",
  "type": "pre | jrn | ret",
  "matched_keywords": ["metagenomics"],
  "ingest": "europepmc",
  "epistemic_status": "unverified_signal"
}
```

`epistemic_status` is always `unverified_signal`. It is a field in the data
model rather than a note in the footer, so anything consuming the API inherits
the caveat automatically.

`type` on the literature channel is provenance, not assessment:

| code | meaning |
| --- | --- |
| `PRE` | preprint, not peer reviewed |
| `JRN` | journal article |
| `RET` | retraction recorded upstream, shown anyway |

## Append only

Records are never rewritten. `state/seen-*.json` holds the first observation
timestamp for every id, so a record keeps the moment it was first seen even if
the same item reappears in a later sweep. Daily files only ever gain rows.

Git history is the immutable layer underneath. Nobody can reconstruct this
archive after the fact, which is the point of starting it early.

## Testing

```bash
python scripts/_selftest.py
```

Runs the whole normalise, dedup, archive and merge chain against fixtures with
the network stubbed out. It cannot be broken by an upstream API changing, and
it verifies the properties that matter: no duplicates, timestamps never
rewritten, retractions retained, off-topic items filtered, and an upstream
outage leaving the existing archive intact.

## Scope

Metadata and links only. Nothing is hosted, rewritten, summarised or scored.
Headlines are stored as the publisher wrote them and every entry points back to
the original. Preprints, retracted papers and low credibility outlets appear
deliberately, each marked with its type.

Verification and assessment happen downstream, not here.

## Sources

| channel | upstream | key needed |
| --- | --- | --- |
| news | GDELT DOC 2.0 | no |
| news | RSS listed in `config/feeds.json` | no |
| literature | Europe PMC REST | no |

---

[CBRNE OSINT Reading Room](https://ss-shiri.github.io/kaf-cbrne/) ·
[ALEF OSINT](https://ss-shiri.github.io/ALEF-OSINT/) ·
[LinkedIn](https://www.linkedin.com/in/sajad-shiri/)
