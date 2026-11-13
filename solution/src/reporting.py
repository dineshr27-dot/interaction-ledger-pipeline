
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from solution.src.database import (
    PROJECT_ROOT,
    decrypt_value,
    get_connection,
)
from solution.src.event_ingestion import load_dispositions
from solution.src.ingestion import load_schema
from solution.src.validation import (
    build_header_mapping,
    validate_lead_row,
)


DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
REPORT_DIR = PROJECT_ROOT / "solution" / "reports"

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def get_drop_directories():
    """Return input drop directories in numeric order."""
    drops = [
        path
        for path in DATA_DIR.glob("drop_*")
        if path.is_dir()
    ]

    return sorted(
        drops,
        key=lambda path: int(path.name.split("_")[-1]),
    )


def get_lead_files():
    """Return all lead CSV files in deterministic order."""
    files = []

    for drop in get_drop_directories():
        lead_root = drop / "leads"

        if not lead_root.exists():
            continue

        for workflow_dir in sorted(lead_root.iterdir()):
            if not workflow_dir.is_dir():
                continue

            for csv_path in sorted(workflow_dir.glob("*.csv")):
                files.append((workflow_dir.name, csv_path))

    return files


def parse_timestamp(timestamp: str) -> datetime:
    """Parse a stored timestamp as an aware datetime."""
    value = datetime.fromisoformat(
        timestamp.replace("Z", "+00:00")
    )

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def to_ist(timestamp: str) -> datetime:
    """Convert a stored UTC timestamp into Indian Standard Time."""
    return parse_timestamp(timestamp).astimezone(IST)


def mask_phone(encrypted_phone: str | None) -> str:
    """Decrypt a phone number only to create its masked representation."""
    if not encrypted_phone:
        return ""

    phone = decrypt_value(encrypted_phone)

    if not phone:
        return ""

    digits = "".join(
        character
        for character in phone
        if character.isdigit()
    )

    if len(digits) < 4:
        return ""

    return "+91******" + digits[-4:]


def load_leads(connection):
    """Load lead records without exposing plaintext PII."""
    rows = connection.execute(
        """
        SELECT
            id,
            workflow_id,
            batch_id,
            pii_json,
            fields_json
        FROM leads
        ORDER BY workflow_id, id
        """
    ).fetchall()

    leads = {}

    for row in rows:
        pii = json.loads(row["pii_json"])
        fields = json.loads(row["fields_json"])

        leads[row["id"]] = {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "batch_id": row["batch_id"],
            "fields": fields,
            "phone_masked": mask_phone(pii.get("phone")),
        }

    return leads


def load_call_events(connection):
    """Group linked events by lead and call ID."""
    rows = connection.execute(
        """
        SELECT
            id,
            event_type,
            workflow_id,
            call_id,
            occurred_at,
            lead_id,
            payload_json
        FROM events
        WHERE lead_id IS NOT NULL
        ORDER BY occurred_at, id
        """
    ).fetchall()

    calls = defaultdict(lambda: defaultdict(list))

    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")

        calls[row["lead_id"]][row["call_id"]].append({
            "event_type": row["event_type"],
            "workflow_id": row["workflow_id"],
            "occurred_at": row["occurred_at"],
            "payload": payload,
        })

    return calls


def get_effective_disposition(events, definitions, workflow_id):
    """
    Find the effective disposition for a call.

    A valid override takes priority over disposition.set events.
    If there is no valid override, use the latest valid disposition.set.
    Unknown disposition codes are ignored.
    """

    overrides = [
        event
        for event in events
        if (
            event["event_type"] == "disposition.override"
            and (
                workflow_id,
                event["payload"].get("disposition_code"),
            ) in definitions
        )
    ]

    disposition_events = [
        event
        for event in events
        if (
            event["event_type"] == "disposition.set"
            and (
                workflow_id,
                event["payload"].get("disposition_code"),
            ) in definitions
        )
    ]

    candidates = overrides if overrides else disposition_events

    if not candidates:
        return None, None

    latest = max(
        candidates,
        key=lambda event: parse_timestamp(event["occurred_at"]),
    )

    code = latest["payload"].get("disposition_code")
    definition = definitions.get((workflow_id, code))

    return code, definition


