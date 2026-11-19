import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import Field, model_validator

from agents import Email, Record, TODAY, persist_audit, run
from dataset import CsvText, DatasetError, SCHEMA, load_tables, parse_emails, sample_text

app = FastAPI(title="Kaveri | Supply Chain Agents", version="1.1.0")
PAGE = Path(__file__).with_name("dashboard.html")

CsvName = Literal["products.csv", "stock.csv", "sales_history.csv", "suppliers.csv", "open_orders.csv"]
EmailText = Annotated[str, Field(max_length=300_000)]


class RunRequest(Record):
    mode: Literal["offline", "groq"] = "offline"
    planning_date: Annotated[date, Field(ge=date(1900, 2, 1), le=date(2100, 12, 31))] = TODAY
    emails: Annotated[list[Email], Field(max_length=15)] | None = None
    email_file: EmailText | None = None
    files: dict[CsvName, CsvText] = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def check_email_input(self):
        if self.emails is not None and self.email_file is not None:
            raise ValueError("Use either an email file or pasted emails")
        return self


def prepare(request):
    try:
        emails = parse_emails(request.email_file) if request.email_file is not None else request.emails
        is_custom = bool(request.files) or request.planning_date != TODAY
        if emails is None and is_custom:
            emails = []
        if emails is not None:
            if len({email.email_id for email in emails}) != len(emails):
                raise DatasetError("Email IDs must be unique")
            if any(email.received_at.date() > request.planning_date for email in emails):
                raise DatasetError(f"Email timestamps must be on or before {request.planning_date}")
        tables, info = load_tables(request.files, request.planning_date)
        info["email_count"] = len(emails) if emails is not None else 15
        info["email_source"] = "upload" if request.email_file is not None else (
            "sample" if emails is None else "pasted" if emails else "none")
        return emails, tables, info, is_custom
    except DatasetError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return PAGE.read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok", "planning_date": str(TODAY),
            "server_groq_configured": bool(os.getenv("GROQ_API_KEY") and os.getenv("DEMO_ACCESS_TOKEN"))}


@app.get("/api/sample/{filename}", response_class=PlainTextResponse)
def sample_file(filename: str):
    if filename not in {*SCHEMA, "emails.jsonl"}:
        raise HTTPException(404, "Unknown sample file")
    kind = "text/csv" if filename.endswith(".csv") else "application/x-ndjson"
    return PlainTextResponse(sample_text(filename), media_type=kind,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/validate")
def validate_uploads(request: RunRequest):
    _, _, info, _ = prepare(request)
    return info


@app.post("/api/run")
def run_pipeline(
    request: RunRequest,
    x_groq_key: Annotated[str | None, Header(max_length=256)] = None,
    x_demo_token: Annotated[str | None, Header(max_length=256)] = None,
):
    emails, tables, info, is_custom = prepare(request)
    key = None
    if request.mode == "groq" and emails != []:
        token = os.getenv("DEMO_ACCESS_TOKEN")
        if x_groq_key:
            key = x_groq_key
        elif token and secrets.compare_digest(token.encode(), (x_demo_token or "").encode()):
            key = os.getenv("GROQ_API_KEY")
        if not key:
            raise HTTPException(403, "Provide your Groq key or a valid demo token with server-side Groq configured.")
    result = run(emails, as_of=request.planning_date, mode=request.mode, key=key,
                 tables=tables, dataset_summary=info if is_custom else None)
    run_id = uuid4().hex
    # Hosted /tmp is temporary. Download the audit to retain it.
    audit_dir = Path("/tmp/kaveri-audit" if os.getenv("VERCEL") else "output")
    audit_dir.mkdir(parents=True, exist_ok=True)
    result["audit"].insert(0, {"agent": "Dataset validation", "input": {"planning_date": str(request.planning_date)},
                               "output": info})
    persist_audit(result["audit"], audit_dir / f"{run_id}.jsonl")
    return {"run_id": run_id, **result, "dataset": info}
