# Administrator report outputs

The Reports page's top exports explicitly cover all recorded dates.
Print Report opens a standalone print preview, not a screenshot of the dashboard.
Use A4 portrait; disable browser Headers and footers to avoid adding the URL.
Download PDF provides fixed-layout, paginated output with an embedded DejaVu font.
The unmodified font and its redistribution license are in `reports/fonts/`.
Print, PDF and detailed CSV use short headings and notes. Essential collection,
purchase, purchased-time and expense-estimate distinctions stay visible; numeric
values, date ranges, CSV column order and the single capital total are unchanged.
Print tables flow continuously instead of forcing the daily summary onto a new page.

## Scope and definitions

- Daily: today in Asia/Manila. Weekly: last 7 dates including today. Monthly:
  last 30 dates including today. Custom ranges include both endpoints, normalize
  reversed dates, reject malformed dates, and are limited to 10 years.
- Coin revenue is `CoinEvent.amount` by payment timestamp, including unassigned
  coins and extensions of older sessions. It does not use saved daily summaries
  or attribute a session's lifetime amount to its start date.
- Plan figures use `PurchaseTransaction` by timestamp. They are not additional
  revenue to add to collections. A transaction is not necessarily one plan unit.
  Current configured price is labeled separately from the amount actually paid.
- Session counts mean session starts, not unique customers, concurrent users or
  resumes. Duration is purchased minutes, not observed online duration.
- Today is partial as of the displayed generation timestamp. Zero-revenue dates
  are included, but do not establish whether the system was operating.
- All-time print/PDF/period-CSV reports retain operating-cost estimates, total
  capital investment and operating surplus. Recurring cost rows remain itemized;
  individual one-time capital items are replaced by one Total Capital Investment
  row after the recurring rows. Stored capital records are not changed or deleted.
  Estimates use the dashboard's existing recurring
  cost model; they are not a history of paid bills. Capital is shown separately,
  not deducted from operating surplus. No accounting records are changed.
- Session CSV amounts are current lifetime session amounts, not cash received
  during the export window. Saved usage is MiB (bytes / 1048576), not a live probe.

## CSV and privacy

Revenue CSV is a flat daily table with a totals row. Period CSV is a labeled,
eleven-column report containing summary, daily, purchase-ledger and session
sections. Sessions CSV remains a flat session table and preserves the Users
page's existing filter behavior. Top-level Reports links explicitly request all
sessions. Files use UTF-8 BOM, proper CSV quoting, numeric monetary cells and
formula-injection protection for untrusted text, including leading whitespace.

Exports remain staff-only and private/no-store. Print/PDF summaries omit MAC/IP
identifiers; session/detailed CSV files contain them and must be kept private.
These exports are not encrypted or audited financial statements.

## Deployment and verification

This change uses `reports/web_exports.py` only from web views. The scheduled
Celery delivery pipeline continues using `reports/generator.py`, unchanged.
Do not restart background workers or run scheduled delivery to verify this web
change. No migration, network change, settings POST or customer-record edit is
required. Reload only `pisowifi` after the usual backup and fast-forward.

Run `reports.test_web_exports`, `reports.tests`,
`dashboard.tests.ReportsEnhancementTests`, `dashboard.tests.DashboardSecurityTests`,
`dashboard.tests.UsersSessionsHardeningTests` and
`dashboard.tests.RevenueHardeningTests` using an in-memory test database/cache
and mocked hardware. Keep pipeline test files in a disposable MEDIA_ROOT.
Visual QA must include multi-page print/PDF output, repeated headers, long names,
peso/accented glyphs, totals and an empty period, using synthetic records only.
Production verification should use authenticated read-only GETs and a read-only
database transaction, inspect results privately on the Pi, and disclose no
customer records in logs/chat or public artifacts.
