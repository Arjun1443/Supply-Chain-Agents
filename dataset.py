import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from pydantic import Field, ValidationError

from agents import DATA, Email, rows


FILE_LIMIT = 850_000
TOTAL_LIMIT = 2_500_000
ROW_LIMIT = 30_000
CODE = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,39}\Z")
MONEY = re.compile(r"\d{1,9}(?:\.\d{1,2})?\Z")
CsvText = Annotated[str, Field(max_length=FILE_LIMIT)]
SCHEMA = {
    "products.csv": ("sku", "product_name", "category"),
    "stock.csv": ("warehouse", "sku", "on_hand", "on_order"),
    "sales_history.csv": ("date", "warehouse", "sku", "units_sold"),
    "suppliers.csv": ("supplier_id", "supplier_name", "sku", "lead_time_days", "moq_units",
                      "case_pack", "unit_price_inr", "approved", "alternate_supplier_id"),
    "open_orders.csv": ("po_id", "supplier_id", "sku", "warehouse", "qty_units",
                        "unit_price_inr", "order_value_inr", "eta"),
}


class DatasetError(ValueError):
    pass


def read_table(name, raw):
    reader = csv.DictReader(io.StringIO(raw.removeprefix("\ufeff"), newline=""), strict=True)
    try:
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise DatasetError(f"{name}: missing or duplicate header columns")
        missing = set(SCHEMA[name]) - set(reader.fieldnames)
        if missing:
            raise DatasetError(f"{name}: missing columns {', '.join(sorted(missing))}")
        result = []
        for row in reader:
            if len(result) >= ROW_LIMIT:
                raise DatasetError(f"{name}: exceeds {ROW_LIMIT:,} rows")
            if None in row or None in row.values():
                raise DatasetError(f"{name}, line {reader.line_num}: unexpected number of columns")
            result.append({key: row[key].strip() for key in SCHEMA[name]})
    except csv.Error as exc:
        raise DatasetError(f"{name}, line {reader.line_num}: invalid CSV ({exc})") from exc
    if not result and name not in ("open_orders.csv", "sales_history.csv"):
        raise DatasetError(f"{name}: add at least one data row")
    return result


def required(row, columns, pattern):
    for field in columns:
        if not pattern.fullmatch(row[field]):
            raise DatasetError(f"invalid {field}: {row[field][:50]!r}")


def integer(row, columns, minimum=0):
    for field in columns:
        value = row[field]
        if not re.fullmatch(r"\d{1,9}", value) or int(value) < minimum:
            raise DatasetError(f"{field} must be an integer of at least {minimum}")


def unique(seen, key):
    if key in seen:
        raise DatasetError(f"duplicate key {key}")
    seen.add(key)


