# Offline corpus (T010)

`corpus.py` validates `sources/manifest.json` and each snapshot's SHA-256 before
returning fresh data. Call `load_corpus(environment="test")` or use the pytest
`corpus` fixture. `corpus_clock` fixes eligibility scenarios to the manifest's
`as_of` instant; do not compare them against the machine clock. `test_environment`
provides fake configuration values without changing process environment.

The three `official_download` files are frozen downloads from their manifest URLs:
Registrar HTML, Public Safety HTML, and the Faculty Senate's five-page academic
calendar PDF. Retrieval timestamps and byte hashes record their provenance.
They are data for extraction tests, not executable pages: do not serve their HTML
or execute embedded scripts. Tests do not fetch these URLs or their linked assets.
The PDF is historical source evidence, not a claim about current deadlines.

Conflict HTML, program/course DOCX, and the deliberately truncated PDF are
synthetic test documents, explicitly labeled in both content and metadata. Their
PNW URLs identify the associated subject source, not the origin of those bytes.
Do not cite their invented deadlines or prerequisites as university policy.

Cases reference documents independently, so the same snapshot can test draft,
rejected, pending, active, approved-but-inactive, superseded, retired, expired,
and future states. Cases also cover both campuses, a program/course/term scope,
conflicting evidence, and unreadable content. `expected_eligible` is the expected
base eligibility at `as_of`, before applying a particular question's context.
Conflict groups stay separate from stored revision status, matching the current
model while letting later eligibility tests require an unresolved outcome.

Every approval is a **synthetic local/test approval**, with an artificial reviewer
subject and reason. There is no real owning-office approval in this corpus.
The loader rejects any environment other than `local` or `test`; it writes no
records and provides no production seed command. Later ingestion/seeding code must
retain this guard and must not treat manifest approval as live authorization.

Contacts were checked in the downloaded Registrar and Public Safety HTML. Emergency
contacts are separate from campus non-emergency numbers. The manifest preserves
source links so future refreshes can reverify each number; ordinary tests remain
offline. Review refreshed bytes and provenance before changing stored hashes.
