import os
import secrets
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import Field, model_validator

from agents import Email, Record, TODAY, persist_audit, run

app = FastAPI(title="Kaveri | Supply Chain Agents", version="1.0.0")
PAGE = Path(__file__).with_name("dashboard.html")


class RunRequest(Record):
    mode: Literal["offline", "groq"] = "offline"
    emails: Annotated[list[Email], Field(min_length=1, max_length=15)] | None = None

    @model_validator(mode="after")
    def check_emails(self):
        if self.emails is not None:
            if len({email.email_id for email in self.emails}) != len(self.emails):
                raise ValueError("Email IDs must be unique")
            if any(email.received_at.date() > TODAY for email in self.emails):
                raise ValueError(f"Emails must be received by the planning date {TODAY}")
        return self


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return PAGE.read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok", "planning_date": str(TODAY),
            "server_groq_configured": bool(os.getenv("GROQ_API_KEY") and os.getenv("DEMO_ACCESS_TOKEN"))}


@app.post("/api/run")
def run_pipeline(
    request: RunRequest,
    x_groq_key: Annotated[str | None, Header(max_length=256)] = None,
    x_demo_token: Annotated[str | None, Header(max_length=256)] = None,
):
    key = None
    if request.mode == "groq":
        token = os.getenv("DEMO_ACCESS_TOKEN")
        if x_groq_key:
            key = x_groq_key
        elif token and secrets.compare_digest(token.encode(), (x_demo_token or "").encode()):
            key = os.getenv("GROQ_API_KEY")
        if not key:
            raise HTTPException(403, "Provide your Groq key or a valid demo token with server-side Groq configured.")
    result = run(request.emails, mode=request.mode, key=key)
    run_id = uuid4().hex
    # Vercel's /tmp is invocation-local storage. The response includes the downloadable audit.
    audit_dir = Path("/tmp/kaveri-audit" if os.getenv("VERCEL") else "output")
    audit_dir.mkdir(parents=True, exist_ok=True)
    persist_audit(result["audit"], audit_dir / f"{run_id}.jsonl")
    return {"run_id": run_id, **result}
