import argparse
import os
from pathlib import Path

from agents import Email, persist_audit, read_jsonl, run


def main():
    parser = argparse.ArgumentParser(description="Run Kaveri's replenishment planning pipeline.")
    parser.add_argument("--mode", choices=["offline", "groq"], default="offline")
    parser.add_argument("--emails", type=Path, help="Optional showcase JSONL file")
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args()
    key = os.getenv("GROQ_API_KEY")
    if args.mode == "groq" and not key:
        parser.error("Set GROQ_API_KEY before choosing Groq mode.")
    emails = [Email.model_validate(row) for row in read_jsonl(args.emails)] if args.emails else None
    result = run(emails, mode=args.mode, key=key)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "daily_report.md").write_text(result["report"], encoding="utf-8")
    persist_audit(result["audit"], args.out / "audit.jsonl")
    print(result["report"])


if __name__ == "__main__":
    main()
