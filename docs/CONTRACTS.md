# Domain contracts

The domain contracts are strict, immutable Pydantic v2 models. They are pure
Python and do not access the network, SQLite, FastAPI, or a concrete source.

## Identity and inventory

`AuctionGroup` and `AuctionLot` preserve stable external IDs, normalized text,
prices, currency, absolute credential-free URLs, optional images, activity,
closing dates, and UTC observation time. `source_id` is an ASCII lowercase
slug. Auction and lot IDs are opaque, non-empty, control-free values up to 256
characters and may contain delimiters, spaces, and Unicode.

The public lot identity is exclusively the canonical reversible form
`aw1:<source-escaped>:<auction-escaped>:<lot-escaped>`. Each component is
percent-encoded independently. Decoding uses strict UTF-8 and accepts only
the exact representation that the encoder produces; legacy keys, unnecessary
encoding, lowercase hex, invalid UTF-8, empty components, and unknown prefixes
are rejected.

## Profiles

`SearchProfile` supports `kind=user|system`, `locked`, `seed_key`, and
`seed_version` in addition to its matching rules. User profiles cannot carry
system seed metadata. System profiles require locked, versioned metadata and
are immutable in identity and seed ownership, but remain pausable. The public
`consolas` seed selects Bavastro, Castells, Remotes, TodoRemates, and Prado;
it is versioned, idempotent, and can be cloned into a fully editable user
profile without copying private runtime state.

Profiles may also declare reusable `risk_keywords` with bounded score
penalties and `context_rules` with required or excluded context terms. These
rules express ambiguous terms such as controls, Nintendo Switch, Odyssey, and
Pong without embedding a category-specific branch in the matcher.

Terms are deduplicated case- and accent-insensitively while retaining their
first readable spelling. Positive rules cannot overlap exclusions. Prices use
finite non-negative `Decimal` values paired with an uppercase three-letter
currency. Schedules validate IANA timezones and canonical `HH:MM` values.

## MatchResult

`MatchResult` records a canonical profile slug, canonical `opportunity_key`,
match decision, score, terms, matched fields, rejection codes, and a stable
explanation. `matched_fields` is restricted to `title`, `description`, and
`category`; every field key must occur in matched or excluded terms. Term and
field lists reject duplicates. A successful result must contain at least one
matched term and no rejection details.

## Sources and persistence

`SourceScanResult` contains normalized groups/lots, source-level discovery
status, explicit inventory authority, per-group `GroupReceipt`s, and sanitized
errors. Empty or drifted payloads are not authoritative without structural
evidence. Bavastro and WordPress adapters prove pagination; Castells proves
GXState plus lot response structure; Remotes proves an RSS channel and item
structure. Source adapters use injected transports and never perform matching,
persist rows, send mail, or write runtime files.

Persistence contracts cover runs, source results, coverage, lifecycle,
profile matches, user decisions, snapshots, and notification outbox records.
They validate the same identities and UTC timestamps before SQLAlchemy rows are
written. See `docs/PERSISTENCE.md` for schema and reconciliation invariants.
