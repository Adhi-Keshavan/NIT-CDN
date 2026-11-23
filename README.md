# NIT-CDN

Day 1 Django foundation for customers, products, materials, bills of materials, purchasing, production, sales orders, invoicing, payments, and a signed stock ledger.

Django keeps the business rules and role checks in one backend, while PostgreSQL gives the ledger transactional writes and exact decimal arithmetic. Server-rendered pages are a small starting point; the order screen can be made mobile-friendly without changing the data model.

## Local setup

Use Python 3.10–3.14. On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set a unique `SECRET_KEY`, and set `DATABASE_URL=sqlite:///db.sqlite3` for a local start. Then run:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Open <http://127.0.0.1:8000/login/>. A `createsuperuser` account receives the `admin` business role and can use `/admin/` and `/management/`. The role routes are `/sales/`, `/production/`, and `/management/`.

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, `cp .env.example .env`, and `.venv/bin/python manage.py ...`.

## Supabase PostgreSQL

In the Supabase dashboard, open **Connect** and copy the PostgreSQL connection string. Put it in `.env` as `DATABASE_URL` and append `?sslmode=require` if the URL has no query string (or `&sslmode=require` if it does). URL-encode reserved characters in the password. For an IPv4-only network, use the **Session pooler** connection string on port 5432. See [Supabase's connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres).

Run `manage.py migrate` and `manage.py runserver` as above. The project uses Django's PostgreSQL backend with psycopg. No Supabase API key is needed for this database connection.

## Benchmark data

The supplied [Section 5 fixture](data/benchmark.json) includes Murukku 200 g (200 packets expected per batch), its five BOM ingredients and packaging lines, the stated rates and opening balances, Retailer A, Distributor B, and demo users for Salesman A/B, admin, and production. The problem statement does not give SKUs, account credentials, HSN code, or a date, so the fixture assigns stable SKUs and uses **2026-11-01** for opening balances. The demo usernames are `salesman_a`, `salesman_b`, `factory_admin`, and `production_supervisor`; their shared dummy password is `BenchmarkDemo2026!`. These accounts are not Django superusers. Replace demo credentials before publishing an accessible deployment.

Materials are classified as raw or packaging. BOM quantities are **per batch**, with an expected yield of 200 packets. Product stock is measured in packets; pouch and carton stock in pieces. The product records 20 packets per carton for unit conversion. Money and quantities use decimal fields, and the 5% GST rate is stored on the product. The HSN code remains blank because the statement does not specify one.

```powershell
.\.venv\Scripts\python.exe manage.py seed_benchmark
.\.venv\Scripts\python.exe manage.py seed_benchmark  # safe to run again
```

Records are upserted by username, customer code, SKU, BOM product/material pair, and opening balance SKU. Existing records with those keys are updated; running twice creates no duplicates. Opening movements use source references like `benchmark:SKU`.

## Stock ledger

Each stock movement records an item, signed quantity, unit, date, source type, source document reference, and quantity converted to the item's base unit. A positive quantity adds stock; a negative quantity removes it. Product and material balances are the sum of `base_quantity`, optionally through a date:

```python
from erp.models import Item, StockMovement
item = Item.objects.get(sku="YOUR-SKU")
balance = StockMovement.objects.balance_for(item)
```

The ledger does not infer movements merely from draft purchases, runs, or orders. Record a movement when its source document is actually posted. Opening stock is created by `seed_benchmark`.

## Current scope and next build step

The Day 1 foundation covers the schema, Supabase connection, roles, exact benchmark master data, unit conversions, opening stock, and ledger queries. The Section 5 **transaction scenario is not yet implemented**: purchases, production runs, approvals, invoices, dispatch, payments, costing, exports, and audit logging still need posting workflows. Build production posting first, so one run automatically consumes actual materials and packaging, adds good packets, and reports yield and consumption variances. Then build stock-checked order approval, GST invoices, dispatch, payment allocation, and reports. The expected checks after those workflows are flour **120 kg**, oil **19.5 L**, pouches **306**, finished goods **238** after production and **88** after Retailer A dispatch, with a **32 packet** shortage for Distributor B.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

The tests use the configured database. For isolated local tests, set `DATABASE_URL=sqlite:///db.sqlite3` in the environment before running them. The test suite checks both an illustrative fixture and the exact Section 5 master data, plus role access, conversions, and balances.
