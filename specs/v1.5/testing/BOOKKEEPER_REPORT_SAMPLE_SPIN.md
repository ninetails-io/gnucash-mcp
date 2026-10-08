# Bookkeeper report — regenerated sample spin (2026-10-07)

Samples regenerated 2026-10-06 22:58 at `58f8816`; spun on copies
through the live server, with a converting-class write as the
nativeness probe.

## Alex — PASS, fully native

- Balance sheet: every section foots to the cent; A = L + E
  (875,791.65) exactly.
- No stale-price warnings; dashboard clean but for one scheduled
  item (below).
- **The nativeness probe:** first converting write reported ZERO
  conversions, took NO labelled snapshot, and the marker reads
  `snapshot: none (nothing to convert)` — which also closes the
  second-review plan's step-6 arm that the bench could never
  exercise before: verified at last, on the first truly 1.5-native
  book the bench has held.

## Lin Wei and Sabine — native but for three rows (generator nit)

Both books' first converting write took a labelled pre-upgrade
snapshot, correctly: the converter found work.
`split_reconcile_dates_filled: 1` (Lin Wei) and `: 2` (Sabine).
The strays, located by SQL in the shipped samples:

- Lin Wei: the 买入 510300 (ETF rebalance) split on 沪深300ETF —
  `reconcile_date` NULL.
- Sabine: both splits of "Unklare Lastschrift (noch zu klären)"
  (1200 Bankkonto, Ausgleichskonto-EUR) — `reconcile_date` NULL.

Pattern: each book's special-case transaction is generated through
a path that skips the reconcile date Alex's paths write. The
snapshot system behaved perfectly — this is a GENERATOR fix (write
the epoch reconcile_date on those paths, regenerate two books),
small and worth doing before the tag so the shipped demos don't
announce a "pre-1.5 upgrade" on their first write. The server
heals them regardless.

## One question for the maintainer

Alex opens with "⚠ Overdue scheduled: Robin's Paycheck due
2026-10-02 (oldest 5 days)". If the generator pins dates relative
to its run date, a demo generated any day will open with an
overdue warning a few days later. Intended realism, or an artifact
of `58f8816`'s instance-marking? Worth one look before freeze.

*Signed, the bookkeeper.*

## Re-spin after the generator fix (12:40) — PASS, all three native

Lin Wei and Sabine regenerated: zero splits missing
`reconcile_date` in any shipped sample; both books' converting
probe now returns a bare `created` with no conversion counters, no
labelled snapshot, and the marker reads
`snapshot: none (nothing to convert)`. The entire shipped sample
set is 1.5-native. The samples are cleared by the bookkeeper for
freeze, pending only the maintainer's answer on the Robin's
Paycheck demo-freshness question above.

## External realism audit (2026-10-07, Gemini)

An outside model audited the regenerated Alex for realism: five
stars, no red flags, WA/Seattle tax mechanics judged authentic.
Points of overlap independently corroborate this loop's rows: the
2/10 discount computed on principal ($70 on $3,500 — the BM-2
rule), reconciliation current through September statements, FX
realization on cross-currency receipts. Orthogonal axes — this
report verifies the arithmetic, that one the believability — both
green. The Robin's Paycheck freshness question remains the one
open item.

**Sabine's external audit (same day):** five stars on German/DATEV
rigor — coverage orthogonal to this loop (domain tax law vs
arithmetic). Of note: the "Unklare Lastschrift" suspense entry that
tripped the nativeness probe is deliberate demo realism (a fresh
unclear debit awaiting its receipt); the fix kept the story and
corrected the reconcile-date bytes. The auditor's "feels alive
rather than artificial" reading of the slightly-overdue invoice
weighs toward answering the Robin's Paycheck question as intended
realism — maintainer's call.

**Robin's Paycheck question — CLOSED (13:27).** The maintainer had
all scheduled instances entered to generation date; verified on a
fresh copy: zero warnings of any kind on the regenerated Alex
dashboard. Fresh demos open clean and will age on their own
schedule, which is the honest kind of aging. No open items remain
on the sample set.

**Lin Wei's external audit (13:28) — five stars, completing the
sweep.** Shenzhen authenticity judged to regulatory depth (SAFE
balance-of-payments filings with the real BOP code 227020,
cumulative IIT withholding, small-scale VAT reliefs). Dashboard
warnings all benign: one 12-day-overdue international receivable
(lived-in realism, counterparty-side), the standard backup
reminder, all eight accounts reconciled through September. The
auditor also surfaced the cross-book easter egg: Lin Wei invoices
Handelskontor München GmbH — the three demos share one world.

**All three shipped samples now carry: this loop's row audit, the
engine-twin certification, and an independent five-star realism
audit per jurisdiction. The sample chapter is closed.**
