# Money math and reports: review of d42400e..e99d46e

*The reviewer could not write this file itself; its report is kept
here verbatim from its return. Repro scripts were in the session
scratchpad (`m1_budget_dashboard.py` … `m7_first_budget_snapshot.py`).*

Seven defects: one SERIOUS, six MINOR, all reproduced. The diff's core
money changes (C63 date ranges, per-account rounding, C20 printing,
MM-12, C46, FC-20) held up under testing apart from these.

## Findings

**M-1 — SERIOUS, CONFIRMED.** The dashboard budget line still uses
one period-end rate; MM-12 fixed only the report. `core.py`,
`_budget_headline`: it converts both targets and actuals at a single
rate taken at the budget's last period end. In the repro, a euro
expense account holds 100 EUR in January (rate 1.0) and 100 EUR in
February (rate 1.4): dashboard `USD 400 spent / USD 1,832 expected`;
`get_budget_report(period="ytd")` actual 240.00, budgeted 1840.00.
Fix: build monthly factors over the budget's span and convert as the
report does; lock headline and report together.

**M-2 — MINOR, CONFIRMED.** Flow reports print lines that don't add up
to their TOTAL, and the structured output is unrounded. With three
EUR categories of 10.00 at 1.0005 the table printed `10.00 / 10.00 /
10.00 / TOTAL 30.02`; `compact=False` returned `"10.0050"` and
`"30.0150"`; `cash_flow` returned `"outflows": "30.0150"`. Fix: round
each cell once, totals from the rounded cells, pick the grain so
`TestModeAgreement` holds.

**M-3 — MINOR, CONFIRMED.** `calculate_lot_gain` rounds cost,
proceeds and gain separately: 3 shares for 100.00, 1 sold at 10.015
gives proceeds 10.02, cost 33.33, gain −23.32 instead of −23.31; and
proceeds, a split value, should round half-up. Pre-dates C20.

**M-4 — MINOR, CONFIRMED.** A desktop schedule amount finer than its
currency (1001/1000) is refused when instantiated; desktop's
Since-Last-Run rounds it half-up to 1.00.

**M-5 — MINOR, CONFIRMED, pre-existing.** In UTC−11 and UTC+14 the
server's own transactions decode a day off (piecash binds a flat
10:59 UTC; `_neutral_time` was applied only to documents and
prices), so C63 puts them in the wrong period.

**M-6 — MINOR, CONFIRMED.** The pre-upgrade marker names a snapshot
that `_withdraw_pre_upgrade_snapshot` then deletes.

**M-7 — MINOR, CONFIRMED.** A fresh book's first budget keeps a
"pre-1.5 upgrade" snapshot: `book_stamped` counts as a conversion,
though stamping a first budget is what desktop itself does.

## Examined and sound

Date ranges (slack covers UTC−12..+14; `date.min`/`date.max` bounds;
compact-row clause; stable re-sort; cache keyed on `_cache_token`;
no other SQL-side date comparison). Per-account rounding (every
`_market_value` caller passes a whole balance; four net-worth figures
matched to the cent on 36 random books and the three samples; A = L
+ E; dust rows 0.00). Amount printing (ISO and non-ISO commodities,
JPY, BHD, non-decimal fractions, negative zero, prices). MM-12
period-end month mapping. C46 `compute_time` port. C26 reader. FC-20
mark written once, never on reads or dry runs; no budget row
rewritten.
