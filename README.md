# NIT-CDN

**An order-to-audit ERP for small food manufacturers.**

NIT-CDN is a Django application that connects customer orders, purchasing, production, inventory, GST invoicing, payments, and audit-ready reporting in a single workflow. Every stock movement, cost, and invoice is traceable to a source document and a user.

> **Status:** Working benchmark prototype. Configure and verify real GSTIN, HSN, company, and customer data before issuing business invoices. See [Current Limitations](#current-limitations).

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Database Options](#database-options)
- [Demo Accounts and Access Control](#demo-accounts-and-access-control)
- [Benchmark Walkthrough](#benchmark-walkthrough)
- [Ledger and Costing Rules](#ledger-and-costing-rules)
- [Reports and Audit](#reports-and-audit)
- [Testing and Verification](#testing-and-verification)
- [Documentation](#documentation)
- [Current Limitations](#current-limitations)

---

## Features

| Area | Capabilities |
| --- | --- |
| **Access control** | Role-based workspaces for administrators, salespeople, and production staff. New signups stay inactive until an administrator approves them. |
| **Inventory** | Stock ledger of signed receipts and issues; balances are derived from posted movements, never stored as editable totals. |
| **Production** | Batch requirements from a bill of materials (BOM), common unit conversions, and recorded yield and material variances with alert thresholds. |
| **Costing** | Moving weighted-average material cost; production costs are snapshotted when a run is posted. |
| **Sales** | Order review and approval with stock checks, GST invoice PDFs, dispatch, payment collection, and full credit notes for unpaid invoices. |
| **Reporting** | Date-filtered sales, GST, purchase, stock, production, and collections reports with CSV and PDF export, plus a dedicated visualizations page. |
| **Audit** | User- and timestamp-stamped audit events for key posting and invoice actions; issued invoices are immutable. |

## Quick Start

### Requirements

- Python 3.10 or newer
- Windows PowerShell (commands below). macOS and Linux users can run the equivalent Python commands.

### 1. Install

From the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### 2. Configure

Edit `.env` (see [Configuration](#configuration)), then generate a private secret key:

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 3. Run the benchmark demo

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
```

This single command:

1. Uses an isolated local SQLite database (`db.local.sqlite3`)
2. Applies migrations
3. Loads the benchmark data
4. Runs Django's system check
5. Starts the development server

It overrides `DATABASE_URL` for its own process only, so it never connects to Supabase.

Open <http://127.0.0.1:8000/login/> and sign in with a [demo account](#demo-accounts-and-access-control).

To prepare the database without starting the server:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -Database db.local.sqlite3 -NoServer
```

### Running Django commands manually

Use this path when you intentionally want Django to use the `DATABASE_URL` from `.env`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_benchmark
.\.venv\Scripts\python.exe manage.py runserver
```

`seed_benchmark` is idempotent: re-running it updates benchmark records without creating duplicates.

## Configuration

Settings are read from `.env`. This file contains environment-specific secrets and **must not be committed**.

| Variable | Local development value | Notes |
| --- | --- | --- |
| `SECRET_KEY` | A private random value | Generate with the command above. |
| `DEBUG` | `True` | Set to `False` in any shared or production deployment. |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | Use a PostgreSQL URI for Supabase. |

## Database Options

| Option | When to use | Command / setting |
| --- | --- | --- |
| **Local SQLite runner** | Demos and evaluation | `scripts/run_local.ps1` (always SQLite) |
| **SQLite via `.env`** | Manual local development | `DATABASE_URL=sqlite:///db.sqlite3` |
| **Supabase PostgreSQL** | Hosted database | See below |

### Using Supabase PostgreSQL

1. In Supabase, open **Connect** and copy the PostgreSQL connection URI. Use the **Session pooler** URI if your network requires a pooled connection.
2. Set it in `.env`, including the password and SSL mode:

   ```text
   DATABASE_URL=postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
   ```

3. Replace the placeholders with your project values and **URL-encode reserved characters** in the password.
4. Apply the schema and load the benchmark:

   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py seed_benchmark
   ```

Django migrations create all required tables; do not create them manually in the Supabase Table Editor.

## Demo Accounts and Access Control

The benchmark creates the accounts below. The password for each is `BenchmarkDemo2026!`.

| Role | Username | Main workspace |
| --- | --- | --- |
| Admin | `factory_admin` | Overview, order review, access approvals, reports, ERP administration |
| Production | `production_supervisor` | Purchases, production runs, stock |
| Salesperson | `salesman_a` | Create orders, view own orders |
| Salesperson | `salesman_b` | Create orders, view own orders |

> **Security:** These are demo credentials. Replace them before exposing any deployment.

- The benchmark admin is a **business administrator**, not a Django superuser. Create a full Django administrator with:

  ```powershell
  .\.venv\Scripts\python.exe manage.py createsuperuser
  ```

- New signups request a role and remain **inactive** until an administrator approves them on the People/access page. A signup cannot grant itself administrator access.

## Benchmark Walkthrough

The fixture in [`data/benchmark.json`](data/benchmark.json) creates:

- **Murukku 200 g** and its five BOM components
- Two customers and four demo users
- Opening stock dated 2026-11-01
- An expected yield of 200 packets per batch

The fixture does **not** include real company or customer GSTINs, or a product HSN code.

### Part 1: Purchase and production

1. Sign in as `production_supervisor` and receive **60 kg flour at ₹45/kg**. Flour stock rises from 100 kg to 160 kg.
2. Start one Murukku 200 g batch. The BOM requires 40 kg flour, 10 L oil, 1.2 kg spice, 200 pouches, and 10 cartons, for 200 expected packets.
3. Record actual issues: 40 kg flour, 10.5 L oil, 1.2 kg spice, 194 pouches, 9 cartons. Record 6 damaged pouches and **188 good packets**, then post the run.
4. The run flags **+5% oil use** and **−6% yield** against the 3% alert threshold. Add ₹1,200 labour and ₹600 gas/power. The benchmark cost is **₹31.14 per good packet**.

### Part 2: Order, invoice, dispatch, and payment

1. Complete Part 1 first; finished stock should be **238 packets**.
2. Sign in as `salesman_a` and submit Retailer A's order for **150 packets at ₹50 each**.
3. Sign in as `factory_admin`, review the order, and approve it once the stock check shows 238 available.
4. Before invoicing, enter the company's actual name, address, state code, and GSTIN under **Company settings**. Add the product's actual HSN and customer details in ERP administration.
5. Issue the invoice. Expected benchmark values:

   | Component | Amount |
   | --- | --- |
   | Taxable value | ₹7,500.00 |
   | CGST | ₹187.50 |
   | SGST | ₹187.50 |
   | **Total** | **₹7,875.00** |

6. Dispatch the order. Finished stock falls from 238 to **88 packets**. Record a ₹4,000 payment; Retailer A then owes **₹3,875**.
7. As `salesman_b`, submit Distributor B's 120-packet order. It faces a **32-packet shortage** against the 88-packet balance and cannot be approved.

## Ledger and Costing Rules

- **Signed movements.** A posted stock movement has an item, signed quantity, unit, date, and source document. Positive quantities add stock; negative quantities remove it.
- **Derived balances.** Balances sum each movement's converted base quantity, optionally through a selected date. Draft purchases and production runs do not affect stock.
- **Safe posting.** Purchases, production runs, adjustments, and dispatches post transactionally. Duplicate posting is prevented, and stock is checked before any issue is recorded.
- **Costing.** Production material issues use the moving weighted-average cost at posting time. A run stores its material rates and extended costs, so later purchases never rewrite recorded cost.
- **Wastage.** Wastage is included in the quantity issued. In the benchmark, 194 pouches are issued and 6 are recorded as damaged; the 6 are not deducted a second time.
- **Alerts.** Material and yield alerts trigger when the absolute variance exceeds 3%.

## Reports and Audit

### Reports

Administrators can open **Reports** at `/reports/`. Each report supports inclusive **From** and **Through** dates and exports to **CSV** and **PDF**.

| Report | Purpose |
| --- | --- |
| Sales register | Invoiced sales for the period |
| GST summary | Tax collected by component |
| Purchase register | Material receipts and costs |
| Stock summary | Opening stock, period movements, and closing stock through the end date |
| Stock ledger | Movement-level detail (filterable by item) |
| Production variance/cost | Yield and material variances with costs |
| Collections/outstanding | Payments received and balances due |

The separate **Visualization** card opens `/reports/visualizations/`, where charts summarize the selected period.

### Invoice integrity and credit notes

- Issued invoices preserve the seller, customer, HSN, price, tax, and line values used at issue time.
- Invoices cannot be silently edited or deleted through the application.
- An administrator can issue a **dated, reasoned full credit note** for an unpaid invoice.
- A credit note does **not** automatically return dispatched goods to stock.

### Audit trail

Key posting and invoice actions record the acting user and a timestamp.

## Testing and Verification

Run the following from PowerShell. The temporary `DATABASE_URL` directs tests to local SQLite even if `.env` points to Supabase.

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
$env:DATABASE_URL = "sqlite:///db.tests.sqlite3"
.\.venv\Scripts\python.exe manage.py test
Remove-Item Env:DATABASE_URL
```

The test suite covers role access, stock posting, the production benchmark, order and invoice totals, dispatch, payments, report exports, audit events, and invoice immutability.

## Documentation

- [Design Note](docs/DESIGN_NOTE.md): schema overview, unit-conversion assumptions, valuation details, and known gaps.
- [Benchmark fixture](data/benchmark.json): the seed data used by the demo and tests.

## Current Limitations

This project is a working benchmark prototype. Before issuing business invoices, configure and verify actual GSTIN, HSN, company, and customer data.

Not currently implemented:

- Partial credit notes
- Refunds for paid invoices
- Automated returned-goods processing
- Digital signing
- E-invoicing
- GST return filing