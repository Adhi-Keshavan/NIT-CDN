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

Records are keyed by username, customer code, SKU, BOM product/material pair, and opening balance SKU. Running twice creates no duplicates. Posted opening balances are immutable; if an opening quantity must be corrected, post a dated stock adjustment rather than editing history. Opening movements use source references like `benchmark:SKU`.

## Stock ledger

Each stock movement records an item, signed quantity, unit, date, source type and reference, an enforced foreign key to exactly one purchase, production run, shipped order, invoice, or stock adjustment, and quantity converted to the item's base unit. A positive quantity adds stock; a negative quantity removes it. Product and material balances are the sum of `base_quantity`, optionally through a date:

```python
from erp.models import Item, StockMovement
item = Item.objects.get(sku="YOUR-SKU")
balance = StockMovement.objects.balance_for(item)
```

Posting is explicit and transactional. In Django admin, save a purchase and select **Receive selected purchases and post stock** to add its material; save a production run with one actual consumption line for each BOM material and select **Complete selected runs and post stock** to subtract materials and add good packets; approve an order after the stock check, then select **Dispatch selected orders and post stock** to subtract finished goods. Stock adjustments have their own document and lines, posted with **Post selected stock adjustments**. Each action is safe to repeat, checks stock before an issue, and prevents editing posted quantities. Draft documents do not change stock.

## Current scope and next build step

The ledger posting test now reproduces the Section 5 stock checks: flour **120 kg**, oil **19.5 L**, pouches **306**, finished goods **238** after production and **88** after Retailer A dispatch, with a **32 packet** shortage for Distributor B. It also checks the **−6%** yield and **+5%** oil variances. These transactions run in an isolated test database; `seed_benchmark` loads only master data and opening stock. The remaining application work is mobile order entry, GST invoice creation and PDF, payments, costing, exports, and audit logging. The admin actions are a working posting interface until dedicated role screens are built.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
$env:DATABASE_URL='sqlite:///db.sqlite3'
.\.venv\Scripts\python.exe manage.py test
Remove-Item Env:DATABASE_URL
```

The test command above temporarily selects local SQLite so Django does not create a test database in Supabase. The test suite checks seed idempotency, role access, unit conversion, ledger source links, idempotent posting, balance calculations, stock shortages, and posted-document immutability.
