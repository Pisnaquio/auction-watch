# Domain contracts

The domain contracts are strict, immutable Pydantic v2 models. They are pure
Python and do not access the network, SQLite, FastAPI, or any concrete source.
Source adapters will construct these models from their own responses later.

## `AuctionGroup`

`AuctionGroup` contains `source_id`, `auction_id`, `title`, `url`, `category`,
`active`, `closing_at`, and `observed_at`. `source_id` is an exact ASCII
lowercase slug. `auction_id` is an opaque non-empty external identifier with a
bounded length. Category is optional metadata and may be empty. Dates are
timezone-aware, and the URL must be absolute HTTP/HTTPS without credentials.

## `AuctionLot`

`AuctionLot` contains `source_id`, `auction_id`, `lot_id`, `title`,
`description`, `category`, `price_value`, `price_currency`, `price_label`,
`closing_at`, `lot_url`, `auction_url`, `image_url`, `active`, and
`observed_at`. Prices use finite non-negative `Decimal` values paired with
exactly three ASCII uppercase currency letters. Raw source payloads are
deliberately not part of the model. URLs are absolute HTTP/HTTPS without
credentials; a blank image URL becomes `None`.

The persistence identity is the composite tuple `(source_id, auction_id,
lot_id)`. The public `opportunity_key` is exclusively the canonical,
versioned reversible form `aw1:<source-escaped>:<auction-escaped>:<lot-escaped>`,
with each component percent-encoded independently. Decoding uses strict UTF-8
and then requires `encode_opportunity_key(*decoded) == original_key`;
unnecessary encoding such as `%61`, lowercase hex, invalid UTF-8, legacy keys,
and malformed components are rejected. The encoder and decoder prevent
delimiter collisions and alternate representations.

## `SearchProfile`

`SearchProfile` contains an exact ASCII lowercase slug `id`, non-empty `name`, `enabled`,
`keywords_any`, `keywords_all`, `exact_phrases`, `exclude_keywords`, bounded
positive-integer `boost_keywords`, at least one `source_ids`, non-negative
`minimum_score`, an optional `PriceFilter`, notification mode, and a validated
`SearchSchedule`.

Terms are deduplicated case- and accent-insensitively while their first readable
spelling is retained. Ordered keyword, source, and schedule inputs accept only
lists or tuples. Source IDs are deduplicated by exact equality and are not
linguistically normalized. A profile must have at least one positive rule from
`keywords_any`, `keywords_all`, or `exact_phrases`; repeated normalized rules
across those groups are rejected.

Positive rules cannot overlap exclusions. A boost may overlap a positive rule,
but cannot overlap an exclusion. Boosts are immutable after construction and
remain JSON objects.

`PriceFilter` uses a positive finite `Decimal` maximum paired with an uppercase
currency, and an `on_unknown` policy of `include` or `exclude`. Currency
conversion is not part of this contract. `SearchSchedule` validates IANA
timezones, canonicalizes duplicate `HH:MM` values, and requires at least one
time when enabled; disabled schedules may retain configured times.

## `MatchResult`

`MatchResult` records a canonical profile slug, a canonical `opportunity_key`,
`matched`, `score`,
`matched_terms`, `excluded_terms`, `missing_required_terms`,
`matched_fields`, machine-readable `rejection_reasons`, and a stable human
explanation. `matched_fields` is restricted to `title`, `description`, and
`category`; its terms must be present in `matched_terms` or `excluded_terms`.
Mappings are deeply immutable while remaining JSON objects, and term and field
lists cannot contain duplicates. A successful result must contain at least one
matched term and cannot contain rejection details; a rejected result must
contain at least one known rejection code.

## `Run`

`Run` is reserved for the later scheduler and persistence task. It will
represent one manual or scheduled scan, including its identifier, status,
timestamps, per-source status, and content hash.

## `Snapshot`

`Snapshot` is reserved for the later publication task. It will represent the
immutable published result of a run and share the run identifier and content
hash with the scan.

## `AuctionSource`

`AuctionSource` is reserved for the later source-adapter task. Its common
interface will expose source identity, discovery, lot fetching, normalization,
and health/error information. It must not perform profile matching or
notification delivery.
