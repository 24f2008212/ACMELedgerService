from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "orders.ndjson"
BUSINESS_TZ = ZoneInfo("Asia/Kolkata")
USD_PER_UNIT = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.04"),
    "INR": Decimal("0.01107"),
}
REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCTS = [
    "Air Fryer", "Blender", "Grinder", "Juicer",
    "Kettle", "Mixer", "Rice Cooker", "Toaster",
]
MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"],
        1,
    )
}
MONTHS.update({name[:3].lower(): number for name, number in MONTHS.items() if len(name) > 3})

app = FastAPI(title="Acme Appliances Ledger Q&A", version="1.0.0")


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BUSINESS_TZ)
    return dt.astimezone(BUSINESS_TZ)


def load_orders() -> list[dict]:
    if not DATA_FILE.exists():
        raise RuntimeError(f"Missing ledger data file: {DATA_FILE}")
    latest_by_id: dict[str, dict] = {}
    with DATA_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            row = dict(raw)
            row["_created"] = parse_dt(row["created_at"])
            row["_updated"] = parse_dt(row["updated_at"])
            row["_amount"] = Decimal(str(row["amount"]))
            row["_qty"] = int(row["qty"])
            previous = latest_by_id.get(row["id"])
            if previous is None or row["_updated"] >= previous["_updated"]:
                latest_by_id[row["id"]] = row
    return list(latest_by_id.values())


ORDERS = load_orders()


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def money_usd(row: dict) -> Decimal:
    return row["_amount"] * USD_PER_UNIT[row["currency"]]


def requested_currency(q: str) -> str:
    qn = norm(q)
    if re.search(r"\b(inr|rupees?|₹)\b", qn):
        return "INR"
    if re.search(r"\b(eur|euros?|€)\b", qn):
        return "EUR"
    return "USD"


def money_for(row: dict, currency: str) -> Decimal:
    usd = money_usd(row)
    return usd / USD_PER_UNIT[currency]


def round_money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def date_bounds(q: str) -> tuple[datetime | None, datetime | None]:
    qn = norm(q)
    year_match = re.search(r"\b(20\d{2})\b", qn)
    year = int(year_match.group(1)) if year_match else None

    # Quarter-specific questions, e.g. "Q1 2026".
    quarter = re.search(r"\bq([1-4])\b", qn)
    if quarter:
        qntr = int(quarter.group(1))
        y = year or datetime.now(BUSINESS_TZ).year
        month = (qntr - 1) * 3 + 1
        start = datetime(y, month, 1, tzinfo=BUSINESS_TZ)
        end_month = month + 3
        end = datetime(y + (end_month - 1) // 12, ((end_month - 1) % 12) + 1, 1, tzinfo=BUSINESS_TZ)
        return start, end

    # Natural month names; if a year is absent, use the current year.
    for month_name, month_num in sorted(MONTHS.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(month_name)}\b", qn):
            y = year or datetime.now(BUSINESS_TZ).year
            start = datetime(y, month_num, 1, tzinfo=BUSINESS_TZ)
            if month_num == 12:
                end = datetime(y + 1, 1, 1, tzinfo=BUSINESS_TZ)
            else:
                end = datetime(y, month_num + 1, 1, tzinfo=BUSINESS_TZ)
            return start, end

    # Explicit ISO dates. A two-date range is inclusive of the second date.
    iso_dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", qn)
    if iso_dates:
        first = date.fromisoformat(iso_dates[0])
        start = datetime(first.year, first.month, first.day, tzinfo=BUSINESS_TZ)
        if len(iso_dates) > 1:
            last = date.fromisoformat(iso_dates[1])
            end_date = last + timedelta(days=1)
            end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=BUSINESS_TZ)
        else:
            end_date = first + timedelta(days=1)
            end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=BUSINESS_TZ)
        return start, end

    # Relative periods are interpreted in the business timezone.
    today = datetime.now(BUSINESS_TZ).date()
    if "last month" in qn or "previous month" in qn:
        first_this = date(today.year, today.month, 1)
        last_month_end = first_this
        last_month_start = date(
            today.year - (1 if today.month == 1 else 0),
            12 if today.month == 1 else today.month - 1,
            1,
        )
        return (
            datetime.combine(last_month_start, datetime.min.time(), BUSINESS_TZ),
            datetime.combine(last_month_end, datetime.min.time(), BUSINESS_TZ),
        )
    if "this month" in qn or "current month" in qn:
        start = datetime(today.year, today.month, 1, tzinfo=BUSINESS_TZ)
        if today.month == 12:
            end = datetime(today.year + 1, 1, 1, tzinfo=BUSINESS_TZ)
        else:
            end = datetime(today.year, today.month + 1, 1, tzinfo=BUSINESS_TZ)
        return start, end

    if year:
        return (
            datetime(year, 1, 1, tzinfo=BUSINESS_TZ),
            datetime(year + 1, 1, 1, tzinfo=BUSINESS_TZ),
        )
    return None, None


