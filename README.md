# Acme Appliances Ledger Q&A

FastAPI service answering natural-language questions about the Acme Appliances order ledger.

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

POST to `/`:
```bash
curl -X POST http://localhost:8000/ \
  -H 'Content-Type: application/json' \
  -d '{"question":"What was the total revenue in USD from the North region in March 2026?"}'
```

## Render
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- The `orders.ndjson` file must be committed alongside `main.py`.

Business rules:
- Business dates use Asia/Kolkata.
- For duplicate order IDs, only the record with the latest `updated_at` is current.
- Only current orders with status `paid` count as revenue.
- Refund amounts are based on current orders with status `refunded`.
- Money is converted using the API-provided rates: USD 1, EUR 1.04, INR 0.01107 USD per currency unit.
