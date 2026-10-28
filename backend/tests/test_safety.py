import pytest

from aira.safety import check, evidence_numbers, spoken_numbers, words_to_digits

CATALOG = [
    {"sku": "BALL-TEN", "name": "Tennis Ball", "unit": "piece", "price": 60, "sam_noun": "tennis ball",
     "aliases": {"en": ["tennis ball"], "ta": ["டென்னிஸ் பந்து"], "hi": ["टेनिस बॉल"]}},
    {"sku": "BAT-KW", "name": "Kashmir Willow Bat", "unit": "piece", "price": 1200,
     "aliases": {"en": ["kashmir bat"], "ta": ["மட்டை"], "hi": ["बल्ला"]}},
    {"sku": "GRIP", "name": "Bat Grip", "unit": "piece", "price": 150, "aliases": {"en": ["grip"]}},
]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("two hundred and fifty rupees", "250 rupees"),
        ("twenty five balls", "25 balls"),
        ("one thousand two hundred rupees", "1200 rupees"),
        ("2 thousand rupees", "2000 rupees"),
        ("one lakh", "100000"),
        ("twelve tennis balls", "12 tennis balls"),
        ("the first ball", "the first ball"),
        ("someone came", "someone came"),
        ("one and two", "1 and 2"),
        ("hundred percent", "hundred percent"),
        ("Forty-five", "45"),
    ],
)
def test_words_to_digits(text, expected):
    assert words_to_digits(text) == expected


def test_price_from_catalog_allowed():
    assert check("A tennis ball is 60 rupees.", [], CATALOG) == []
    assert check("The Kashmir bat costs ₹1,200.", [], CATALOG) == []


def test_invented_money_flagged():
    v = check("That will be 450 rupees.", [], CATALOG)
    assert len(v) == 1 and "450" in v[0]


def test_money_from_tool_result_allowed():
    results = [{"status": "ok", "sale_id": "S-1", "total": 450, "say_en": "Total 450 rupees"}]
    assert check("Total is four hundred and fifty rupees.", results, CATALOG) == []
    assert check("Total is Rs. 450", results, CATALOG) == []
    assert check("Total is Rs450", results, CATALOG) == []
    assert check("Total is 450/-", results, CATALOG) == []


def test_nested_and_numeric_string_values():
    results = [{"status": "ok", "sale": {"items": [{"sku": "GRIP", "qty": 3, "line_total": "450"}]}}]
    assert check("Three grips, 450 rupees.", results, CATALOG) == []


def test_tamil_and_hindi_money_units():
    assert check("மொத்தம் 999 ரூபாய்", [], CATALOG) != []
    assert check("कुल 999 रुपये", [], CATALOG) != []
    assert check("மொத்தம் 60 ரூபாய்", [], CATALOG) == []
    assert check("कुल 1200 रुपये", [], CATALOG) == []


def test_count_needs_evidence():
    v = check("You have 14 tennis balls.", [], CATALOG)
    assert len(v) == 1 and "14" in v[0]
    assert check("You have 14 tennis balls.", [{"status": "ok", "on_hand": 14}], CATALOG) == []
    assert check("I counted ten pieces.", [{"counted": 10}], CATALOG) == []
    assert check("Only 7 left.", [], CATALOG) != []
    assert check("7 in stock", [{"on_hand": 7}], CATALOG) == []


def test_tamil_product_word_count():
    assert check("5 மட்டை உள்ளது", [], CATALOG) != []
    assert check("5 மட்டை உள்ளது", [{"on_hand": 5}], CATALOG) == []


def test_hedged_counts_allowed():
    assert check("I see about 10 tennis balls.", [], CATALOG) == []
    assert check("Around twelve balls, roughly.", [], CATALOG) == []
    assert check("சுமார் 10 பந்து", [], [*CATALOG, {"name": "பந்து"}]) == []


def test_hedge_does_not_excuse_money():
    assert check("About 500 rupees.", [], CATALOG) != []


def test_external_results_ignored():
    results = [{"status": "ok", "external": True, "say_en": "Online it sells for 999 rupees", "price": 999}]
    assert check("Online it is 999 rupees.", results, CATALOG) != []
    nested = [{"status": "ok", "web": {"external": True, "price": 999}, "total": 60}]
    assert check("It is 999 rupees.", nested, CATALOG) != []


@pytest.mark.parametrize(
    "spoken",
    [
        "Come back at 5 pm.",
        "It is 5:30 now.",
        "The 1st sale today was good.",
        "Sale S1234 is open.",
        "Give me 2 minutes.",
        "Say yes in one moment.",
        "That is the second bat.",
        "100% sure.",
        "Number 7 is the best.",
    ],
)
def test_no_false_positives(spoken):
    assert check(spoken, [], CATALOG) == []


def test_ids_are_not_evidence():
    results = [{"status": "ok", "sale_id": "S-777", "confirm_id": "c777"}]
    assert check("777 rupees", results, CATALOG) != []


def test_evidence_walks_types():
    ev = evidence_numbers([{"a": [1, {"b": 2.0}], "c": "3 balls, 4,500 rupees", "ok": True}], CATALOG)
    assert {1, 2, 3, 4500, 60, 1200, 150} <= ev
    assert True not in {x for x in ev if x is True}


def test_bool_is_not_evidence():
    assert check("1 grip", [{"needs_confirm": True}], CATALOG) != []


def test_spoken_numbers_kinds():
    found = spoken_numbers("2 grips for 300 rupees", CATALOG)
    kinds = [s.split(":")[0] for s, _, _ in found]
    assert kinds == ["count", "money"]
    assert [v for _, v, _ in found] == [2, 300]


def test_empty():
    assert check("", [], CATALOG) == []
    assert check("Okay, done.", [], []) == []


def test_duplicate_violation_reported_once():
    v = check("450 rupees. Yes, 450 rupees.", [], CATALOG)
    assert len(v) == 1
