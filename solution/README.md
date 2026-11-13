# Interaction Ledger & Reporting Pipeline

## 1. Project Overview

The Interaction Ledger & Reporting Pipeline is a Python-based application that processes lead records and call events for two business workflows:

- Motor Renewal
- Policy Feedback

The application validates lead data, handles duplicate records, stores call events, links events with leads, protects personal information, and generates reports.

## 2. Technologies Used

- Python 3.11
- SQLite
- SQL
- Cryptography (Fernet encryption)
- HMAC-SHA256
- Pytest

## 3. Project Structure

```text
solution/
│
├── __init__.py
│
├── src/
│   ├── __init__.py
│   ├── database.py
│   ├── validation.py
│   ├── ingestion.py
│   ├── event_ingestion.py
│   ├── pipeline.py
│   └── reporting.py
│
├── tests/
│   ├── __init__.py
│   ├── test_validation.py
│   └── test_event_linking.py
│
└── README.md
```

## 4. Setup Instructions

Run the following commands from the project root directory.

### Create a virtual environment

```powershell
python -m venv .venv
```

### Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### Install dependencies

```powershell
pip install -r requirements.txt
```

## 5. Generate Sample Data

Generate the assignment's sample dataset:

```powershell
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

This generates two data drops:

- `data/drop_1`
- `data/drop_2`

## 6. Run the Pipeline

### Ingest the first data drop

```powershell
python -m solution.src.pipeline ingest data/drop_1
```

### Ingest the second data drop

```powershell
python -m solution.src.pipeline ingest data/drop_2
```

### Generate reports

```powershell
python -m solution.src.pipeline report --out solution/reports
```

## 7. Main Features

### Lead Ingestion and Validation

- Normalizes column names and supports schema aliases.
- Validates required and optional fields.
- Normalizes Indian phone numbers.
- Supports multiple date formats.
- Validates decimal and enum values.
- Rejects invalid required fields.
- Records warnings for invalid optional fields.
- Merges duplicate leads using workflow ID and normalized phone number.

### Event Ingestion

- Reads JSONL event files.
- Validates event structures and timestamps.
- Prevents duplicate event ingestion.
- Links events to leads using workflow ID and phone number.
- Links disposition overrides using call ID.
- Retains unlinked events for later resolution.
- Tracks malformed and unknown disposition events.

### Data Protection

- Encrypts PII fields using Fernet.
- Uses HMAC-SHA256 for phone-number matching.
- Keeps encryption keys outside the source code.

### Incremental Processing

- Tracks ingested lead batches and event files.
- Prevents repeated processing of unchanged input files.
- Supports resolving previously unlinked events when matching leads become available.

## 8. Lead Status Buckets

Each lead is classified into one of four buckets:

| Bucket | Description |
|---|---|
| `yet_to_reach_out` | No initiated calls |
| `completed` | At least one call has a terminal disposition |
| `in_progress_telephony_failed` | Every initiated call has a telephony failure |
| `in_progress_dialed` | Other leads with initiated calls |

## 9. Generated Reports

The pipeline generates five reports:

| Report | Description |
|---|---|
| `batch_summary.csv` | Lead counts by workflow, batch, and status |
| `daily_overview.csv` | Daily call and connectivity statistics |
| `lead_report.csv` | Lead-level call and disposition information |
| `rejections.csv` | Rejected lead records and reasons |
| `data_quality.json` | Data quality and ingestion statistics |

Report dates use the Asia/Kolkata timezone.

## 10. Run Tests

Run the test suite:

```powershell
python -m pytest solution/tests -v
```

The current test suite contains 19 tests covering data validation and event linking.

## 11. Sample Execution Results

For the generated small dataset, the current execution produced:

| Metric | Result |
|---|---:|
| Unique leads | 1,892 |
| Rejected lead rows | 108 |
| Duplicate lead rows merged | 57 |
| Events processed | 8,745 |
| Malformed events | 8 |
| Duplicate events dropped | 235 |
| Unknown disposition events | 21 |
| Remaining orphan events | 461 |

These results are specific to the generated sample dataset.

## 12. Design Decisions

- SQLite is used for persistent data storage.
- Lead identity is determined by workflow ID and HMAC of the normalized phone number.
- PII is encrypted before being stored.
- Events are maintained in an append-only ledger.
- Duplicate processing is controlled through batch and event-file tracking.
- Reports are generated from the stored records.

## 13. Limitations and Future Improvements

- Add more automated tests for complete pipeline and reporting behavior.
- Improve crash recovery and transaction handling.
- Benchmark performance with larger datasets.
- Add structured logging and monitoring.
- Develop a dashboard for report visualization.

## 14. Conclusion

This project demonstrates an end-to-end data processing pipeline for lead ingestion, validation, event tracking, data protection, and reporting.

It uses Python and SQLite to provide a structured and repeatable approach to managing interaction data.