
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalize_header(header: str) -> str:
    """Normalize CSV headers for matching."""
    header = header.strip().lower()
    return re.sub(r"[_\s]+", " ", header)


def normalize_phone(value: str) -> str:
    """Normalize an Indian phone number to +91XXXXXXXXXX."""
    digits = re.sub(r"\D", "", value)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("invalid_phone")

    return "+91" + digits


def normalize_date(value: str) -> str:
    """Parse supported date formats and return YYYY-MM-DD."""
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    ]

    for date_format in formats:
        try:
            return datetime.strptime(
                value.strip(), date_format
            ).date().isoformat()
        except ValueError:
            continue

    raise ValueError("invalid_date")


def normalize_decimal(value: str) -> str:
    """Parse an optional currency prefix and return 2 decimal places."""
    cleaned = value.strip()

    cleaned = re.sub(
        r"^(?:₹|Rs\.?|INR)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.replace(",", "")

    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("invalid_decimal")

    if not number.is_finite() or number < 0:
        raise ValueError("invalid_decimal")

    number = number.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return f"{number:.2f}"


def normalize_enum(value: str, allowed_values: list[str]) -> str:
    """Match an enum value without case sensitivity."""
    normalized = value.strip().lower()
    allowed = [item.lower() for item in allowed_values]

    if normalized not in allowed:
        raise ValueError("invalid_enum")

    return normalized


def normalize_field_value(value: str, field: dict):
    """Normalize one field according to its schema type."""
    value = value.strip()

    if not value:
        return None

    field_type = field["type"]

    if field_type == "string":
        return value

    if field_type == "phone":
        return normalize_phone(value)

    if field_type == "date":
        return normalize_date(value)

    if field_type == "decimal":
        return normalize_decimal(value)

    if field_type == "enum":
        return normalize_enum(value, field["values"])

    raise ValueError(f"unsupported_type:{field_type}")


def build_header_mapping(headers: list[str], schema: dict) -> dict:
    """Map CSV column positions to schema field names."""
    mapping = {}

    for index, header in enumerate(headers):
        normalized_header = normalize_header(header)

        for field in schema["fields"]:
            accepted_names = [field["name"]] + field.get("aliases", [])

            normalized_names = {
                normalize_header(name) for name in accepted_names
            }

            if normalized_header in normalized_names:
                mapping[field["name"]] = index
                break

    return mapping


def validate_lead_row(row: list[str], mapping: dict, schema: dict):
    """
    Validate a CSV row.

    Returns:
        (normalized_values, rejection_reason, field_warnings)
    """
    normalized_values = {}
    warnings = []

    for field in schema["fields"]:
        name = field["name"]
        column_index = mapping.get(name)

        raw_value = (
            row[column_index].strip()
            if column_index is not None and column_index < len(row)
            else ""
        )

        if not raw_value:
            normalized_values[name] = None

            if field.get("required", False):
                return {}, f"missing_required:{name}", []

            continue

        try:
            normalized_values[name] = normalize_field_value(
                raw_value, field
            )

        except ValueError as error:
            reason = str(error)

            if field.get("required", False):
                return {}, reason, []

            normalized_values[name] = None
            warnings.append(name)

    return normalized_values, None, warnings
