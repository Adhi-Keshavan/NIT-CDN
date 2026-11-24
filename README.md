# NIT-CDN

Days 1–4 Django order-to-audit system for customers, products, materials, bills of materials, purchasing, production, sales orders, GST invoicing, payments, stock ledger, reports, credit notes, and audit events.

Django keeps business rules and role checks in one backend, while PostgreSQL gives the ledger transactional writes and exact decimal arithmetic. The responsive UI has role-specific navigation, interactive order lines and production variance previews.

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

For a **one-command local demo** after installing requirements, run this instead of the three management commands above. It uses `db.local.sqlite3` even if `.env` points to Supabase, applies migrations, upserts the benchmark, checks the app, and starts the server:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
```

To verify startup on a fresh, separate database without keeping a server open, run `powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -Database db.day4-check.sqlite3 -NoServer`. The script deliberately never writes to Supabase. Use normal `manage.py migrate` and `manage.py seed_benchmark` with your Supabase `DATABASE_URL` for that database.

## Login, signup, and roles

The login page links to `/signup/`. New users enter their name, email, password, and requested role (Salesman, Production, or Admin). Signup creates an **inactive** account, so choosing Admin does not grant access. An existing admin opens `/management/access/` to approve or reject each request. Approval activates the account and grants the requested role; rejection leaves it inactive. The first admin can come from `createsuperuser` or the benchmark seed. Email addresses are collected for account identity, but this prototype does not send approval emails.

| Role | After login | Access |
| --- | --- | --- |
| Salesman | Sales orders | Create orders and view only their own orders and invoices. |
| Production | Production | Receive purchases, post runs, and view stock. |
| Admin | Overview | Sales, production, management, account approvals, and ERP master data in Django admin. |

The server enforces these permissions on every route. Business admins can edit ERP products and customers in Django admin, including HSN and GSTIN setup, while account requests are reviewed from the People page. Signup and approval pages are responsive for phone-sized screens.

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

Posting is explicit and transactional. Supervisors can post purchases and production runs through `/production/`. In Django admin, save a purchase and select **Receive selected purchases and post stock** to add its material; save a production run with one actual consumption line for each BOM material and select **Complete selected runs and post stock** to subtract materials and add good packets; approve an order after the stock check, then select **Dispatch selected orders and post stock** to subtract finished goods. Stock adjustments have their own document and lines, posted with **Post selected stock adjustments**. Each action is safe to repeat, checks stock before an issue, and prevents editing posted quantities. Draft documents do not change stock.

## Day 2 supervisor workflow

1. Log in as `production_supervisor` or `factory_admin`. Open `/production/purchases/new/`, receive **60 kg flour at ₹45/kg** dated after the opening balance, and check `/production/stock/`. Flour goes from **100 to 160 kg**. Each purchase receipt has a linked ledger entry.
2. Open `/production/runs/new/`, select **Murukku 200 g**, and choose **one batch**. The standard BOM shows **40 kg flour, 10 L oil, 1.2 kg spice, 200 pouches, 10 cartons**, and **200 expected packets**. Enter actual issues of **40 kg flour, 10.5 L oil, 1.2 kg spice, 194 pouches, 9 cartons**, with **6 damaged pouches** and **188 good packets**. The form previews issue and yield variances as values are entered.
3. Post the run. The run detail page shows **+5% oil** and **−6% yield**, both beyond the **3%** alert threshold. Posting creates five material issues and one finished packet receipt in one transaction. If any material is short, none of the run or stock movements are saved. Repeating a posting service call does not create more movements.
4. On the run detail page, enter **₹1,200 labour** and **₹600 gas/power**. The change is recorded with user and time, and cost per good packet becomes **₹31.14**. The run detail and stock summary pages show the traceable movements and resulting balances.

The benchmark tests reproduce flour **120 kg**, oil **19.5 L**, pouches **306**, and finished goods **238 packets** after the run. Day 1's order dispatch test then verifies **88 packets** after Retailer A and a **32 packet** shortage for Distributor B. These transactions run in isolated tests; `seed_benchmark` itself loads only master data and opening stock.

## Valuation, variance, and wastage rules

- Material cost uses a moving weighted average in each material's base unit. Opening stock enters at its fixture `standard_cost`. Purchases enter at actual `quantity × rate`, converting quantity to the base unit for the average. Issues leave at the average immediately before the run. Positive stock adjustments enter at the current average (or opening rate when stock is empty). Posting a purchase, run, or adjustment before a later movement for the same item is rejected so historical average costs cannot silently change.
- Each production consumption stores its unit rate and extended cost at posting. The benchmark flour rate is **(100 × ₹42 + 60 × ₹45) ÷ 160 = ₹43.125/kg**. Material costs total **₹4,055**; with labour and gas/power, the run totals **₹5,855**, divided by **188 good packets** and rounded to paise. Later purchases do not change that run's recorded cost.
- Actual issued quantities **include** damaged or wasted material. Record **194 pouches issued**, of which **6 are damaged**; the ledger removes 194 pouches. Wastage is a subset of issue, so it is not subtracted a second time. The finished goods receipt is only the **188 good packets**. Material variance is `(actual issued − standard) ÷ standard × 100`; yield variance is `(good output − expected output) ÷ expected output × 100`. Alerts appear when the absolute variance is **greater than 3%**.
- The per packet margin shown is the product selling price minus production cost per packet, before GST or selling expenses. This is a production estimate, not an invoice profit calculation.

## Day 3 order-to-payment workflow

1. First complete the Day 2 purchase and production run so finished stock is **238 packets**. The benchmark seed only creates opening stock; it does not create the purchase, run, orders, invoices, or payments.
2. Log in as `salesman_a`. On `/sales/`, create an order for **Retailer A**, **150 Murukku 200 g packets**. The phone-sized form uses the current product master price (**₹50 per packet**) and submits a draft order for review. Salespeople see their own orders and statuses only.
3. Log in as `factory_admin`. On `/management/`, review the order: **238 available**. You may edit the customer, products, or quantities while it is a draft; saved line prices are refreshed from the product master. Approve or reject with a reason. Rejected orders are visible to their salesperson and cannot be approved or edited.
4. Before invoicing, fill `/management/company/` with the company's **actual** name, address, state code, and GSTIN. Enter the product's **actual HSN code** in Django admin under Products. The benchmark statement supplies neither, and the app will refuse to issue an invoice with those fields missing. Customer address, state code, and optional GSTIN are editable under Customers in Django admin.
5. Issue the approved order's invoice from its review page. Confirm the place of supply. For the benchmark, invoice taxable value is **₹7,500**, CGST **₹187.50**, SGST **₹187.50**, IGST **₹0**, and total **₹7,875**. The invoice number follows `INV/2026-27/0001` for that financial year. Seller/customer identity, place of supply, HSN, item description, quantity, price, rate, and tax totals are stored as immutable snapshots. The invoice page offers an A4 PDF link for download or sharing.
6. Dispatch from the order review page after the invoice is issued. The existing ledger posts one finished goods issue, taking stock from **238 to 88 packets**. Repeating dispatch does not create another issue. Record a **₹4,000** payment on the invoice; its outstanding balance and Retailer A's customer balance become **₹3,875**.
7. As `salesman_b`, submit Distributor B's **120-packet** order. Admin review shows **88 available** and **32 short**; approval is blocked.

The financial year runs from April 1 through March 31. Tax is calculated from each invoice line's taxable value, with each component rounded to paise using half-up rounding. When supplier and place of supply state codes match, the product's GST rate is split equally into CGST and SGST. When they differ, the full rate is IGST. This follows the [CBIC invoice particulars](https://cbic-gst.gov.in/gst-invoice-rules.html) and [intra/interstate classification](https://cbic-gst.gov.in/pdf/e-version-gst-fliers/eflier-IGST-23062017.pdf) for this domestic goods workflow. GSTINs and HSN codes in automated tests are illustrative only; the seed does not set them. Review tax classifications, invoice particulars, and signing requirements for actual business use.

## Current scope

The app covers the benchmark purchase, production, order, invoice, dispatch, and payment flow, plus admin reports and full invoice credit notes. The [two-page design note](docs/DESIGN_NOTE.md) summarizes the schema and rules.

## Day 4 reports and audit

Admins open `/reports/` for the sales register, GST summary, purchase register, stock summary, stock ledger, production variance/cost, and collections/outstanding with ageing. Each report accepts inclusive **From** and **Through** dates and offers CSV and PDF containing the same rows. Stock summary shows opening balance before From, receipts/issues within the range, and closing balance through Through. The ledger shows running base-unit balances. Collections show invoices and payments during the range; outstanding and ageing include all invoices through the end date, using due date or invoice date when no due date exists. GST and sales registers include full credit notes as negative documents on the credit-note date. The reports use posted invoice snapshots, never current product prices.

Issuing an invoice, posting a production run, changing run overheads, recording payment, and issuing a credit note create timestamped audit events; actions from the UI record the acting user. Posted invoice and line values cannot be edited or deleted through model methods, bulk ORM changes, or Django admin. To cancel an **unpaid** invoice, admin opens its detail page and issues a dated, reasoned **full credit note** with a PDF download. The original invoice stays visible, its balance becomes zero, and later payment or dispatch is blocked. Dispatched stock is **not** automatically returned; a physical return needs a separate stock document. Paid invoices require refund/reconciliation before a full credit note, which this prototype does not implement. Partial credit notes, return authorizations, digital signing, e-invoicing, and GST return filing remain outside this demo. Audit events begin when this migration is applied; earlier documents have no reconstructed actor stamps. The [CBIC credit-note guidance](https://cbic-gst.gov.in/sectoral-faq.html) describes the underlying tax concept; review actual filing and timing requirements before real use.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
$env:DATABASE_URL='sqlite:///db.sqlite3'
.\.venv\Scripts\python.exe manage.py test
Remove-Item Env:DATABASE_URL
```

The test command above temporarily selects local SQLite so Django does not create a test database in Supabase. The suite includes the Day 2 benchmark and the complete Day 3 flow through the sales and admin pages, PDF response, interstate IGST, paise rounding, role restrictions, duplicate dispatch, shortages, and payment limits. Tests do not post benchmark transactions to Supabase.

Day 4 tests also replay the 238 → 88 packet benchmark, verify ₹7,875 invoice/₹3,875 outstanding and ₹31.14 production cost, exercise every report's HTML/CSV/PDF output and date filter, and check credit-note, audit, and immutability rules. No test transactions are posted to Supabase.
