# Dashboard honest failure — spec

**Status:** implemented on `feat/dashboard-honest-failure` (2026-09-22).
**Amends:** `specs/v1.2/features/GET_BOOK_SUMMARY_SPEC.md`, resilience
rule: "If a single warning condition can't be checked (table
missing, query fails), skip that warning but emit the others."

## Problem

The rule above was implemented as twelve `except Exception` handlers
across the three dashboard collectors (`_business_summary_counts`,
`_overdue_scheduled_warnings`, `_collect_warnings`). Five were a bare
`pass`; the rest logged at debug level. Two consequences:

1. **A failed check reads as a clean book.** A warning's absence is
   the signal ("don't print Warnings: none"), so a check that fell
   over is indistinguishable from a check that found nothing. This
   is how the overdue-invoice warnings went dark on Python 3.10: a
   GDATE parse raised, the handler swallowed it, the dashboard said
   all clear. The debug log is opt-in; the users this reaches are the
   ones who never turned it on.
2. **On PostgreSQL, one failure blanks everything after it.** A
   failed SQL statement aborts the transaction; every later
   collector's query fails with `InFailedSqlTransaction`, each
   swallowed in turn. "Skip the failed check, emit the rest" was
   false on that backend.

## Rule

A failed check is **recorded, never swallowed**. Every handler in the
three collectors routes through one chokepoint, `_check_failed(book,
check, exc)`, which in order:

1. clears an aborted PostgreSQL transaction (`_rollback_if_aborted`,
   a no-op on SQLite and MySQL), so the next collector runs on a live
   connection;
2. logs the traceback at debug, for those who have a log;
3. returns the visible line, **reason inline**.

## Message shape

    <Check> check failed: <ExceptionType>: <first line of message>

- First line of the exception text only. SQLAlchemy's statement,
  parameter dump, and docs link live on later lines and never appear.
- Any URI in the text is password-masked through `_redact_uri`.
- Truncated with an ellipsis past 120 characters.
- **One line per check, never per item.** A per-item loop reports its
  first failure plus a count: `… — 3 documents skipped`.

Check names: `Business-count`, `Active-jobs`, `Overdue-schedule`,
`Low-cash`, `Overdue-document`, `Stale-price`, `Backup-health`,
`Legacy-recipe`.

## Placement

Failed-check lines render in the Warnings section immediately after
the data-integrity lines and before every other category: a check
that could not run calls the numbers below it into question the same
way an Imbalance balance does. They carry the ordinary `⚠` prefix.

## Lock

`TestDashboardHonestFailure` in `tests/test_contract_integrity.py`
walks the three collectors with the AST and fails if any
`except` handler (other than the `ImportError` module-not-loaded
guards) does not call `_check_failed`.

## Verification

- SQLite: a collector forced to raise produces exactly one visible
  line naming the check and the exception, and every other section
  still renders. A per-item failure across N documents produces one
  line with the count.
- PostgreSQL (real driver, `_RealDatabaseTests`): with the
  transaction poisoned ahead of a collector, the dashboard returns,
  that collector reports `InFailedSqlTransaction` once, and no later
  collector fails. MySQL, which has no aborted state, reports nothing.

## Bookkeeper note

New warning shape on the dashboard. On a healthy book nothing
changes; the lines appear only when a check fails, and the message
is the thing to paste into an issue.
