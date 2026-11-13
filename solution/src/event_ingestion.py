
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from solution.src.database import (
    encrypt_value,
    get_connection,
    hash_phone,
)
from solution.src.validation import normalize_phone


IST = ZoneInfo("Asia/Kolkata")


def load_dispositions(dispositions_path: Path) -> dict:
    """Load valid disposition codes for each workflow."""
    definitions = {}

    with dispositions_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            workflow = row["workflow_id"]
            code = row["code"]

            definitions[(workflow, code)] = {
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }

    return definitions


def normalize_timestamp(value: str) -> str:
    """Convert timestamps to UTC ISO format. Naive timestamps mean IST."""
    timestamp = value.strip()

    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        raise ValueError("invalid_timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)

    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def ensure_event_tracking_table(connection):
    """Create a table to track processed event files."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ingested_event_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_hash TEXT NOT NULL,
            events_total INTEGER NOT NULL DEFAULT 0,
            malformed_events INTEGER NOT NULL DEFAULT 0,
            duplicate_events_dropped INTEGER NOT NULL DEFAULT 0,
            unknown_disposition_events INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA-256 for an event file."""
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def resolve_pending_events(connection):
    """
    Link events to leads whenever possible.

    Phone events use workflow_id + phone_hash.
    Override events use workflow_id + call_id.
    """
    # First resolve events that have a valid phone hash.
    connection.execute(
        """
        UPDATE events
        SET lead_id = (
            SELECT leads.id
            FROM leads
            WHERE leads.workflow_id = events.workflow_id
              AND leads.phone_hash = events.phone_hash
            LIMIT 1
        )
        WHERE lead_id IS NULL
          AND phone_hash IS NOT NULL
        """
    )

    # Then resolve phone-less overrides using another event
    # belonging to the same call.
    connection.execute(
        """
        UPDATE events
        SET lead_id = (
            SELECT linked.lead_id
            FROM events AS linked
            WHERE linked.workflow_id = events.workflow_id
              AND linked.call_id = events.call_id
              AND linked.lead_id IS NOT NULL
            LIMIT 1
        )
        WHERE lead_id IS NULL
          AND event_type = 'disposition.override'
          AND call_id IS NOT NULL
        """
    )


def ingest_event_file(
    event_path: Path,
    dispositions_path: Path,
    db_path: Path | None = None,
) -> dict:
    """Ingest one JSONL event file with event-ID deduplication."""

    if not event_path.is_file():
        raise FileNotFoundError(event_path)

    if not dispositions_path.is_file():
        raise FileNotFoundError(dispositions_path)

    definitions = load_dispositions(dispositions_path)
    file_hash = calculate_file_hash(event_path)
    file_identity = str(event_path.resolve())

    result = {
        "status": "processed",
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "unknown_disposition_events": 0,
    }

    connection = get_connection(db_path)

    try:
        with connection:
            ensure_event_tracking_table(connection)

            existing_file = connection.execute(
                """
                SELECT file_hash
                FROM ingested_event_files
                WHERE file_path = ?
                """,
                (file_identity,),
            ).fetchone()

            if existing_file is not None:
                if existing_file["file_hash"] == file_hash:
                    result["status"] = "skipped"
                    return result

                raise ValueError(
                    f"Event file changed after processing: {event_path}"
                )

            with event_path.open(
                "r", encoding="utf-8-sig"
            ) as file:

                for line in file:
                    if not line.strip():
                        continue

                    result["events_total"] += 1

                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        result["malformed_events"] += 1
                        continue

                    if not isinstance(event, dict) or not event.get("event_id"):
                        result["malformed_events"] += 1
                        continue

                    event_id = str(event["event_id"]).strip()

                    if not event_id:
                        result["malformed_events"] += 1
                        continue

                    # Check duplicates across all previously processed files.
                    duplicate = connection.execute(
                        "SELECT 1 FROM events WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()

                    if duplicate is not None:
                        result["duplicate_events_dropped"] += 1
                        continue

                    event_type = event.get("event_type")
                    workflow_id = event.get("workflow_id")
                    call_id = event.get("call_id")
                    occurred_at = event.get("occurred_at")

                    if (
                        not isinstance(event_type, str)
                        or not isinstance(workflow_id, str)
                        or not isinstance(occurred_at, str)
                        or not occurred_at.strip()
                    ):
                        result["malformed_events"] += 1
                        continue

                    payload = event.get("payload", {})

                    if not isinstance(payload, dict):
                        result["malformed_events"] += 1
                        continue

                    if event_type not in {
                        "call.initiated",
                        "call.telephony_failed",
                        "call.connected",
                        "call.ended",
                        "disposition.set",
                        "disposition.override",
                    }:
                        result["malformed_events"] += 1
                        continue

                    # Validate disposition codes.
                    if event_type in {
                        "disposition.set",
                        "disposition.override",
                    }:
                        code = payload.get("disposition_code")

                        if (workflow_id, code) not in definitions:
                            result["unknown_disposition_events"] += 1
                            continue

                    try:
                        normalized_time = normalize_timestamp(occurred_at)
                    except ValueError:
                        result["malformed_events"] += 1
                        continue

                    # Overrides do not contain phone numbers.
                    raw_phone = event.get("phone")
                    phone_hash = None
                    encrypted_phone = None

                    if raw_phone is not None:
                        if not isinstance(raw_phone, str):
                            result["malformed_events"] += 1
                            continue

                        encrypted_phone = encrypt_value(raw_phone)

                        try:
                            canonical_phone = normalize_phone(raw_phone)
                            phone_hash = hash_phone(canonical_phone)
                        except ValueError:
                            # Keep the event, but leave it unlinked.
                            phone_hash = None

                    connection.execute(
                        """
                        INSERT INTO events (
                            event_id,
                            event_type,
                            workflow_id,
                            call_id,
                            occurred_at,
                            lead_id,
                            phone_hash,
                            encrypted_phone,
                            payload_json
                        )
                        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
                        """,
                        (
                            event_id,
                            event_type,
                            workflow_id,
                            call_id,
                            normalized_time,
                            phone_hash,
                            encrypted_phone,
                            json.dumps(payload),
                        ),
                    )

            # Resolve events after inserting the file.
            resolve_pending_events(connection)

            # Store file counters so repeated runs don't count twice.
            connection.execute(
                """
                INSERT INTO ingested_event_files (
                    file_path,
                    file_hash,
                    events_total,
                    malformed_events,
                    duplicate_events_dropped,
                    unknown_disposition_events
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    file_identity,
                    file_hash,
                    result["events_total"],
                    result["malformed_events"],
                    result["duplicate_events_dropped"],
                    result["unknown_disposition_events"],
                ),
            )

    finally:
        connection.close()

    return result


def get_orphan_event_count(db_path: Path | None = None) -> int:
    """Count events that remain unlinked after resolution."""
    with get_connection(db_path) as connection:
        resolve_pending_events(connection)

        row = connection.execute(
            "SELECT COUNT(*) AS total FROM events WHERE lead_id IS NULL"
        ).fetchone()

        return row["total"]


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]

    dispositions = (
        project_root
        / "assignment-a-data"
        / "schemas"
        / "dispositions.csv"
    )

    events_folder = project_root / "data"

    for drop in sorted(events_folder.glob("drop_*")):
        for event_file in sorted((drop / "events").glob("*.jsonl")):
            result = ingest_event_file(event_file, dispositions)
            print(f"{event_file}: {result}")

    print("Remaining orphan events:", get_orphan_event_count())
