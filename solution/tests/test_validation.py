
import pytest

from solution.src.validation import (
    normalize_phone,
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
)


# PHONE TESTS

def test_valid_phone():
    assert normalize_phone("9876543210") == "+919876543210"


def test_phone_with_country_code():
    assert normalize_phone("+91 98765 43210") == "+919876543210"


def test_phone_with_leading_zero():
    assert normalize_phone("09876543210") == "+919876543210"


@pytest.mark.parametrize(
    "phone",
    ["1234567890", "987654321", "12345678901", "abcdefghij"],
)
def test_invalid_phone(phone):
    with pytest.raises(ValueError):
        normalize_phone(phone)


# DATE TESTS

def test_iso_date():
    assert normalize_date("2026-11-21") == "2026-11-21"


def test_slash_date():
    assert normalize_date("21/11/2026") == "2026-11-21"


def test_month_name_date():
    assert normalize_date("21-Nov-2026") == "2026-11-21"


def test_impossible_date():
    with pytest.raises(ValueError):
        normalize_date("31/02/2026")


# DECIMAL TESTS

def test_decimal_with_currency():
    assert normalize_decimal("INR 25301.4") == "25301.40"


def test_decimal_with_rupee_symbol():
    assert normalize_decimal("₹27,554") == "27554.00"


def test_negative_decimal():
    with pytest.raises(ValueError):
        normalize_decimal("-100")


def test_invalid_decimal():
    with pytest.raises(ValueError):
        normalize_decimal("not-a-number")


# ENUM TESTS

def test_valid_enum():
    assert normalize_enum("TA", ["en", "hi", "ta"]) == "ta"


def test_invalid_enum():
    with pytest.raises(ValueError):
        normalize_enum("Tamil", ["en", "hi", "ta"])


# HEADER TESTS

def test_header_normalization():
    assert normalize_header(" Customer__Name ") == "customer name"
