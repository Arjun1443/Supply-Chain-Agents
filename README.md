# Supply Chain Agents

FastAPI application for Kaveri Distributors. The demo plans against the supplied 1 December 2026 stock snapshot. It recommends actions for the operations manager. The application has no purchasing, transfer, or supplier-account write tools.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m uvicorn app:app
```

Open http://127.0.0.1:8000 for the dashboard or http://127.0.0.1:8000/docs for the API. Choose **Groq live** and enter your key for that request, or run the CLI with a securely configured `GROQ_API_KEY`.

```bash
python cli.py
python cli.py --mode groq
python cli.py --mode groq --emails showcase.jsonl
```

On PowerShell set an environment variable using `$env:GROQ_API_KEY = "your-key"` before running the CLI. Do not commit credentials.

## How it works

1. **Email Reader** passes untrusted email text to Groq as data and validates the JSON against a Pydantic schema and source evidence. A failed extraction retries once. Ambiguous, suspicious, and failed emails go to review. The offline reader uses local extraction rules for a repeatable demo.
2. **Stock Analyst** calculates 28-day average demand and stock position, applies the latest accepted PO email and projects demand across receipt dates. It checks for shortages before revised deliveries, adjusts cancellations and splits verified partial shipments.
3. **Planner** reserves surplus above the source warehouse's target, otherwise recommends a case-rounded alternate purchase with human review. Stock at or below the reorder point triggers standard replenishment. Orders strictly above ₹50,000 need approval.

The fixed sequence lets Groq interpret language while Python owns arithmetic, sourcing, routing and report writing. The CLI and dashboard produce Markdown and a structured JSONL audit containing agent inputs and outputs. Hosted audit storage is temporary. Download the audit to retain it.

## Interpretation

- Average daily sales divide the total in the 28 days before the snapshot by 28. The 3-day safety and 14-day replenishment buffers are fixed. Case packs round up after applying the MOQ.
- Transfers are projected with no transit delay because the dataset has no transit-time input. Alternate orders require human review of the delivery date. A valid accepted email updates only the planning projection.
- A notice without a firm second ETA uses the stated revised delivery date as the first planning checkpoint. Supplier price changes appear as notes pending verification.
- Scoring compares expected event count and labeled fields in order, plus the extraction-stage human-review flag. The label file is used only after extraction. Planning recommendations and message supersession are checked separately.
- A fresh JSONL input replaces the sample email batch and uses the same stock and PO snapshot. For the showcase, submit messages dated through 1 December 2026.
- The offline reader was evaluated on the supplied dataset. Run Groq mode to obtain its independent score.

## Deploy on Vercel

Import the GitHub repository as FastAPI. The entry point is `app.py`. The dashboard accepts a user-supplied Groq key over HTTPS. For API access with a centrally managed key, configure `GROQ_API_KEY` and `DEMO_ACCESS_TOKEN` as sensitive project variables and pass the token as `X-Demo-Token`. Optional `GROQ_MODEL` defaults to `llama-3.3-70b-versatile`.

## Manager uploads

- Set the planning date, then select any of the five supported CSV files in the dashboard. Download the sample beside each slot to see its columns. Empty slots use the bundled sample data. Use a consistent stock snapshot and ensure `stock.csv` on-order quantities reconcile with `open_orders.csv`.
- Upload up to 15 emails as a JSON array or JSONL file, paste them, or choose stock-only mode. The original sample run still uses the 15 supplied emails and displays its evaluation score.
- Select **Validate files** to check schemas, duplicate keys, reference integrity and PO values before the run. Sales coverage and overdue POs are flagged as warnings. Incomplete sales days count as zero.
- Review and filter proposed actions, view the underlying inventory risks and inspect the email review queue. Export Markdown, an actions CSV or the JSONL audit.
- Uploads apply only to the current run. Inventory CSVs stay within the planning service. Live Groq mode sends the submitted email and product lookup to Groq for extraction.
- The API accepts CSV file contents under `files` keyed by filename and optional email contents under `email_file`. Use `POST /api/validate` before `POST /api/run`. The maximum is 850 KB per CSV and 2.5 MB across the CSVs together.
