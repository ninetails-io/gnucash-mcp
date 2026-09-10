# Budget sign convention vs. GnuCash 3.8+

Date: 2026-09-09. Trigger: GnuCash 5.12 showed the book notice
"This book has budgets. The internal representation of budget
amounts no longer depends on the Reverse Balanced Accounts
preference" on the maintainer's real book. The book was expense-only,
so nothing changed there. The investigation found a convention
mismatch that is silently wrong for anyone else.

## What GnuCash does (source-verified, stable branch)

- Since 3.8, budget amounts are stored in the account's NATURAL sign.
  The budget editor (`gnc-budget-view.c`, `budget_col_edited` /
  `budget_col_source`) negates on entry and on display when
  `gnc_reverse_balance(account)` is true. Under the default
  "credit accounts" preference (`gnc-ui-util.cpp`) that set is
  LIABILITY, PAYABLE, EQUITY, INCOME, CREDIT. So a 5,000 income
  target is −5000 on disk and shows as 5,000.
- The book is stamped with feature `Use natural signs in budget
  amounts` — the VALUE of `GNC_FEATURE_BUDGET_UNREVERSED` in
  `gnc-features.h`, verbatim (KVP `features/Use natural signs in
  budget amounts`, value = the description string; presence of the
  key is the test). GnuCash refuses to open a book carrying a key it
  doesn't know.
- A book with budgets and no stamp gets `gnc_maybe_scrub_all_budget_signs`
  at open (`libgnucash/engine/ScrubBudget.c`). Per budget, each
  account's total across set periods contributes its sign (−1/0/+1)
  to a tally by type; policy = expense tally < 0 → flip INCOME and
  EXPENSE rows; else income tally < 0 → flip nothing; else flip
  LIABILITY, EQUITY, INCOME rows. Then stamp. On the SQL backends
  this is written at open, not on save.

## What this server did

`set_budget_amount` stored the caller's magnitude for every account
type; `get_budget`, `get_budget_report`, and the dashboard headline
read the stored value raw; the report negated income ACTUALS to
compare. Internally consistent, and wrong against the app in both
directions:

- A GnuCash-native income row (−5000, stamped) reported as a −5,000
  target at 0% used. Demonstrated on a scratch Alex.
- An income row written here (+5000, no stamp) displayed as −5,000
  in GnuCash once its scrub picked the credit-accounts policy — the
  policy every book of ours lands on, since our expense totals are
  never negative.

No generator, sample book, or test ever budgeted an income account,
which is why no loop saw it.

## The fix (branch `fix/budget-sign-convention`)

One chokepoint in `book/_base.py`:

- `_budget_stored_sign(account)` — −1 for the credit-normal set, +1
  otherwise. The only sign the writer applies.
- `_budget_targets(book, budget)` — the only reader. Stamped book →
  natural sign to magnitude. Un-stamped book → the same heuristic
  GnuCash will apply, virtually, so the numbers don't change under
  the user when GnuCash later opens the book. Readers never write.
- `_ensure_budget_unreversed(book)` (budgets mixin, write path
  only) — performs GnuCash's scrub, negating the numerator column
  as `gnc_numeric_neg` does (the Decimal setter would re-derive the
  denominator), then stamps. Called by `create_budget` and
  `set_budget_amount` before their own write.

Surface unchanged: amounts are magnitudes in and out for every
type. Audit `prior_amounts` are magnitudes. Expense-only books
(all three samples) are byte-identical before and after; their
first budget write will add the stamp.

Known narrowness, mirrored deliberately: GnuCash's scrub flips
LIABILITY / EQUITY / INCOME but not PAYABLE / CREDIT, while its
display reverses all five. A pre-3.8 credit-card budget row
therefore displays negative in GnuCash after the scrub, and here
too. Matching the app beats correcting it.

## Locks

`tests/test_budget.py::TestBudgetSignConvention` (round trip,
stamp on create, native-book read, legacy read + scrub on write,
both heuristic policies, the already-natural no-op, audit
magnitudes, headline agreement) and a grep lock in
`test_chokepoint_invariants.py` that keeps `Decimal(str(ba.amount))`
out of `book/*.py` outside `_base.py`. The sign helper was mutated
to a constant and the class failed.

## Postscript, same day: the key was wrong on the first cut

The first commit wrote the stamp under `features/Budgets: sign
reversal fixed`. That string was written from memory; the source
fetch that "confirmed" it only quoted the description, never the
`#define`. Every unit test passed. GnuCash 5.12 refused to open the
bookkeeper's PRODUCTION book ("features not supported by this
version"), and the bookkeeper repaired it with raw SQLite after
taking a backup. Fixed to the verbatim define, with a test that pins
the key against a copy of the header line, and a migration that
deletes the bogus row on the next budget write (the key was never
released). Standing rule from this: any GnuCash-facing constant is
fetched and quoted from the source before it is typed, and any
branch that writes book slots has "opens in GnuCash desktop" as its
acceptance gate. Nothing else could have caught it.
