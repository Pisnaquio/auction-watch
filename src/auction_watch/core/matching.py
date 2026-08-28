"""Deterministic, source-independent profile matching."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from auction_watch.core.models import AuctionLot, ContextRule, MatchResult, SearchProfile
from auction_watch.core.normalization import contains_term, normalize_term, normalize_text

ANY_SCORE = 2
ALL_SCORE = 3
EXACT_PHRASE_SCORE = 5
TITLE_BONUS = 1

_SEARCH_FIELDS = ("title", "description", "category")


@dataclass(frozen=True)
class _FoundRule:
    term: str
    fields: tuple[str, ...]


def _field_texts(lot: AuctionLot) -> dict[str, str]:
    return {
        "title": normalize_text(lot.title),
        "description": normalize_text(lot.description),
        "category": normalize_text(lot.category),
    }


def _find_rule(
    term: str, fields: dict[str, str], context_rules: tuple[ContextRule, ...] = ()
) -> _FoundRule | None:
    matched_fields = tuple(
        field_name for field_name in _SEARCH_FIELDS if contains_term(fields[field_name], term)
    )
    if not matched_fields:
        return None
    full_text = " ".join(fields.values())
    rule = next(
        (rule for rule in context_rules if normalize_term(rule.term) == normalize_term(term)), None
    )
    if rule is not None:
        if rule.required_any and not any(
            contains_term(full_text, item) for item in rule.required_any
        ):
            return None
        if any(contains_term(full_text, item) for item in rule.excluded_any):
            return None
    return _FoundRule(term=term, fields=matched_fields)


def _add_found(
    found: dict[str, _FoundRule],
    rule: _FoundRule,
) -> None:
    key = normalize_term(rule.term)
    existing = found.get(key)
    if existing is None:
        found[key] = rule
        return
    fields = tuple(dict.fromkeys((*existing.fields, *rule.fields)))
    found[key] = _FoundRule(term=existing.term, fields=fields)


def _found_terms(found: dict[str, _FoundRule]) -> tuple[str, ...]:
    return tuple(rule.term for rule in found.values())


def _found_fields(found: dict[str, _FoundRule]) -> dict[str, tuple[str, ...]]:
    return {rule.term: rule.fields for rule in found.values()}


def _human_list(values: Sequence[str]) -> str:
    return ", ".join(f"“{value}”" for value in values)


def _result(
    profile: SearchProfile,
    lot: AuctionLot,
    *,
    matched: bool,
    score: int = 0,
    matched_terms: Sequence[str] = (),
    excluded_terms: Sequence[str] = (),
    missing_required_terms: Sequence[str] = (),
    matched_fields: dict[str, tuple[str, ...]] | None = None,
    rejection_reasons: Sequence[str] = (),
    explanation: str,
) -> MatchResult:
    return MatchResult(
        profile_id=profile.id,
        opportunity_key=lot.opportunity_key,
        matched=matched,
        score=score,
        matched_terms=tuple(matched_terms),
        excluded_terms=tuple(excluded_terms),
        missing_required_terms=tuple(missing_required_terms),
        matched_fields=matched_fields or {},
        rejection_reasons=tuple(rejection_reasons),
        explanation=explanation,
    )


def _price_rejection(profile: SearchProfile, lot: AuctionLot) -> str | None:
    price_filter = profile.price_filter
    if price_filter is None or price_filter.maximum is None:
        return None
    same_currency = (
        lot.price_currency is not None
        and price_filter.currency is not None
        and lot.price_currency == price_filter.currency
    )
    if lot.price_value is None or not same_currency:
        return "unknown_price" if price_filter.on_unknown == "exclude" else None
    if lot.price_value > price_filter.maximum:
        return "price_above_maximum"
    return None


def _price_explanation(profile: SearchProfile, lot: AuctionLot, reason: str) -> str:
    price_filter = profile.price_filter
    if reason == "price_above_maximum" and price_filter is not None:
        return (
            f"Descartado porque el precio {lot.price_label or lot.price_value} "
            f"supera el máximo de {price_filter.maximum} {price_filter.currency}."
        )
    return "Descartado porque el precio es desconocido o usa otra moneda."


def match_lot(profile: SearchProfile, lot: AuctionLot) -> MatchResult:
    """Evaluate one normalized lot against one immutable search profile."""

    if not profile.enabled:
        return _result(
            profile,
            lot,
            matched=False,
            rejection_reasons=("profile_disabled",),
            explanation="Descartado porque el perfil está deshabilitado.",
        )
    if lot.source_id not in profile.source_ids:
        return _result(
            profile,
            lot,
            matched=False,
            rejection_reasons=("source_not_selected",),
            explanation="Descartado porque la fuente no está habilitada en el perfil.",
        )
    if not lot.active:
        return _result(
            profile,
            lot,
            matched=False,
            rejection_reasons=("lot_inactive",),
            explanation="Descartado porque el lote no está activo.",
        )

    fields = _field_texts(lot)
    found: dict[str, _FoundRule] = {}
    excluded: dict[str, _FoundRule] = {}
    for term in profile.exclude_keywords:
        rule = _find_rule(term, fields, profile.context_rules)
        if rule is not None:
            _add_found(excluded, rule)
    if excluded:
        excluded_terms = _found_terms(excluded)
        matched_fields = _found_fields(excluded)
        verb = "aparecieron" if len(excluded_terms) > 1 else "apareció"
        return _result(
            profile,
            lot,
            matched=False,
            excluded_terms=excluded_terms,
            matched_fields=matched_fields,
            rejection_reasons=("excluded_term",),
            explanation=f"Descartado porque {verb} {_human_list(excluded_terms)}.",
        )

    any_found: list[_FoundRule] = []
    all_missing: list[str] = []
    all_found: list[_FoundRule] = []
    exact_found: list[_FoundRule] = []
    for term in profile.keywords_any:
        rule = _find_rule(term, fields, profile.context_rules)
        if rule is not None:
            any_found.append(rule)
            _add_found(found, rule)
    for term in profile.keywords_all:
        rule = _find_rule(term, fields, profile.context_rules)
        if rule is None:
            all_missing.append(term)
        else:
            all_found.append(rule)
            _add_found(found, rule)
    for phrase in profile.exact_phrases:
        rule = _find_rule(phrase, fields, profile.context_rules)
        if rule is not None:
            exact_found.append(rule)
            _add_found(found, rule)

    if all_missing:
        return _result(
            profile,
            lot,
            matched=False,
            matched_terms=_found_terms(found),
            missing_required_terms=tuple(all_missing),
            matched_fields=_found_fields(found),
            rejection_reasons=("missing_required_terms",),
            explanation=f"Descartado porque faltan {_human_list(all_missing)}.",
        )

    all_rules_are_positive = not profile.keywords_any and not profile.exact_phrases
    if not any_found and not exact_found and not (all_found and all_rules_are_positive):
        return _result(
            profile,
            lot,
            matched=False,
            matched_terms=_found_terms(found),
            matched_fields=_found_fields(found),
            rejection_reasons=("no_positive_trigger",),
            explanation="Descartado porque no se encontró un disparador positivo.",
        )

    boosts_found: list[_FoundRule] = []
    boost_weights: dict[str, int] = {}
    for term, weight in profile.boost_keywords.items():
        rule = _find_rule(term, fields, profile.context_rules)
        if rule is not None:
            boosts_found.append(rule)
            boost_weights[term] = weight
            _add_found(found, rule)

    score = sum(ANY_SCORE for _ in any_found)
    score += sum(ALL_SCORE for _ in all_found)
    score += sum(EXACT_PHRASE_SCORE for _ in exact_found)
    score += sum(boost_weights.values())
    score += sum(
        TITLE_BONUS for rule in (*any_found, *all_found, *exact_found) if "title" in rule.fields
    )

    risk_score = sum(
        weight
        for term, weight in profile.risk_keywords.items()
        if _find_rule(term, fields, profile.context_rules) is not None
    )
    score = max(0, score - risk_score)

    price_rejection = _price_rejection(profile, lot)
    matched_fields = _found_fields(found)
    if price_rejection is not None:
        return _result(
            profile,
            lot,
            matched=False,
            score=score,
            matched_terms=_found_terms(found),
            matched_fields=matched_fields,
            rejection_reasons=(price_rejection,),
            explanation=_price_explanation(profile, lot, price_rejection),
        )
    if score < profile.minimum_score:
        return _result(
            profile,
            lot,
            matched=False,
            score=score,
            matched_terms=_found_terms(found),
            matched_fields=matched_fields,
            rejection_reasons=("score_below_minimum",),
            explanation=(
                f"Descartado porque el score {score} es menor que el mínimo "
                f"{profile.minimum_score}."
            ),
        )

    matched_terms = _found_terms(found)
    title_terms = tuple(
        rule.term for rule in (*any_found, *all_found, *exact_found) if "title" in rule.fields
    )
    field_explanation = ", ".join(
        f"“{term}” en {', '.join(matched_fields[term])}" for term in matched_terms
    )
    boost_explanation = (
        f" Boosts aplicados: {_human_list(tuple(rule.term for rule in boosts_found))}."
        if boosts_found
        else ""
    )
    title_explanation = (
        f" Bonus de título aplicado a {_human_list(title_terms)}." if title_terms else ""
    )
    return _result(
        profile,
        lot,
        matched=True,
        score=score,
        matched_terms=matched_terms,
        matched_fields=matched_fields,
        explanation=(
            f"Coincidió por {field_explanation}. Score {score}."
            f"{boost_explanation}{title_explanation}"
        ),
    )


def match_inventory(
    profiles: Sequence[SearchProfile],
    lots: Sequence[AuctionLot],
) -> list[MatchResult]:
    """Evaluate every lot against every profile in caller-provided order."""

    return [match_lot(profile, lot) for profile in profiles for lot in lots]
