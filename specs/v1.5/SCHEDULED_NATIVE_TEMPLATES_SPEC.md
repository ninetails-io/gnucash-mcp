# Native template transactions for scheduled transactions

Status: GO given 2026-09-10; implemented on
`feat/scheduled-native-templates` (see §9 for what changed from
this plan). Closes S-2 from
`review/CODE_REVIEW_SCHEDULING_2026-09-07.md`.

Every GnuCash constant below was fetched from the `stable` branch on
2026-09-10 and is quoted verbatim, per the rule that came out of the
budget-stamp incident (`feedback_desktop_open_is_the_gate`). Where a
GnuCash behaviour was not located in source, the spec says so rather
than guessing.

## 1. The problem

This server stores a schedule's recipe as a `splits-json` slot on the
`schedxactions` row. GnuCash stores it as real Transaction rows on the
schedule's template account, with the target account and the amounts
in KVP slots on each split. Neither side reads the other's form.

Consequences, both verified against `gnc-sx-instance-model.c`:

- **Desktop reading ours.** Since-Last-Run generates a `TO_CREATE`
  instance for any enabled schedule whose date has passed, iterates
  the template account's transactions (none), creates nothing,
  records no error, and advances `last_occur` / `instance_count`
  anyway (`increment_sx_state` runs when `instance_errors == NULL`).
  A user who opens the book in GnuCash and clicks OK marks every
  MCP-created schedule done for the period with nothing posted; this
  server then refuses the date behind the backfill guard.
- **Us reading theirs.** A desktop-created schedule has no
  `splits-json`; `create_transaction_from_scheduled` raises "No split
  templates found". The dashboard flags it overdue every month and no
  tool can act on it.

Who it touches: anyone using both GnuCash desktop and this server on
one book, in either direction. A server-only user never hits it. The
maintainer's real book has seven schedules rebuilt through this
server on 2026-09-05 and is opened in GnuCash; it is exposed today.

## 2. GnuCash's format, verbatim

### 2.1 Template split slots (`libgnucash/engine/Split.cpp`)

```c
#define GNC_SX_ID                    "sched-xaction"
#define GNC_SX_ACCOUNT               "account"
#define GNC_SX_CREDIT_FORMULA        "credit-formula"
#define GNC_SX_DEBIT_FORMULA         "debit-formula"
#define GNC_SX_CREDIT_NUMERIC        "credit-numeric"
#define GNC_SX_DEBIT_NUMERIC         "debit-numeric"
#define GNC_SX_SHARES                "shares"
```

The GObject properties the instance model reads (`sx-account`,
`sx-credit-formula`, …) map to two-element KVP paths
`{GNC_SX_ID, <key>}`, i.e. on disk, in the `slots` table:

| path | type | value |
|---|---|---|
| `sched-xaction` | frame (9) | frame guid |
| `sched-xaction/account` | guid (5) | target account GUID |
| `sched-xaction/credit-formula` | string (4) | formula text |
| `sched-xaction/debit-formula` | string (4) | formula text |
| `sched-xaction/credit-numeric` | numeric (3) | gnc_numeric |
| `sched-xaction/debit-numeric` | numeric (3) | gnc_numeric |

Child rows carry the full path as `name` and the frame's `guid_val`
as `obj_guid`; piecash writes hierarchical keys the same way
(`kvp.py` `slot()`: `name = parent._name + "/" + name`), which is
what makes `gnc-mcp/…` namespaced slots and the budget stamp work.
`sched-xaction/shares` is legacy (a string; the source comment says
it should have been numeric) and is not written.

The SX editor writes BOTH sides on every split
(`split-register-model-save.c`: `save_cell(..., FCRED_CELL)` and
`save_cell(..., FDEBT_CELL)`), zero on the unused side, and attaches
the split to the schedule's template account
(`xaccAccountInsertSplit(template_acc, sd->split)`).

### 2.2 How Since-Last-Run turns a template into a transaction

`create_each_transaction_helper` in `gnc-sx-instance-model.c`:

- Target account: `sx-account` GUID → `xaccAccountLookup`. A split
  without it aborts creation for that schedule.
- Amount: when the instance has no variable bindings and the numeric
  is valid and non-zero, the numeric is used; otherwise the formula
  is parsed. `final = gnc_numeric_sub_fixed(debit_num, credit_num)`.
