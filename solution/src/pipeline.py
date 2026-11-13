
import argparse
from pathlib import Path

from solution.src.database import initialize_database
from solution.src.ingestion import ingest_lead_file
from solution.src.event_ingestion import (
    ingest_event_file,
    get_orphan_event_count,
)
from solution.src.reporting import generate_reports


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
DISPOSITIONS_PATH = SCHEMA_DIR / "dispositions.csv"


def get_drop_number(drop_path: Path) -> int:
    """Extract the number from a folder such as drop_1."""
    try:
        return int(drop_path.name.split("_")[-1])
    except (ValueError, IndexError):
        raise ValueError(f"Invalid drop folder name: {drop_path.name}")


def ingest_drop(drop_path: Path):
    """Ingest all lead and event files from one data drop."""

    if not drop_path.exists() or not drop_path.is_dir():
        raise FileNotFoundError(
            f"Drop directory not found: {drop_path}"
        )

    initialize_database()

    totals = {
        "processed_lead_files": 0,
        "skipped_lead_files": 0,
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "processed_event_files": 0,
        "skipped_event_files": 0,
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "unknown_disposition_events": 0,
    }

    # Process lead files in workflow and filename order.
    leads_folder = drop_path / "leads"

    if leads_folder.exists():
        csv_files = []

        for workflow_folder in sorted(leads_folder.iterdir()):
            if not workflow_folder.is_dir():
                continue

            for csv_path in sorted(workflow_folder.glob("*.csv")):
                csv_files.append((workflow_folder.name, csv_path))

        for workflow_id, csv_path in csv_files:
            schema_path = SCHEMA_DIR / f"{workflow_id}.json"

            if not schema_path.exists():
                raise FileNotFoundError(
                    f"Schema not found: {schema_path}"
                )

            result = ingest_lead_file(
                csv_path=csv_path,
                workflow_id=workflow_id,
                schema_path=schema_path,
            )

            print(
                f"Lead {workflow_id}/{csv_path.name}: "
                f"{result['status']}"
            )

            if result["status"] == "skipped":
                totals["skipped_lead_files"] += 1
                continue

            totals["processed_lead_files"] += 1

            for key in (
                "lead_rows_total",
                "lead_rows_rejected",
                "duplicate_lead_rows_merged",
            ):
                totals[key] += result[key]

    # Process event files after lead files in this drop.
    events_folder = drop_path / "events"

    if events_folder.exists():
        for event_path in sorted(events_folder.glob("*.jsonl")):
            result = ingest_event_file(
                event_path=event_path,
                dispositions_path=DISPOSITIONS_PATH,
            )

            print(f"Event {event_path.name}: {result['status']}")

            if result["status"] == "skipped":
                totals["skipped_event_files"] += 1
                continue

            totals["processed_event_files"] += 1

            for key in (
                "events_total",
                "malformed_events",
                "duplicate_events_dropped",
                "unknown_disposition_events",
            ):
                totals[key] += result[key]

    print(f"\n========== {drop_path.name} SUMMARY ==========")

    for key, value in totals.items():
        print(f"{key}: {value}")

    print("Remaining orphan events:", get_orphan_event_count())

    return totals


def run_report(output_dir: Path):
    """Generate all five reports in the requested directory."""

    initialize_database()

    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    generate_reports(output_dir=output_dir)


def main():
    """Command-line interface for Assignment A."""

    parser = argparse.ArgumentParser(
        description="Interaction Ledger and Reporting Pipeline"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest one data drop",
    )
    ingest_parser.add_argument(
        "drop_path",
        type=Path,
        help="Path to a drop directory, for example data/drop_1",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Generate all five reports",
    )
    report_parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports"),
        help="Report output directory (default: reports)",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        drop_path = args.drop_path

        if not drop_path.is_absolute():
            drop_path = PROJECT_ROOT / drop_path

        ingest_drop(drop_path)

    elif args.command == "report":
        run_report(args.out)


if __name__ == "__main__":
    main()
