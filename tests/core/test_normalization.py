from auction_watch.core.normalization import (
    contains_term,
    dedupe_terms,
    normalize_phrase,
    normalize_term,
    normalize_text,
    tokenize,
)


def test_normalization_ignores_case_accents_punctuation_and_spaces() -> None:
    assert normalize_text("  José—PlayStation-2!  ") == "jose playstation 2"
    assert normalize_term("Cámara") == "camara"
    assert normalize_phrase("Rock argentino") == "rock argentino"


def test_tokenization_and_word_boundaries() -> None:
    assert tokenize("parte arte") == ("parte", "arte")
    assert contains_term("Una parte", "arte") is False
    assert contains_term("Una pieza de arte", "arte") is True
    assert contains_term("PlayStation-2", "playstation 2") is True


def test_phrase_order_is_preserved() -> None:
    assert contains_term("rock argentino", "rock argentino") is True
    assert contains_term("argentino rock", "rock argentino") is False


def test_dedupe_is_stable_and_preserves_readable_spelling() -> None:
    assert dedupe_terms([" José ", "jose", "", "Edición", "edicion"]) == ["José", "Edición"]