- Same-commodity splits get `value` directly. A split whose account
  commodity differs from the transaction currency goes through
  `split_apply_exchange_rate`, which looks up a variable named
  `"<split mnemonic> -> <txn mnemonic>"` in the instance's bindings
  (asked of the user in the Since-Last-Run dialog) and divides
  (non-currency) or multiplies (currency) to set the split amount.
- Transaction currency: `get_transaction_currency` starts from the
  template transaction's currency; if that currency is not among the
  split commodities it falls back to the first split currency, then
  the first split commodity. So any currency that appears in the
  splits is honoured. (The editor-side rule for what currency a new
  template transaction gets was not located in `split-register.c`;
  it does not matter here because the reader tolerates ours.)
- The new transaction is `xaccTransCloneNoKvp(template_txn)`
  (description and the splits' memo/action come across), dated to the
  instance, notes copied, and stamped
  `from-sched-xaction` = the SX GUID (`Transaction.cpp`:
  `#define GNC_SX_FROM "from-sched-xaction"`, a 1-element KVP path,
  GUID type).

### 2.3 The template account and commodity

`xaccSchedXactionInit` (`SchedXaction.cpp`) creates the template
account with name = the SX GUID string, type `ACCT_TYPE_BANK`, parent
= the book's template root, commodity = the template
pseudo-commodity. That commodity is created by
`gnc_commodity_table_add_default_data` (`gnc-commodity.cpp`):

```c
gnc_commodity_table_add_namespace(table, GNC_COMMODITY_NS_TEMPLATE, book);
c = gnc_commodity_new(book, "template", GNC_COMMODITY_NS_TEMPLATE, "template", "template", 1);
```

with `#define GNC_COMMODITY_NS_TEMPLATE "template"` (`gnc-commodity.h`):
fullname `template`, namespace `template`, mnemonic `template`, cusip
`template`, fraction 1. None of the three sample books carry that row
(piecash never creates it); books GnuCash has opened do, which is why
`list_commodities` already filters the `template` namespace.

## 3. Design

### 3.1 Storage: write native, read native first

`create_scheduled_transaction` writes what the SX editor writes:

1. Ensure the template commodity row exists (insert exactly the
   default-data row above if absent; raw SQL + `_verify_write`).
2. Template account: name = the SX GUID string, type BANK, commodity =
   the template commodity, parent = `root_template`. (Today: name =
   SX name, commodity = book default. Aligning costs nothing and
   removes a name-collision surface.)
3. One template Transaction on that account: currency = the SX's
   instantiation currency (the `currency` slot's commodity, else book
   default), description = the SX description, notes = the SX notes,
   post_date = start date. One Split per recipe leg, all on the
   template account, `value = quantity = 0`, memo and action from the
   recipe, and the six slots of §2.1: `account` = target GUID (we
   already store GUIDs since #176); a positive recipe amount is the
   debit side, a negative one the credit side; the numeric is the
   quantized `gnc_numeric` at the target commodity's fraction; the
   formula is the same number as a plain decimal string; the unused
   side is `""` / `0/1`. GnuCash prefers the numeric whenever it is
   non-zero, so the formula's decimal separator (C locale) never
   reaches a locale-sensitive parser in the normal path.
4. The `description`, `notes`, and `currency` slots on the SX row are
   no longer written; `splits-json` is no longer written.

Reads (`_get_sx_splits` becomes `_sx_recipe`): if the template
account has transactions, build the recipe from them — target GUID
from `sched-xaction/account`, amount = debit − credit numeric, memo
and action from the split, description/notes from the template
transaction, currency from its currency. Else fall back to
`splits-json` and the three SX slots exactly as today. One chokepoint;
every reader (list verbose, upcoming verbose, instantiate, delete's
audit snapshot, `_upcoming_within_days`) goes through it.

### 3.2 Cross-commodity legs

GnuCash has no fixed-quantity concept for a template split; it asks
for the exchange rate at Since-Last-Run. This server replays a stored
quantity (`quantity` per the shared split contract). Keep that: the
quantity is written as `gnc-mcp/quantity` (numeric) on the template
split, read by our instantiation only. Desktop ignores it and asks
for a rate, which is its normal behaviour for such a schedule. The
namespaced key follows the slot-naming rule in CLAUDE.md: no
reasonable developer arrives at this key independently.

### 3.3 Instantiation

`create_transaction_from_scheduled` phase 2 is unchanged (it takes
the recipe from the chokepoint), plus the created transaction gets
`from-sched-xaction` = the SX GUID so desktop knows where it came
from. piecash's `SlotGUID._mapping_name_class` already knows that
key (`ScheduledTransaction`), so `txn["from-sched-xaction"] = sx`
works through the ORM.

Desktop-created schedules become instantiable here for the common
case (numeric amounts, no variables). A template whose formula
carries variables and has no non-zero numeric (GnuCash prompts for
those) is refused with a message naming the formula and saying to run
it from GnuCash. An exchange-rate variable is the one variable we can
answer: if the only missing binding is `"<split> -> <txn>"`, resolve
it from `book.prices` through `_rates_as_of` at the instance date, as
`pay_document` does for cross-currency documents.

### 3.4 Migration

Existing schedules (every sample book; the maintainer's seven) hold
`splits-json`. Lazily, on the first WRITE that touches a schedule
(`update_scheduled_transaction`, phase 3 of instantiation, or a new
`migrate` pass inside `create_scheduled_transaction` for its
siblings — decide at implementation; the first two are enough), write
the native template from the recipe, then delete `splits-json` and
the three SX slots, in one session. Reads never migrate. The write
response carries `template_migrated: true` and the audit line says
so, per the rule from the budget stamp: a storage change is never
silent in the log.

Template account rename/commodity change is NOT applied to existing
schedules (a rename changes nothing GnuCash reads; leave them).

### 3.5 Delete

`delete_scheduled_transaction` already deletes desktop recipe rows
before the template account (C9). It now also deletes the
`sched-xaction` slot frames of those splits explicitly, raw SQL +
`_verify_delete`, rather than trusting the split cascade (the
credit-note incident: a GUID-valued slot cascade can reach the
referenced entity).

### 3.6 What stays filtered

Template transactions are real rows. `_template_account_guids`
already keeps them out of `list_transactions`, `search_transactions`,
the dashboard counts, `_collect_create_signals` (duplicate detection),
and reports. Add a source-grep lock that every `book.transactions` /
`session.query(Transaction)` iteration in `book/*.py` either goes
through `_query_filtered_splits` or references
`_template_account_guids` within 20 lines. The template commodity
stays filtered from `list_commodities` and stale-price warnings (in
place since C9).

## 4. Blast radius

| area | change |
|---|---|
| `book/scheduling.py` create | template commodity ensure, account alignment, template txn + split slots (raw SQL for the six slots, `_verify_composite_write` each) |
| `book/scheduling.py` read | `_sx_recipe` chokepoint, native first, `splits-json` fallback |
| `book/scheduling.py` instantiate | `from-sched-xaction` on the created txn; variable refusal; FX variable from prices |
| `book/scheduling.py` update / phase 3 | lazy migration |
| `book/scheduling.py` delete | explicit slot-frame delete |
| `logging_config.py` | CREATE / UPDATE / CREATE_FROM_SCHEDULED render `template_migrated` |
| `book/core.py` | no change expected; lock the template filter |
| `tools/scheduling.py` | docstrings only |
| tests | see §6 |

Medium. No report numbers shift (recipes are the same numbers in a
different container); the sample oracles change only when a
continuation run migrates their schedules, which is a deliberate,
capture-rig-invalidating event per the release checklist and should
be scheduled, not stumbled into.

## 5. Acceptance gate

This branch writes template rows GnuCash reads, so the gate is
GnuCash, not the suite:

1. On a scratch copy of Alex, create a schedule here, open the copy in
   GnuCash 5.12: it opens with no complaint, the schedule shows its
   splits in the SX editor's template ledger with the right accounts
   and amounts.
2. Set the schedule's start date into the past, reopen, click OK on
   Since-Last-Run: GnuCash creates the transaction with the right
   splits and description, and `list_scheduled_transactions` here
   shows `last_occur` advanced with the transaction present. Then
   confirm this server refuses to re-enter that date (backfill guard)
   — the guard's message, which already names desktop as a suspect,
   is finally true.
3. In GnuCash, create a schedule the normal way; here,
   `create_transaction_from_scheduled` posts it with the right splits
   and the audit CREATE line shows it.
4. A cross-commodity schedule created here: desktop asks for the rate
   at Since-Last-Run (its normal behaviour); instantiation here
   replays the stored quantity.
5. The lazy migration on one of the maintainer's real schedules, with
   a `create_backup` first: the write response and audit line say
   migrated; GnuCash then shows the recipe in the editor.

Steps 1, 2, and 5 each end with GnuCash desktop opening the book.

## 6. Test plan (unit)

- Round trip: create → the template account carries one transaction
  with N splits, each split's six slots exactly as §2.1 (raw SQL
  read), numeric at the target fraction, formula the decimal string,
  unused side `""` and `0/1`.
- Native-first read: a schedule built by writing the native rows by
  hand (the shape a desktop book has) instantiates with the right
  splits; `list_scheduled_transactions` verbose shows the recipe.
- Fallback read: a `splits-json`-only schedule still lists and
  instantiates; nothing is written on read (raw row count unchanged).
- Migration: first `update_scheduled_transaction` on a legacy
  schedule writes the native rows and deletes the three slots +
  `splits-json`; response and audit carry `template_migrated`; a
  second write does not migrate again.
- Variables: a template with `credit-formula` `"x*2"` and zero
  numeric is refused, naming the formula; one whose only variable is
  `"USD -> CNY"` instantiates using the rate on file.
- Template commodity: created once, exact default-data row; not
  re-created; filtered from `list_commodities`.
- Filters: source-grep lock (§3.6); `create_transactions` duplicate
  detection ignores template rows (a template with the same
  description/amount as a real entry does not flag it).
- Delete: recipe rows, their slot frames, and the template account
  are gone; the target accounts are untouched (the cascade trap).
- Contract: every raw-SQL DML site has its `_verify_*` within 40
  lines (`TestWriteVerificationCoverage` catches it).

## 7. Out of scope, deliberately

- Recurrence shapes this module does not model (daily, semiannual,
  end-of-month, nth-weekday, composite) — S-4 in the scheduling
  review; a separate ruling.
- Editing a recipe in place (`update_scheduled_transaction` still
  changes enabled/end_date/notes only). Native storage makes an
  amount edit a two-slot write; it becomes cheap after this lands.
- `auto_create` / `adv_creation` semantics.

## 8. Open questions for the maintainer

1. Migration trigger: lazy-on-write only (this spec), or also a
   one-time pass on server start for the configured book? Lazy is
   quieter and auditable per schedule; the start pass would migrate
   all seven of yours in one bounce.
2. Should the sample books be regenerated after this lands so the
   frozen demos carry native templates? The generators would need the
   new create path (they already call it), and a regeneration is the
   capture-rig-invalidating event the checklist reserves for
   deliberate moments.

## 9. As implemented (2026-09-10)

- Slot writes and reads are raw SQL, not piecash's `entity[key]`
  accessors: loading a `SlotGUID` into the session arms its
  delete-orphan cascade (see below), and the polymorphic Slot ORM is
  unsafe to query. Each write has its `_verify_composite_write`.
- **The cascade landmine, generalized.** piecash's `SlotGUID` inherits
  `SlotFrame.slots`, joined on `obj_guid == guid_val` with
  `delete-orphan`; for a GUID slot that is every slot of the
  REFERENCED entity. ORM-deleting a template split would sweep the
  target account's slots; deleting a stamped instance would sweep
  the schedule's. New base chokepoint `_strip_guid_slots` deletes
  GUID and frame rows by raw SQL and expires the owners' `slots`
  collections before any ORM delete; called from
  `delete_transaction` (both sites) and `delete_scheduled_transaction`.
  Locked by a test that puts slots on the target accounts, deletes
  the schedule, and reads them back; watched failing under mutation.
- Recipe split order: the splits table has no sequence column, so
  legs come back debits first, then by account path (stable on
  PostgreSQL too, which has no rowid).
- Amounts read from numerics are quantized at the numeric's
  denominator (4250/100 → 42.50).
- `list_scheduled_transactions` verbose carries `recipe`
  (`native` / `legacy`) and `problems`.
- Migration is lazy on `update_scheduled_transaction` and on
  instantiation (phase 3); no start-up pass (open question 1 left
  at "lazy"). Notes edits land on the template transaction.
- The §3.6 source-grep lock was not added: the remaining
  `book.transactions` iterations are GUID-prefix maps, where a
  template row is harmless. Duplicate detection is locked by test.
