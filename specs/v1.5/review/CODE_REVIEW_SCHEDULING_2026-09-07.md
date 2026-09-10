# Adversarial read: `book/scheduling.py` against GnuCash's own SX semantics

Date: 2026-09-07. Scope: the whole file (1,122 lines), its tool wrapper,
`tests/test_scheduled.py` (53 tests, green at baseline), the dashboard's
overdue helper in `core.py`, and GnuCash's `SchedXaction.cpp` /
`gnc-sx-instance-model.c` (stable branch, fetched today) for the semantics
the file reimplements. Every finding below was either reproduced with a
script against the sample books or verified against quoted GnuCash source.
Nothing here is inferred from reading alone.

Why this file: it has the highest raw-SQL density per line in the tree
(11 sites in 1,122 lines) because piecash models the SX row and its
recurrence but not the template recipe. Everything piecash doesn't model,
this file re-derives from GnuCash's rules by hand. That is where the
monthly bug stream has come from.

## Findings

### S-1 HIGH — Overdue occurrences are skipped by every surface except the dashboard, then locked out

Five sites compute "the next occurrence" and they disagree on the
threshold:

| site | `after=` | effect |
|---|---|---|
| `_overdue_scheduled_warnings` (core.py) | `start - 1 day` | first un-instantiated occurrence, i.e. the OVERDUE one |
| `_sx_to_dict` (list_scheduled_transactions) | `today - 1 day` | first occurrence ≥ today |
| `get_upcoming_transactions` | `today - 1 day` | same |
| `_upcoming_within_days` (dashboard Scheduled line) | `today - 1 day` | same |
| `create_transaction_from_scheduled` default date | `today - 1 day` | same |

