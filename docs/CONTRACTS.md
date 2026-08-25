# Domain contracts

The domain contracts are strict, immutable Pydantic v2 models. They are pure
Python and do not access the network, SQLite, FastAPI, or any concrete source.
Source adapters will construct these models from their own responses later.

## `AuctionGroup`

`AuctionGroup` contains `source_id`, `auction_id`, `title`, `url`, `category`,
`active`, `closing_at`, and `observed_at`. Identifiers and descriptive fields
cannot be empty. Dates are timezone-aware.

## `AuctionLot`

`AuctionLot` contains `source_id`, `auction_id`, `lot_id`, `title`,
`description`, `category`, `price_value`, `price_currency`, `price_label`,
`closing_at`, `lot_url`, `auction_url`, `image_url`, `active`, and
`observed_at`. Prices use `Decimal`, currencies are uppercase, and raw source
payloads are deliberately not part of the model.

The canonical identity is `(source_id, auction_id, lot_id)`. The stable
`opportunity_key` property renders it as `source_id:auction_id:lot_id`.

## `SearchProfile`

`SearchProfile` contains a slug `id`, non-empty `name`, `enabled`,
`keywords_any`, `keywords_all`, `exact_phrases`, `exclude_keywords`, bounded
positive-integer `boost_keywords`, at least one `source_ids`, non-negative
`minimum_score`, an optional `PriceFilter`, notification mode, and a validated
`SearchSchedule`.

Terms are deduplicated case- and accent-insensitively while their first readable
spelling is retained. A profile must have at least one positive rule from
`keywords_any`, `keywords_all`, or `exact_phrases`.

`PriceFilter` uses a positive `Decimal` maximum, an uppercase currency, and an
`on_unknown` policy of `include` or `exclude`. Currency conversion is not part
of this contract. `SearchSchedule` validates IANA timezones and canonicalizes
duplicate `HH:MM` values.

## `MatchResult`

`MatchResult` records `profile_id`, `opportunity_key`, `matched`, `score`,
`matched_terms`, `excluded_terms`, `missing_required_terms`,
`matched_fields`, machine-readable `rejection_reasons`, and a stable human
explanation. Rejections are retained so later layers can decide whether to
display or filter them.

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