def filter_orders(question: str) -> list[dict]:
    qn = norm(question)
    start, end = date_bounds(question)
    selected = ORDERS

    if start:
        selected = [r for r in selected if r["_created"] >= start]
    if end:
        selected = [r for r in selected if r["_created"] < end]

    for region in REGIONS:
        if re.search(rf"\b{re.escape(region.casefold())}\b", qn):
            selected = [r for r in selected if r["region"].casefold() == region.casefold()]
            break

    customer_match = re.search(r"\bC\d{4}\b", question, flags=re.IGNORECASE)
    if customer_match:
        customer_id = customer_match.group(0).upper()
        selected = [r for r in selected if r["customer"].upper() == customer_id]

    order_match = re.search(r"\bORD-\d+\b", question, flags=re.IGNORECASE)
    if order_match:
        order_id = order_match.group(0).upper()
        selected = [r for r in selected if r["id"].upper() == order_id]

    for product in sorted(PRODUCTS, key=len, reverse=True):
        if product.casefold() in qn:
            selected = [r for r in selected if r["product"].casefold() == product.casefold()]
            break

    if re.search(r"\b(refund|refunded|refunds|returned)\b", qn):
        selected = [r for r in selected if r["status"] == "refunded"]
    elif re.search(r"\b(void|voided|cancelled|canceled)\b", qn):
        selected = [r for r in selected if r["status"] == "void"]
    elif re.search(r"\b(paid|revenue|sales|sold|spent|spend|turnover|income|earn|earned)\b", qn):
        # Explicit "paid orders only" must filter by status even when the
        # question asks for a count of customers rather than revenue.
        selected = [r for r in selected if r["status"] == "paid"]

    return selected


def is_count_question(qn: str) -> bool:
    return bool(re.search(
        r"\b(how many|count|number of|count of|quantity of orders)\b", qn
    )) or bool(re.search(r"\b(number|count)\b.*\borders?\b", qn))


def aggregate_metric(rows: list[dict], q: str) -> Decimal | int:
    qn = norm(q)
    currency = requested_currency(q)

    if re.search(r"\b(average|avg|mean)\b", qn) and re.search(
        r"\b(order value|order amount|order worth|worth|revenue|sales|spend|spent)\b", qn
    ):
        paid = [r for r in rows if r["status"] == "paid"]
        if not paid:
            return Decimal("0")
        return sum((money_for(r, currency) for r in paid), Decimal("0")) / Decimal(len(paid))

    if re.search(r"\b(quantity|units|items)\b", qn) and not re.search(r"\b(order|orders)\b", qn):
        return sum(r["_qty"] for r in rows)

    if is_count_question(qn):
        if re.search(r"\b(customers|clients)\b", qn):
            return len({r["customer"] for r in rows})
        return len(rows)

    if re.search(r"\b(customer|customers|client|clients)\b", qn) and re.search(
        r"\b(how many|count|number of|unique|distinct)\b", qn
    ):
        return len({r["customer"] for r in rows})

    # Revenue is paid orders only; refund questions are filtered to refunded orders.
    if any(word in qn for word in ["revenue", "sales", "turnover", "income", "earn", "earned", "spent", "spend", "amount", "value", "refund"]):
        return sum((money_for(r, currency) for r in rows), Decimal("0"))

    if re.search(r"\b(units|items|quantity)\b", qn):
        return sum(r["_qty"] for r in rows)

    return len(rows)