Reproduced on all three sample books (`scratchpad/sx_probe.py`). Alex,
today: 15 of 17 enabled schedules diverge. Example, Estimated Tax Payment
(quarterly, last_occur 2026-04-15): dashboard says "Overdue scheduled: due
2026-07-15"; `create_transaction_from_scheduled` with no date would post
**2026-10-15**, a future-dated transaction five weeks out, and set
`last_occur = 2026-10-15`. The last_occur guard ("not after last
occurrence") then refuses 2026-07-15 forever. The missed quarter can only
be recovered with a plain `create_transaction`, and the schedule's
`instance_count` is wrong from then on.

GnuCash's own Since-Last-Run creates every instance from `last_occur`
forward through today; it never jumps ahead. The dashboard already has
the correct rule. The other four sites are siblings enforcing it by hand,
wrongly. This is the check-and-act class exactly.

Fix shape (small): one helper `_sx_schedule(sx)` returning
`(frequency, start, end, last)` with the datetime normalization done
once, and one policy for `after` (the dashboard's) used by all five
callers. `get_upcoming` should list overdue occurrences first with a
negative `days_until`; the tool default should instantiate the oldest
un-run occurrence. Lock: a test that creates a schedule with two missed
periods and asserts the dashboard date, the upcoming date, and the
instantiation default are equal.

Note: `TestNextOccurrence::test_last_occur_earlier_than_after_ignored`
locks helper-level behavior that is fine; the bug is the callers'
choice of `after`, not the helper.

### S-2 HIGH (anyone who also opens GnuCash desktop) — MCP-created schedules are empty to GnuCash, and desktop schedules are empty to MCP

Storage: this server keeps the recipe in a `splits-json` slot on the SX
row. GnuCash keeps it as real Transaction rows on the template account
(`sched-xaction/account`, `credit-formula`, `debit-formula`,
`credit-numeric`, `debit-numeric` split slots). Neither side reads the
other's form.

Consequence in the desktop direction, verified against
`gnc-sx-instance-model.c`: for an enabled SX whose occurrence date has
passed, Since-Last-Run generates a `TO_CREATE` instance;
`create_transactions_for_instance` iterates the template account's
transactions (none), produces nothing, records no error; `instance_errors
== NULL` so `increment_sx_state` advances `last_occur` and
`instance_count` anyway. So a user who opens their book in GnuCash and
clicks OK on the Since-Last-Run dialog silently consumes every
MCP-created schedule's period with no transaction posted, and this server
then refuses the date ("already been run through that date, possibly by
GnuCash desktop"). The guard's own message assumes desktop posted
something. It didn't.

Consequence in the MCP direction: a desktop-created SX has no
`splits-json`, so `create_transaction_from_scheduled` raises "No split
templates found". The dashboard will flag it overdue every month with no
tool able to act on it.

Fix shape (medium-large, storage format change, Stephen's call): write
the native form. Create a template transaction on the template account
with GnuCash's split slots; read the native form in `_get_sx_splits`,
falling back to `splits-json` for existing schedules (or migrate on
first read). Also align the template account with GnuCash's constructor
(name = SX guid string, commodity = the `template` pseudo-commodity,
type BANK; today it is name = SX name, commodity = default currency).
`delete_scheduled_transaction` already handles native recipe rows. The
`_template_account_guids` filter already hides template transactions
from reports. Amounts as `credit-numeric`/`debit-numeric` are exact
GncNumeric; formulas are strings, so `_to_decimal` discipline holds.

### S-3 MEDIUM — Stored split refs are raw paths; an account rename or move breaks the schedule silently until its due date

`create_scheduled_transaction` runs `_validate_transaction_splits`
(which resolves every ref to an Account) and then stores
`s["account"]` — the caller's original string — in `splits-json`.
Reproduced (`scratchpad/rename_probe.py`): rename `Expenses:Streaming`
to `Expenses:Streaming Services`, then instantiate "Streaming Bundle":

```
Account not found: Expenses:Streaming. Did you mean: 'Expenses:Streaming Services' ...
```

`list_scheduled_transactions` verbose still shows the dead path. GnuCash
stores the account GUID in the template split, so renames are free
there. A `%xxxxxxx` short ref stored here is nearly as fragile: it
resolves until a later account collides on the prefix.

Fix shape (small): store `v["account"].guid` (full GUID) from the
validated splits; render the fullname at read time. Folds into S-2 if
the native form is adopted, since the native split slot IS a GUID.

### S-4 MEDIUM (desktop-created books only) — Four GnuCash schedule features are silently ignored

All code-verified, none reproducible on the sample books because the
generators only create MCP-shaped schedules. Real books from GnuCash
users will carry these.

- **`num_occur` / `rem_occur` ignored.** GnuCash clears the next
  instance when `rem_occur == 0` (verified in
  `xaccSchedXactionGetNextInstance`). This file never reads either
  column: a finished 12-payment schedule shows as upcoming/overdue
  forever. Instantiation never decrements `rem_occur`, so desktop will
  over-create after MCP runs one.
- **Period types beyond `week`/`month`/`year` at mult 1/2/3.** `day`,
  `once`, `end of month`, `nth weekday`, `last weekday`, and any other
  multiplier (semiannual = month×6) map to frequency `"unknown"`. They
  are dropped from upcoming, from the overdue warning, and from
  instantiation ("Unknown recurrence frequency"). A desktop user's daily
  or semiannual schedule is invisible to the dashboard.
- **Composite schedules.** GnuCash allows several recurrence rows per SX
  (e.g. 1st and 15th). piecash's `recurrence` relation is
  `uselist=False`; the extra rows are ignored.
- **`recurrence_weekend_adjust`** (`back`/`forward`) is written as
  `none` and never read.

Fix shape: `num_occur`/`rem_occur` is small and belongs with S-1's
chokepoint (the helper returns None when `rem_occur == 0` on a
`num_occur > 0` schedule; instantiation decrements it). The period-type
expansion is medium and should be its own decision: at minimum, list
unknown-frequency schedules with their raw `(period, mult)` and surface
them in the overdue count rather than dropping them.

### S-5 LOW — Torn-write cleanup commits, and is correct only by cascade

`create_scheduled_transaction`'s except block does
`session.delete(template_acct); book.save()`. The raw-SQL SX,
recurrence, and slot inserts that already ran are in the same
transaction, so that `save()` commits them. Probed
(`scratchpad/torn_probe.py`, failure injected on the description-slot
insert): nothing persisted. Instrumenting showed why: piecash's
`Account.scheduled_transaction` relation is `cascade="all,
delete-orphan"`, so deleting the template account loads the
half-written SX row by `template_act_guid` and deletes it, which
cascades to its recurrence and slots. Correct outcome, wrong reason.
`book.cancel()` (rollback) is the honest form: it also un-flushes the
template account, and it stops depending on a piecash relationship
nobody in this file mentions. The existing
`test_torn_write_cleans_up_template_account` injects the failure before
any raw insert, so it can't distinguish the two.

### S-6 LOW — Cosmetic and interop details

- `_overdue_scheduled_warnings` comment says "Search relative to
  'yesterday'" while the code passes `after=start - 1 day`; the
  `< today` check is what excludes today. The comment describes the
  four wrong sites, not this right one.
- Transactions instantiated here don't carry GnuCash's
  `from-sched-xaction` slot (the SX GUID), so desktop can't tell they
  came from a schedule. Trivial to add alongside S-2.
- `update_scheduled_transaction` can change enabled/end_date/notes only.
  Amount, splits, frequency, start date, description, currency all
  require delete-and-recreate. Not a bug; it is the first friction a
  bookkeeper hits at a rent increase, and it will be routed around
  silently. Filed here so it isn't rediscovered.

## What is sound

- `_next_occurrence` anchoring (`start + n×period`, never chained)
  matches GnuCash's month-end clamp exactly (Jan 31 → Feb 28 → Mar 31),
  and both drift tests lock it.
- The three-phase instantiate (read, create in its own session,
  advance) is the right shape; a raise in phase 2 leaves the schedule
  untouched, and phase 3 never rewinds.
- `delete_scheduled_transaction` handles native recipe transactions,
  deletes slots explicitly, and piecash cascades the recurrence row.
- Every raw-SQL write has its `_verify_*` (13 for 11 sites).
- Currency and cross-commodity templates are validated at create time
  through the shared split contract, so a template can't be created
  that instantiation couldn't post.

## Verdict on a branch

Yes, one branch, from `develop`, not from the business refactor:

**`fix/scheduled-occurrence-chokepoint`** (small-to-medium, no report
numbers shift, bookkeeper loop on the sample books):
1. S-1: `_sx_schedule` chokepoint + one `after` policy; overdue-first
   upcoming; instantiate-the-oldest default; agreement lock.
2. S-3: store full account GUIDs in `splits-json`; render paths.
3. S-4 first bullet: honor `rem_occur`; decrement on instantiate.
4. S-5 and S-6 comment fix as passengers.

**S-2 and the rest of S-4** are a storage-format decision (native
template transactions) that changes what GnuCash desktop sees. That is
a spec first, then its own branch, and it is a candidate 1.5 item: it
is the one change that would make this module stop being "its own
GnuCash" and start delegating the recipe to the real one.

## Repro scripts

All read-only or on scratch copies, in the session scratchpad:
`sx_probe.py` (S-1, all books), `rename_probe.py` (S-3),
`torn_probe.py` / `torn_why2.py` (S-5). Regenerable from the
descriptions above; none touched a committed book.