def calculate_lead_details(leads, calls, definitions):
    """Calculate call counts, lead buckets and dispositions."""
    details = {}

    for lead_id, lead in leads.items():
        workflow_id = lead["workflow_id"]
        lead_calls = calls.get(lead_id, {})

        valid_calls = {}

        for call_id, events in lead_calls.items():

            initiated = [
                event
                for event in events
                if event["event_type"] == "call.initiated"
            ]

            # A call counts only when it has an initiated event.
            if not initiated:
                continue

            initiated_event = min(
                initiated,
                key=lambda event: parse_timestamp(
                    event["occurred_at"]
                ),
            )

            connected = any(
                event["event_type"] == "call.connected"
                for event in events
            )

            telephony_failed = any(
                event["event_type"] == "call.telephony_failed"
                for event in events
            )

            disposition_code, disposition = (
                get_effective_disposition(
                    events,
                    definitions,
                    workflow_id,
                )
            )

            valid_calls[call_id] = {
                "initiated_at": initiated_event["occurred_at"],
                "connected": connected,
                "telephony_failed": telephony_failed,
                "disposition_code": disposition_code,
                "disposition": disposition,
            }

        total_calls = len(valid_calls)

        connected_calls = sum(
            1
            for call in valid_calls.values()
            if call["connected"]
        )

        terminal = any(
            call["disposition"] is not None
            and call["disposition"]["is_terminal"]
            for call in valid_calls.values()
        )

        # Apply the required bucket priority.
        if total_calls == 0:
            bucket = "yet_to_reach_out"

        elif terminal:
            bucket = "completed"

        elif all(
            call["telephony_failed"]
            for call in valid_calls.values()
        ):
            bucket = "in_progress_telephony_failed"

        else:
            bucket = "in_progress_dialed"

        latest_call = None

        if valid_calls:
            latest_call = max(
                valid_calls.values(),
                key=lambda call: parse_timestamp(
                    call["initiated_at"]
                ),
            )

        calls_with_disposition = [
            call
            for call in valid_calls.values()
            if call["disposition_code"] is not None
        ]

        current_disposition = ""

        if calls_with_disposition:
            most_recent = max(
                calls_with_disposition,
                key=lambda call: parse_timestamp(
                    call["initiated_at"]
                ),
            )

            current_disposition = most_recent["disposition_code"]

        ever_success = any(
            call["disposition"] is not None
            and call["disposition"]["is_success"]
            for call in valid_calls.values()
        )

        details[lead_id] = {
            "bucket": bucket,
            "calls": valid_calls,
            "total_calls": total_calls,
            "connected_calls": connected_calls,
            "latest_call": latest_call,
            "current_disposition": current_disposition,
            "ever_success": ever_success,
        }

    return details


