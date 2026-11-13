
import hashlib
import hmac
import json
import secrets
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet


# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY_DIR = PROJECT_ROOT / ".keys"
DATABASE_PATH = PROJECT_ROOT / "solution" / "interaction_ledger.db"


def load_or_create_key(filename: str, key_generator) -> bytes:
    """Load a key from disk or create it once."""
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key_path = KEY_DIR / filename

    if key_path.exists():
        return key_path.read_bytes()

    key = key_generator()
    key_path.write_bytes(key)
    return key


def get_encryption_key() -> bytes:
    return load_or_create_key("fernet.key", Fernet.generate_key)


def get_hmac_key() -> bytes:
    return load_or_create_key(
        "hmac.key",
        lambda: secrets.token_bytes(32)
    )


def encrypt_value(value: str | None) -> str | None:
    """Encrypt a PII value before storing it."""
    if value is None:
        return None

    cipher = Fernet(get_encryption_key())
    return cipher.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str | None) -> str | None:
    """Decrypt a PII value when needed."""
    if value is None:
        return None

    cipher = Fernet(get_encryption_key())
    return cipher.decrypt(value.encode("utf-8")).decode("utf-8")


def hash_phone(phone: str) -> str:
    """Create a stable keyed hash for secure phone matching."""
    key = get_hmac_key()

    return hmac.new(
        key,
        phone.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def get_connection(db_path: Path | None = None):
    """Open a SQLite database connection."""
    if db_path is None:
        db_path = DATABASE_PATH

    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database(db_path: Path | None = None):
    """Create all required database tables and indexes."""

    with get_connection(db_path) as connection:

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                phone_hash TEXT NOT NULL,
                pii_json TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workflow_id, phone_hash)
            );

            CREATE TABLE IF NOT EXISTS ingested_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workflow_id, batch_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                call_id TEXT,
                occurred_at TEXT NOT NULL,
                lead_id INTEGER,
                phone_hash TEXT,
                encrypted_phone TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            );

            CREATE INDEX IF NOT EXISTS idx_leads_workflow
            ON leads(workflow_id);

            CREATE INDEX IF NOT EXISTS idx_events_call
            ON events(workflow_id, call_id);

            CREATE INDEX IF NOT EXISTS idx_events_lead
            ON events(lead_id);

            CREATE INDEX IF NOT EXISTS idx_batches_workflow
            ON ingested_batches(workflow_id, batch_id);
            """
        )


if __name__ == "__main__":
    initialize_database()
    print("Database initialized successfully!")
