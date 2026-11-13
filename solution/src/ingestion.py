
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from solution.src.database import (
    encrypt_value,
    get_connection,
    hash_phone,
)
from solution.src.validation import (
    build_header_mapping,
    validate_lead_row,
)
from solution.src.event_ingestion import resolve_pending_events


def load_schema(schema_path: Path) -> dict:
    """Load a workflow schema from JSON."""
    with schema_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def calculate_file_hash(csv_path: Path) -> str:
    """Calculate a SHA-256 fingerprint of a CSV file."""
    digest = hashlib.sha256()

    with csv_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def split_fields(values: dict, schema: dict):
    """Separate encrypted PII fields from ordinary fields."""
    pii_fields = {}
    ordinary_fields = {}

    for field in schema["fields"]:
        name = field["name"]
        value = values.get(name)

        if field.get("pii", False):
            pii_fields[name] = (
                encrypt_value(str(value)) if value is not None else None
            )
        else:
            ordinary_fields[name] = value

    return pii_fields, ordinary_fields


def save_or_merge_lead(
    connection: sqlite3.Connection,
    workflow_id: str,
    batch_id: str,
    values: dict,
    schema: dict,
) -> bool:
    """Save a new lead or merge it with an existing lead."""

    phone_hash = hash_phone(values["phone"])
    new_pii, new_ordinary = split_fields(values, schema)

    existing = connection.execute(
        """
        SELECT id, pii_json, fields_json
        FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, phone_hash),
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO leads (
                workflow_id,
                batch_id,
                phone_hash,
                pii_json,
                fields_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                batch_id,
                phone_hash,
                json.dumps(new_pii),
                json.dumps(new_ordinary),
            ),
        )
        return False

    old_pii = json.loads(existing["pii_json"])
    old_ordinary = json.loads(existing["fields_json"])

    # Later non-null values overwrite earlier values.
    for name, value in new_pii.items():
        if value is not None:
            old_pii[name] = value

    for name, value in new_ordinary.items():
        if value is not None:
            old_ordinary[name] = value

    # Preserve the original batch_id.
    connection.execute(
        """
        UPDATE leads
        SET pii_json = ?,
            fields_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(old_pii),
            json.dumps(old_ordinary),
            existing["id"],
        ),
    )

    return True


def ingest_lead_file(
    csv_path: Path,
    workflow_id: str,
    schema_path: Path,
    db_path: Path | None = None,
) -> dict:
    """Ingest one CSV file with batch-level idempotency."""

    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    schema = load_schema(schema_path)
    batch_id = csv_path.stem
    file_hash = calculate_file_hash(csv_path)

    result = {
        "status": "processed",
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "rejections": [],
        "field_warnings": [],
    }

    connection = get_connection(db_path)

    try:
        with connection:

            # Check whether this batch was already processed.
            existing_batch = connection.execute(
                """
                SELECT file_hash
                FROM ingested_batches
                WHERE workflow_id = ? AND batch_id = ?
                """,
                (workflow_id, batch_id),
            ).fetchone()

            if existing_batch is not None:
                if existing_batch["file_hash"] == file_hash:
                    result["status"] = "skipped"
                    return result

                raise ValueError(
                    f"Batch '{batch_id}' for workflow "
                    f"'{workflow_id}' was already processed "
                    "with different file contents."
                )

            with csv_path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as file:

                reader = csv.reader(file)
                headers = next(reader, None)

                if headers is not None:
                    mapping = build_header_mapping(headers, schema)

                    for row in reader:

                        # Blank rows do not count.
                        if not row or all(
                            not value.strip() for value in row
                        ):
                            continue

                        result["lead_rows_total"] += 1
                        row_number = result["lead_rows_total"]

                        values, rejection_reason, warnings = (
                            validate_lead_row(row, mapping, schema)
                        )

                        if rejection_reason:
                            result["lead_rows_rejected"] += 1
                            result["rejections"].append({
                                "workflow_id": workflow_id,
                                "batch_id": batch_id,
                                "row_number": row_number,
                                "reason": rejection_reason,
                            })
                            continue

                        result["field_warnings"].extend(warnings)

                        was_merged = save_or_merge_lead(
                            connection=connection,
                            workflow_id=workflow_id,
                            batch_id=batch_id,
                            values=values,
                            schema=schema,
                        )

                        if was_merged:
                            result[
                                "duplicate_lead_rows_merged"
                            ] += 1

            # Recheck previously stored events against newly added leads.
            resolve_pending_events(connection)

            # Mark the batch only after all rows have been processed.
            connection.execute(
                """
                INSERT INTO ingested_batches (
                    workflow_id,
                    batch_id,
                    file_hash
                )
                VALUES (?, ?, ?)
                """,
                (workflow_id, batch_id, file_hash),
            )

    finally:
        connection.close()

    return result
