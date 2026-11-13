# AI Usage Disclosure

## Tools used
- ChatGPT (OpenAI)

## What I used them for
- Understanding the assignment requirements and breaking the work into smaller tasks.
- Designing the SQLite database structure for leads, events, and ingestion tracking.
- Implementing phone number normalization, date parsing, decimal validation, and enum validation.
- Developing lead ingestion, duplicate handling, event linking, and orphan event resolution.
- Implementing encryption for PII and HMAC-based phone matching.
- Developing report generation and the command-line interface.
- Writing unit tests and debugging Python errors.
- Reviewing the implementation and preparing documentation.

## What I wrote or decided myself
- Reviewed the assignment requirements and selected Assignment A.
- Set up the Python environment and project structure.
- Generated and inspected the sample datasets.
- Ran the implementation and checked the database results.
- Reviewed the validation rules and business logic.
- Executed tests and verified ingestion idempotency.
- Checked report outputs and reconciled lead and event counts.
- Made final decisions about the implementation after reviewing and testing the suggestions.

## Where the AI was wrong or unhelpful
- The initial pipeline implementation processed lead CSV files but did not include event ingestion in the same CLI workflow.
- I identified this gap by comparing the implementation with the required assignment commands.
- I inspected the existing event ingestion function before updating the pipeline.
- The report generator also initially used a fixed output directory. I updated it to support the required `--out` argument.

## How I verified AI-generated code
- Ran Python syntax checks using `py_compile`.
- Executed the unit test suite: 19 tests passed.
- Tested event linking when a lead arrives after its event.
- Re-ingested event files and verified that the event count remained unchanged at 8,481.
- Re-ingested both lead drops and verified that the lead count remained unchanged at 1,892.
- Generated all five reports in a separate output directory.
- Checked report totals and database counts.
- Reviewed the generated files and CLI help commands.