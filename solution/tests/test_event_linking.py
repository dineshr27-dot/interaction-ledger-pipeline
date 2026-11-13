
import json

from solution.src.database import (
    get_connection,
    hash_phone,
    initialize_database,
)
from solution.src.event_ingestion import resolve_pending_events


def test_event_links_when_lead_arrives_later(tmp_path):
    """An earlier orphan event should link when its lead arrives."""

    # Create an isolated temporary database.
    db_path = tmp_path / "test_ledger.db"
    initialize_database(db_path)

    connection = get_connection(db_path)

    try:
        phone = "+919876543210"
        phone_hash = hash_phone(phone)

        # Step 1: Store an event before its lead exists.
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-event-001",
                "call.initiated",
                "motor_renewal",
                "test-call-001",
                "2026-10-10T04:30:00Z",
                None,
                phone_hash,
                None,
                json.dumps({}),
            ),
        )
        connection.commit()

        # Confirm that the event is initially unlinked.
        event = connection.execute(
            """
            SELECT lead_id
            FROM events
            WHERE event_id = ?
            """,
            ("test-event-001",),
        ).fetchone()

        assert event["lead_id"] is None

        # Step 2: Add the matching lead later.
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
                "motor_renewal",
                "batch_test",
                phone_hash,
                json.dumps({"phone": "encrypted-test-value"}),
                json.dumps({"policy_number": "TEST-001"}),
            ),
        )
        connection.commit()

        # Step 3: Retry linking pending events.
        resolve_pending_events(connection)
        connection.commit()

        # Step 4: Verify that the event now links to the lead.
        lead = connection.execute(
            """
            SELECT id
            FROM leads
            WHERE workflow_id = ?
              AND phone_hash = ?
            """,
            ("motor_renewal", phone_hash),
        ).fetchone()

        linked_event = connection.execute(
            """
            SELECT lead_id
            FROM events
            WHERE event_id = ?
            """,
            ("test-event-001",),
        ).fetchone()

        assert lead is not None
        assert linked_event["lead_id"] == lead["id"]

    finally:
        connection.close()
