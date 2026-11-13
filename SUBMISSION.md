# Submission

## Candidate
- **Name:** Dinesh R
- **Assignment chosen:** A (Data)
- **Time spent:** [Enter your honest estimated hours]

## How to run

Requirements: Python 3.11+

### 1. Set up the environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

### 2. Generate sample data

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

### 3. Ingest the data

```bash
python -m solution.src.pipeline ingest data/drop_1
python -m solution.src.pipeline ingest data/drop_2
```

### 4. Generate reports

```bash
python -m solution.src.pipeline report --out reports/
```

### 5. Run tests

```bash
python -m pytest solution/tests -v
```

## Design

The solution uses Python and SQLite to implement an interaction ledger and reporting pipeline.

### Architecture

The implementation is divided into the following modules:

- `database.py`: SQLite database initialization, encryption, decryption, and HMAC-based phone lookup.
- `validation.py`: Header mapping, phone normalization, date parsing, decimal validation, enum validation, and rejection handling.
- `ingestion.py`: Lead CSV ingestion, duplicate detection, merging, and batch tracking.
- `event_ingestion.py`: JSONL event ingestion, event deduplication, timestamp normalization, and pending event resolution.
- `reporting.py`: Lead metrics, call statistics, bucket calculation, and report generation.
- `pipeline.py`: Command-line interface for ingestion and report generation.

### Database design

The SQLite database contains three main tables:

1. `leads`: Stores workflow information, batch ID, encrypted PII, normalized fields, and a keyed phone hash for identity matching.
2. `events`: Stores append-only interaction events, event IDs, timestamps, call IDs, encrypted phone values, and lead references.
3. `ingested_batches`: Tracks processed lead files and their hashes to prevent repeated ingestion.

An additional `ingested_event_files` table tracks event-file processing and data quality counters.

Phone numbers are encrypted at rest, while HMAC hashes are used for deterministic matching without storing plaintext phone numbers as the lookup key.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| SQLite database | Simple local relational storage for the assignment | PostgreSQL |
| Fernet encryption for PII | Protect sensitive values at rest | Plaintext storage |
| HMAC-SHA256 for phone matching | Deterministic lookup without exposing phone numbers | Plaintext phone lookup |
| Naive event timestamps treated as IST | Follows the assignment's timestamp convention | Treating naive timestamps as UTC |
| First valid lead determines batch ID | Preserves original batch attribution when duplicate leads arrive | Updating batch ID on every duplicate |
| Unknown disposition codes are ignored and counted | Prevents unknown codes from affecting lead status | Treating unknown codes as valid dispositions |

## Failure modes considered

- Invalid or missing required lead fields are rejected.
- Invalid optional fields become null and generate warnings.
- Invalid phone numbers are rejected.
- Duplicate leads are merged using workflow ID and normalized phone identity.
- Repeated lead files are skipped using file tracking.
- Repeated event files are skipped using file tracking.
- Duplicate event IDs are dropped.
- Malformed events are counted and skipped.
- Events without a matching lead remain unlinked and can be resolved when a matching lead arrives.
- PII is encrypted, and phone matching uses HMAC.
- Unknown disposition codes are counted and ignored.

### Known limitations
- There are 461 orphan events remaining after both data drops have been processed. These are retained as unlinked events.
- Rejection and field-warning report information is reconstructed from the original CSV files, so those source files must remain available when generating reports.
- Automated recovery from every possible process crash has not been fully tested.
- Large-scale performance beyond the supplied small dataset has not been benchmarked.

## Testing

The following tests and checks were performed:

- 18 validation tests passed.
- 1 event-linking test passed.
- Full test suite: 19 passed.
- Event ingestion idempotency verified.
- Lead ingestion idempotency verified for both drops.
- CLI help commands verified.
- Custom report output directory tested.
- All five required reports generated and checked.

### Verified results

| Metric | Result |
|---|---:|
| Total unique leads | 1,892 |
| Total stored events | 8,481 |
| Calls | 2,536 |
| Connected calls | 1,467 |
| Rejected lead rows | 108 |
| Duplicate lead rows merged | 57 |
| Malformed events | 8 |
| Duplicate events dropped | 235 |
| Unknown disposition events | 21 |
| Remaining orphan events | 461 |

## Results

Both data drops were successfully ingested.

The final database contains 1,892 unique leads and 8,481 stored events.

The reporting pipeline generates:

1. `batch_summary.csv`
2. `daily_overview.csv`
3. `lead_report.csv`
4. `rejections.csv`
5. `data_quality.json`

The report generation command was tested with a custom output directory.

## If I had another day

1. Add more automated tests for event deduplication and disposition overrides.
2. Investigate the remaining orphan events.
3. Persist rejection and warning details in the database instead of reconstructing them from CSV files.
4. Add stronger crash-recovery and transaction tests.
5. Benchmark the pipeline with a larger dataset.
6. Improve logging and error reporting.