# Matching semantics

The matcher is a pure function:

```python
match_lot(profile: SearchProfile, lot: AuctionLot) -> MatchResult
match_inventory(profiles, lots) -> list[MatchResult]
```

It evaluates normalized lots against profiles without network access, storage,
source-specific logic, global state, or mutation. `match_inventory` returns a
result for every profile/lot pair in the order supplied, including rejections.

## Normalization

Text is case-folded, decomposed with Unicode NFKD, stripped of diacritics,
converted to spaces at punctuation boundaries, and collapsed to single spaces.
Therefore `José` matches `jose` and `PlayStation-2` matches `playstation 2`.

Terms are matched as complete token sequences. `arte` does not match inside
`parte`, and phrase order is preserved. There is no stemming, translation, or
automatic alias expansion; users define variants explicitly.

The searched fields are `title`, `description`, and `category`. Every matched
term records the fields where it occurred.

## Evaluation order and gates

1. Disabled profiles are rejected.
2. Lots from sources not selected by the profile are rejected.
3. Inactive lots are rejected.
4. Any exclusion found in the searchable fields rejects the lot immediately.
5. Every `keywords_all` term must be present.
6. At least one positive trigger must be present: a `keywords_any` term, an
   `exact_phrase`, or all `keywords_all` terms when those are the only positive
   rules.
7. Once a positive candidate exists, the deterministic diagnostic score is
   calculated, including boosts and title bonuses.
8. The price policy is applied as a gate.
9. The diagnostic score is compared with `minimum_score` as the final gate.

This means price and minimum-score rejections retain the score that explains
the candidate, while structural rejections can have score zero.

The machine-readable rejection codes are `profile_disabled`,
`source_not_selected`, `lot_inactive`, `excluded_term`,
`missing_required_terms`, `no_positive_trigger`, `unknown_price`,
`price_above_maximum`, and `score_below_minimum`.

## Score

The constants live together in `core/matching.py`:

| Rule | Score |
| --- | ---: |
| Each `keywords_any` term | +2 |
| Each `keywords_all` term | +3 |
| Each exact phrase | +5 |
| Each matching boost keyword | configured positive integer |
| Each matched non-boost term/phrase in the title | +1 |

Each rule contributes at most once, regardless of repeated appearances. A
boost adds score only after a positive non-boost rule has triggered; a boost
cannot create a match by itself. The title bonus is deliberately not applied to
boost keywords.

## Price

When a maximum is configured, a known price is comparable only when its
currency equals the filter currency. A higher known price is rejected. An
unknown price or a different currency follows `on_unknown`: `include` keeps
evaluating it, while `exclude` rejects it. No conversion is performed.

## Examples

A profile with `keywords_any=["vinilo"]` matches a title containing
`Vinilo de rock argentino` with score 3: two points for the term and one title
bonus. A profile with `keywords_all=["vinilo", "primera edición"]` rejects a
lot missing the second term. A profile with `exclude_keywords=["réplica"]`
rejects as soon as that term appears, even when other terms and boosts would
produce a high score.

To reduce false positives, combine a broad term with `keywords_all`, add
`exclude_keywords` for known irrelevant uses, and raise `minimum_score`. Use
`exact_phrases` when word order is meaningful.

## Known limitations

The current core does not infer synonyms, spelling variants, translations,
stemming, currency conversions, source quality, or semantic similarity. Those
behaviors must be explicit profile or future source-layer decisions.
