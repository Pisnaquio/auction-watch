# Reserved contracts

The following contracts define the vocabulary for later implementation. This
foundation documents them without implementing source, profile, matching, or
run behavior.

## `AuctionGroup`

Represents one auction or remate discovered by a source: source identifier,
auction identifier, title, URL, category, active state, and closing metadata.

## `AuctionLot`

Represents a normalized listing: source identifier, auction identifier, lot
identifier, title, description, category, price value/currency/label, closing
time, lot URL, auction URL, image URL, and active state.

## `SearchProfile`

Represents user-defined search behavior: identity, name, enabled state, any/all
keywords, exact phrases, exclusions, boosts, source identifiers, score and
price limits, notification policy, and schedule settings.

## `MatchResult`

Associates a profile with a lot and records score, matched terms, excluded
terms, searched fields, and a human-readable explanation.

## `Run`

Represents one manual or scheduled scan, including its identifier, status,
start/end timestamps, per-source status, and content hash.

## `Snapshot`

Represents the immutable published result of a run. It shares the run
identifier and content hash with the scan and is verified before delivery.

## `AuctionSource`

The common adapter interface will expose source identity, discovery, lot
fetching, normalization, and health/error information. It must not perform
profile matching or notification delivery.