def iso_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DatasetError(f"invalid date {value[:50]!r}, expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetError(f"invalid calendar date {value!r}") from exc


def validate_tables(tables, as_of):
    products, positions, suppliers, po_ids, sales_records = [set() for _ in range(5)]
    primary = defaultdict(list)
    incoming, sales_coverage = Counter(), Counter()
    overdue = 0
    for filename in SCHEMA:
        for index, row in enumerate(tables[filename], start=2):
            try:
                required(row, ["sku"], CODE)
                sku = row["sku"]
                if filename == "products.csv":
                    unique(products, sku)
                    if not row["product_name"] or len(row["product_name"]) > 160 or not row["category"]:
                        raise DatasetError("a product name (up to 160 characters) and category are required")
                    continue
                if sku not in products:
                    raise DatasetError(f"{sku} is missing from products.csv")
                if filename in ("stock.csv", "sales_history.csv", "open_orders.csv"):
                    required(row, ["warehouse"], CODE)
                    position = row["warehouse"], sku
                if filename == "stock.csv":
                    integer(row, ("on_hand", "on_order"))
                    unique(positions, position)
                elif filename == "sales_history.csv":
                    day = iso_date(row["date"])
                    integer(row, ("units_sold",))
                    unique(sales_records, (day, *position))
                    if position in positions and as_of - timedelta(days=28) <= day < as_of:
                        sales_coverage[position] += 1
                elif filename == "suppliers.csv":
                    required(row, ["supplier_id"], CODE)
                    required(row, ["unit_price_inr"], MONEY)
                    integer(row, ("lead_time_days", "moq_units"))
                    integer(row, ("case_pack",), minimum=1)
                    if int(row["lead_time_days"]) > 3650 or Decimal(row["unit_price_inr"]) <= 0:
                        raise DatasetError("lead time must be at most 3650 days and price must be positive")
                    if row["approved"].lower() not in ("yes", "no") or not row["supplier_name"]:
                        raise DatasetError("supplier_name is required and approved must be yes or no")
                    unique(suppliers, (sku, row["supplier_id"]))
                    if row["alternate_supplier_id"]:
                        required(row, ["alternate_supplier_id"], CODE)
                        primary[sku].append(row)
                else:
                    required(row, ["po_id"], re.compile(r"PO-\d{1,18}\Z"))
                    unique(po_ids, row["po_id"])
                    integer(row, ("qty_units",), minimum=1)
                    required(row, ["unit_price_inr"], MONEY)
                    if not re.fullmatch(r"\d{1,18}(?:\.\d{1,2})?", row["order_value_inr"]):
                        raise DatasetError("order_value_inr must be a plain decimal amount")
                    quantity = int(row["qty_units"])
                    price = Decimal(row["unit_price_inr"])
                    if price <= 0 or Decimal(row["order_value_inr"]) != quantity * price:
                        raise DatasetError("order_value_inr must equal quantity × positive unit price")
                    if position not in positions or (sku, row["supplier_id"]) not in suppliers:
                        raise DatasetError("PO must reference a stocked warehouse/SKU and a known supplier")
                    overdue += iso_date(row["eta"]) < as_of
                    incoming[position] += quantity
            except DatasetError as exc:
                raise DatasetError(f"{filename}, row {index}: {exc}") from exc

    for sku in sorted(products):
        main = primary[sku]
        if len(main) != 1:
            raise DatasetError(f"suppliers.csv: {sku} must have exactly one row with an alternate_supplier_id")
        supplier = main[0]
        alternate = supplier["alternate_supplier_id"]
        if alternate == supplier["supplier_id"] or (sku, alternate) not in suppliers:
            raise DatasetError(f"suppliers.csv: {sku} needs a distinct, existing alternate supplier")
    for row in tables["stock.csv"]:
        position = row["warehouse"], row["sku"]
        if int(row["on_order"]) != incoming[position]:
            raise DatasetError(f"stock.csv: {position[0]}/{position[1]} on_order must match open_orders.csv")

    warnings = []
    incomplete = sum(sales_coverage[position] < 28 for position in positions)
    if incomplete:
        warnings.append(f"{incomplete} warehouse/SKU positions have fewer than 28 sales days. Missing days count as zero.")
    if overdue:
        warnings.append(f"{overdue} open POs have an ETA before the planning date. Verify whether they are still incoming.")
    return warnings


def load_tables(raw_files, as_of):
    if as_of < date(1900, 2, 1):
        raise DatasetError("Choose a planning date on or after 1 February 1900.")
    sizes = {name: len(content.encode("utf-8", errors="replace")) for name, content in raw_files.items()}
    for name, size in sizes.items():
        if size > FILE_LIMIT:
            raise DatasetError(f"{name}: exceeds the {FILE_LIMIT:,} byte limit")
    if sum(sizes.values()) > TOTAL_LIMIT:
        raise DatasetError("Combined CSV uploads must be 2.5 MB or smaller")
    tables = {name: read_table(name, raw_files[name]) if name in raw_files else rows(name) for name in SCHEMA}
    warnings = validate_tables(tables, as_of)
    return tables, {
        "files": {name: {"source": "upload" if name in raw_files else "sample", "rows": len(table)}
                  for name, table in tables.items()},
        "products": len(tables["products.csv"]),
        "positions": len(tables["stock.csv"]),
        "warehouses": len({row["warehouse"] for row in tables["stock.csv"]}),
        "warnings": warnings,
    }


def parse_emails(content):
    raw = content.removeprefix("\ufeff").strip()
    if not raw:
        raise DatasetError("Email file is empty. Use [] for a stock-only run.")
    try:
        values = json.loads(raw) if raw.startswith("[") else [
            json.loads(line) for line in raw.splitlines() if line.strip()
        ]
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Email file has invalid JSON: {exc.msg}") from exc
    if len(values) > 15:
        raise DatasetError("Upload up to 15 emails per run")
    emails = []
    for number, value in enumerate(values, start=1):
        try:
            emails.append(Email.model_validate(value))
        except ValidationError as exc:
            error = exc.errors(include_input=False, include_url=False)[0]
            raise DatasetError(f"Email {number}, {'.'.join(map(str, error['loc']))}: {error['msg']}") from exc
    if len({email.email_id for email in emails}) != len(emails):
        raise DatasetError("Email IDs must be unique")
    return emails


def sample_text(name):
    if name == "emails.jsonl":
        return (DATA / name).read_text(encoding="utf-8")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SCHEMA[name], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows(name))
    return output.getvalue()