def write_csv(path, columns, rows):
    """Write a CSV report with a fixed column order."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=columns,
        )

        writer.writeheader()
        writer.writerows(rows)


def build_batch_summary(leads, details):
    """Build lead bucket counts for each workflow and batch."""
    summary = defaultdict(lambda: {
        "total_leads": 0,
        "yet_to_reach_out": 0,
        "in_progress_telephony_failed": 0,
        "in_progress_dialed": 0,
        "completed": 0,
    })

    for lead_id, lead in leads.items():
        key = (
            lead["workflow_id"],
            lead["batch_id"],
        )

        bucket = details[lead_id]["bucket"]

        summary[key]["total_leads"] += 1
        summary[key][bucket] += 1

    rows = []

    for (workflow_id, batch_id), counts in sorted(summary.items()):
        rows.append({
            "workflow_id": workflow_id,
            "batch_id": batch_id,
            **counts,
        })

    return rows


def build_daily_rows(leads, details):
    """Build daily call statistics using IST dates."""
    daily = defaultdict(lambda: {
        "total_calls": 0,
        "connected_calls": 0,
        "success_leads": set(),
    })

    for lead_id, detail in details.items():
        workflow_id = leads[lead_id]["workflow_id"]

        for call in detail["calls"].values():
            call_date = (
                to_ist(call["initiated_at"])
                .date()
                .isoformat()
            )

            key = (workflow_id, call_date)

            daily[key]["total_calls"] += 1

            if call["connected"]:
                daily[key]["connected_calls"] += 1

            disposition = call["disposition"]

            if disposition and disposition["is_success"]:
                daily[key]["success_leads"].add(lead_id)

    rows = []

    for (workflow_id, date), values in sorted(daily.items()):
        total = values["total_calls"]
        connected = values["connected_calls"]

        rows.append({
            "workflow_id": workflow_id,
            "date": date,
            "total_calls": total,
            "connected_calls": connected,
            "connectivity_rate": f"{connected / total:.4f}",
            "success_leads": len(values["success_leads"]),
        })

    return rows


def build_lead_report(leads, details):
    """Build one report row for every lead."""
    rows = []

    # Load each workflow schema once.
    reference_fields = {}

    for workflow_id in {
        lead["workflow_id"]
        for lead in leads.values()
    }:
        schema = load_schema(
            SCHEMA_DIR / f"{workflow_id}.json"
        )

        reference_fields[workflow_id] = [
            field["name"]
            for field in schema["fields"]
            if field.get("reference", False)
        ]

    for lead_id, lead in leads.items():
        detail = details[lead_id]
        latest = detail["latest_call"]

        refs = reference_fields[lead["workflow_id"]]

        external_ref = ""

        if refs:
            external_ref = lead["fields"].get(
                refs[0],
                "",
            ) or ""

        last_call_at = ""

        if latest:
            last_call_at = to_ist(
                latest["initiated_at"]
            ).isoformat()

        rows.append({
            "workflow_id": lead["workflow_id"],
            "external_ref": external_ref,
            "batch_id": lead["batch_id"],
            "phone_masked": lead["phone_masked"],
            "bucket": detail["bucket"],
            "total_calls": detail["total_calls"],
            "connected_calls": detail["connected_calls"],
            "last_call_at": last_call_at,
            "current_disposition": detail["current_disposition"],
            "ever_success": str(
                detail["ever_success"]
            ).lower(),
        })

    return sorted(
        rows,
        key=lambda row: (
            row["workflow_id"],
            row["external_ref"],
        ),
    )


def build_lead_quality():
    """
    Re-read the original CSV files to reconstruct lead quality counters.

    This is necessary because rejection and warning details are not
    currently persisted in the database.
    """
    total_rows = 0
    rejected_rows = 0
    duplicate_rows = 0
    field_warnings = 0

    rejections = []
    rejection_counts = defaultdict(int)
    seen_phones = set()

    for workflow_id, csv_path in get_lead_files():
        schema_path = SCHEMA_DIR / f"{workflow_id}.json"
        schema = load_schema(schema_path)

        with csv_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:

            reader = csv.reader(file)
            headers = next(reader, None)

            if headers is None:
                continue

            mapping = build_header_mapping(headers, schema)
            row_number = 0

            for row in reader:

                if not row or all(
                    not value.strip()
                    for value in row
                ):
                    continue

                row_number += 1
                total_rows += 1

                values, rejection_reason, warnings = (
                    validate_lead_row(
                        row,
                        mapping,
                        schema,
                    )
                )

                if rejection_reason:
                    rejected_rows += 1
                    rejection_counts[rejection_reason] += 1

                    rejections.append({
                        "workflow_id": workflow_id,
                        "batch_id": csv_path.stem,
                        "row_number": row_number,
                        "reason": rejection_reason,
                    })

                    continue

                field_warnings += len(warnings)

                identity = (
                    workflow_id,
                    values["phone"],
                )

                if identity in seen_phones:
                    duplicate_rows += 1
                else:
                    seen_phones.add(identity)

    rejections.sort(
        key=lambda row: (
            row["workflow_id"],
            row["batch_id"],
            row["row_number"],
        )
    )

    return {
        "lead_rows_total": total_rows,
        "lead_rows_rejected": rejected_rows,
        "duplicate_lead_rows_merged": duplicate_rows,
        "field_warnings": field_warnings,
        "rejections": rejections,
        "rejections_by_reason": dict(
            sorted(rejection_counts.items())
        ),
    }


def build_event_quality(connection):
    """Read event counters from the event-file tracking table."""
    table_exists = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'ingested_event_files'
        """
    ).fetchone()

    if table_exists is None:
        return {
            "events_total": 0,
            "malformed_events": 0,
            "duplicate_events_dropped": 0,
            "unknown_disposition_events": 0,
        }

    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(events_total), 0) AS events_total,
            COALESCE(SUM(malformed_events), 0) AS malformed_events,
            COALESCE(SUM(duplicate_events_dropped), 0)
                AS duplicate_events_dropped,
            COALESCE(SUM(unknown_disposition_events), 0)
                AS unknown_disposition_events
        FROM ingested_event_files
        """
    ).fetchone()

    return dict(row)


def generate_reports(
    db_path: Path | None = None,
    output_dir: Path | None = None,
):
    """Generate all five required assignment reports."""
    report_dir = (
        Path(output_dir) if output_dir is not None else REPORT_DIR
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    connection = get_connection(db_path)

    try:
        leads = load_leads(connection)
        calls = load_call_events(connection)

        # Pass the actual disposition CSV path.
        definitions = load_dispositions(
            SCHEMA_DIR / "dispositions.csv"
        )

        details = calculate_lead_details(
            leads,
            calls,
            definitions,
        )

        batch_rows = build_batch_summary(
            leads,
            details,
        )

        daily_rows = build_daily_rows(
            leads,
            details,
        )

        lead_rows = build_lead_report(
            leads,
            details,
        )

        lead_quality = build_lead_quality()
        event_quality = build_event_quality(connection)

        orphan_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE lead_id IS NULL
            """
        ).fetchone()[0]

        # 1. Batch summary
        write_csv(
            report_dir / "batch_summary.csv",
            [
                "workflow_id",
                "batch_id",
                "total_leads",
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            ],
            batch_rows,
        )

        # 2. Daily overview
        write_csv(
            report_dir / "daily_overview.csv",
            [
                "workflow_id",
                "date",
                "total_calls",
                "connected_calls",
                "connectivity_rate",
                "success_leads",
            ],
            daily_rows,
        )

        # 3. Lead report
        write_csv(
            report_dir / "lead_report.csv",
            [
                "workflow_id",
                "external_ref",
                "batch_id",
                "phone_masked",
                "bucket",
                "total_calls",
                "connected_calls",
                "last_call_at",
                "current_disposition",
                "ever_success",
            ],
            lead_rows,
        )

        # 4. Rejections
        write_csv(
            report_dir / "rejections.csv",
            [
                "workflow_id",
                "batch_id",
                "row_number",
                "reason",
            ],
            lead_quality["rejections"],
        )

        # 5. Data quality
        quality = {
            "lead_rows_total": lead_quality["lead_rows_total"],
            "lead_rows_rejected": lead_quality["lead_rows_rejected"],
            "rejections_by_reason": lead_quality["rejections_by_reason"],
            "duplicate_lead_rows_merged": (
                lead_quality["duplicate_lead_rows_merged"]
            ),
            "field_warnings": lead_quality["field_warnings"],
            "events_total": event_quality["events_total"],
            "malformed_events": event_quality["malformed_events"],
            "duplicate_events_dropped": (
                event_quality["duplicate_events_dropped"]
            ),
            "orphan_events": orphan_count,
            "unknown_disposition_events": (
                event_quality["unknown_disposition_events"]
            ),
        }

        with (
            report_dir / "data_quality.json"
        ).open("w", encoding="utf-8") as file:
            json.dump(
                quality,
                file,
                indent=2,
            )

        total_calls = sum(
            detail["total_calls"]
            for detail in details.values()
        )

        print("Reports generated successfully.")
        print(f"Output directory: {report_dir}")
        print(f"Leads: {len(leads)}")
        print(f"Calls: {total_calls}")
        print(f"Orphan events: {orphan_count}")

    finally:
        connection.close()


if __name__ == "__main__":
    generate_reports()