def group_key(qn: str) -> str | None:
    if re.search(r"\b(by|per)\s+product\b|\bproducts?\b.*\b(breakdown|each|compare)\b", qn):
        return "product"
    if re.search(r"\b(by|per)\s+region\b|\bregions?\b.*\b(breakdown|each|compare)\b", qn):
        return "region"
    if re.search(r"\b(by|per)\s+customer\b|\bcustomers?\b.*\b(breakdown|each|compare)\b", qn):
        return "customer"
    return None


def answer_question(question: str):
    qn = norm(question)
    rows = filter_orders(question)
    currency = requested_currency(question)

    # Questions asking for the top/bottom item should return its API spelling/ID.
    wants_top = bool(re.search(r"\b(top|most|highest|largest|best|greatest|maximum)\b", qn))
    wants_bottom = bool(re.search(r"\b(least|lowest|smallest|fewest|minimum)\b", qn)) and not bool(re.search(r"\bat least\b", qn))
    dimension = None
    if re.search(r"\b(product|products|item|items)\b", qn) and re.search(r"\b(which|what|top|most|highest|best|least|lowest|popular)\b", qn):
        dimension = "product"
    elif re.search(r"\b(customer|customers|client|clients)\b", qn) and re.search(r"\b(which|who|top|most|highest|best|least|lowest|spent|spending)\b", qn):
        dimension = "customer"
    elif re.search(r"\b(region|regions)\b", qn) and re.search(r"\b(which|what|top|most|highest|best|least|lowest)\b", qn):
        dimension = "region"

    if dimension and (wants_top or wants_bottom):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r[dimension]].append(r)
        if not groups:
            return "No matching records"
        if re.search(r"\b(revenue|sales|refund|spent|spend|amount|value)\b", qn):
            score = lambda rs: sum((money_for(r, currency) for r in rs), Decimal("0"))
        elif re.search(r"\b(units|quantity|items sold|most popular|popular)\b", qn):
            score = lambda rs: sum(r["_qty"] for r in rs)
        else:
            score = lambda rs: len(rs)
        ranked = sorted(groups.items(), key=lambda item: (score(item[1]), item[0]), reverse=wants_top)
        return ranked[0][0]

    # Explicit breakdowns, e.g. "revenue by region".
    grouping = group_key(qn)
    if grouping:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r[grouping]].append(r)
        if re.search(r"\b(refund|refunded|refunds)\b", qn):
            metric = lambda rs: round_money(sum((money_for(r, currency) for r in rs), Decimal("0")))
        elif re.search(r"\b(revenue|sales|spent|spend|amount|value|turnover|income|earn|earned)\b", qn):
            metric = lambda rs: round_money(sum((money_for(r, currency) for r in rs), Decimal("0")))
        elif re.search(r"\b(quantity|units|items)\b", qn):
            metric = lambda rs: sum(r["_qty"] for r in rs)
        else:
            metric = lambda rs: len(rs)
        return {key: metric(value) for key, value in sorted(groups.items())}

    # "How many customers" should count distinct IDs, not order rows.
    if re.search(r"\b(how many|number of|count|unique|distinct)\b.*\b(customers|clients)\b", qn):
        return len({r["customer"] for r in rows})

    metric = aggregate_metric(rows, question)
    if isinstance(metric, Decimal):
        return round_money(metric)
    return metric


@app.get("/")
def health():
    return {"service": "Acme Appliances ledger Q&A", "status": "ok", "current_orders": len(ORDERS)}


@app.post("/")
def ask(request: QuestionRequest):
    try:
        answer = answer_question(request.question)
        return {"answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not answer question: {exc}") from exc
