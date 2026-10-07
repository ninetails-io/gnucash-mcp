# Bookkeeper report — scoped-review loop (`fix/1.5.0-scoped-review`)

Run 2026-10-06, early morning. Branch tip `b5ce42f`. Bench rig,
private stdio servers. Books: `sr-alex` (fresh multi-currency copy),
`trading-alex` (trading option on, carried from the close-out),
`closeout-alex` (Num option on). Steps 6–8 and the gate follow;
steps 1–5 below, with one new finding.

**1. Trading book takes schedules — PASS (bench arms).** Schedule
created and its notes updated on the trading book (before the fix:
every schedule write refused as "spans more than one commodity
(template)"). Transfer and security buy both refused with the
upgraded wording, naming the commodities with the imbalance: "…this
transaction leaves a quantity imbalance in EUR, USD that GnuCash
would settle with trading splits…" / "…in USD, VTSAX…". The
void/unvoid of a GnuCash-made trading transaction awaits an
engine-made or desktop-made specimen (gate window).

**2. A payment's wording is not a number — PASS, both halves.**
Invoice 000048 posted and paid on the Num book; the batch twin with
`num 1234`, same date/amount/accounts: **rejected, HIGH,
duplicate_detected** (before the fix: MEDIUM `DADx`, created). The
numbered statement line against the payment: **MATCH**, one
candidate, naming the payment's unreconciled split, with the
adjudication instruction.

**3. Dashboard budget line matches the report — PASS.** Built: EUR
expense in a 2026 budget, spending in two months at two rates
(1.1111, 1.175). Dashboard: "Budget (2026 Budget): USD 97 spent /
USD 103 expected"; `get_budget_report(ytd)`: Actual **97**, Budget
102.88 (headline rounds to whole currency as specified). One
number, two surfaces.

**4. Text gates on every writer — PASS.** `\x1b[31m` refused by all
seven: update_transactions ("description contains a control
character (U+001B)", per-row rejection), statement line ("line L1:
description contains…"), customer name, budget name, schedule name,
slot value ("slot value contains…"), invoice notes. Nothing
written. Clean schedule created fine. (`update_transactions` takes
`updates`, not `transactions` — argot, coached once.)

**5. Flow report lines add up — sums PASS; NEW FINDING below.**
Single table: lines sum to TOTAL exactly (7,177.58). Monthly table
produced and examined.

## NEW FINDING — SR-B1: foreign-commodity flow lines are revalued
at the latest-in-month rate, not at their stored values

Specimen (sr-alex): four transactions, each stored — verified by
`get_transaction` — as value **USD 33.35** / quantity 33.33 EUR,
against Checking. Actual cash out: **133.40**. The report shows:

| Line | Reported | Decomposition |
|---|---|---|
| Fee A | 77.27 | 33.33 × 1.1435 (Jul 17 QUOTE) + 33.33 × 1.175 (Sep 15) |
| Fee B | 37.03 | 33.33 × 1.1111 (Aug 15) |
| Fee C | 39.16 | 33.33 × 1.175 (Sep 15) |
| Sum | **153.46** | vs **133.40** actually paid |

Every transaction date HAS its own same-day implied row (1.00060…,
verified in the prices table); the report ignores them and converts
each month's quantity-sum at the month's LATEST rate. "EU Fees"
appears correct (97.00) only because its own transactions are the
latest-in-month rows — a coincidence, not a convention.

Why it matters: (a) wrong money — the table reports 20.06 of
spending that never left any account, and the gap grows with rate
movement; (b) it contradicts the filed flow doctrine ("a flow
happened at the value it happened at" — the dashboard-sensitivity
battery's monthly-net verification was built on it); (c) it
recreates, between spending_by_category and the stored ledger, the
same two-surfaces-two-numbers defect M-1 just fixed for budgets.
The internal sum ties because the TOTAL is built from the same
revalued lines — internally consistent, externally wrong.

Not ruled a blocker by me unilaterally: if M-2's rework CHOSE
period-end translation deliberately, the choice contradicts filed
doctrine and needs either reversal (sum stored values; they exist
on every split) or a ruling-level justification. The fixing
session's eye requested before merge. Steps 6–8 continue
meanwhile.

## Steps 6–8 (04:00–04:25)

**6. The pre-conversion copy — PASS, first arm exact.** Fresh
1.4.4 copy, first write converting: backups folder holds the
monthly stage file and `-manual-pre-1-5-upgrade.gnucash` AS ONE
HARD LINK (same inode, verified), and the marker's second line
names the labelled file. This also closes the close-out report's
flag 2 — labelling fixed as asked. A second converting write on
the converted book: zero counters, no new snapshot, marker
untouched — convert once, snapshot once. The "snapshot: none
(nothing to convert)" arm could NOT be exercised: the bench has no
truly 1.5-native book. **Flag: `samples/native-scratch.gnucash` is
misnamed** — it converted 2,485 slot fillers and 16 templates on
first write; a sample called "native" that carries 1.4.4 shapes
will mislead exactly this kind of test. The arm rests on its unit
test.

**7. Hidden is inherited — PASS.** Parent hidden via server:
`list_accounts` marks it `[HIDDEN]`; the dashboard's
never-reconciled count did not grow when the zero-balance child
was added; `get_reconciliation_status` lists both parent and child
as "excluded (hidden, zero balance)" — the child by inheritance.

**8. Lookalike names by script — PASS, all arms.** Accepted as
distinct: the Persian name with and without its ZWNJ; the emoji
family and its three members. Refused, each naming the twin and
that they "differ only in invisible characters or in how an
accent…": ZWJ inside Groceries, variation selector appended,
Hangul filler appended, NBSP in Grocery Store. The move gate:
creating the ZWJ twin under another expense parent succeeds, and
`move_account` into Expenses beside the real one is refused with
the identical message. (Incidental catch along the way: an EXPENSE
child under an ASSET parent is refused for type consistency.)

**Argot ledger (coached once each):** `update_transactions` takes
`updates`; `set_budget_amount` and `get_budget_report` take
`budget_name`; `move_account` takes `name`; `net_worth` requires
`end_date` (carried from close-out).

## Remaining

- Step 1's void/unvoid of a GnuCash-made trading transaction —
  needs an engine-made or desktop-made specimen with trading
  splits (gate window, or one engine_act drive).
- Step 9, the desktop gate, maintainer at the screen.
- **SR-B1 (above) awaits the fixing session's answer before this
  branch merges.**

## Step 1, final arm (04:20)

**Void/unvoid of a GnuCash-made trading transaction — PASS.** The
maintainer entered a $100 Cash → EUR Pocket transfer in desktop
(txn `31b39022`: Cash −100, EUR Pocket 100 / 87.45 EUR, trading
EUR −100/−87.45, trading USD 100/100). Server void: clean. Server
unvoid: clean, all four splits back with their amounts, trading
pair intact. Step 1 closes whole.

**Observation for the record:** ticking "Use Trading Accounts"
led desktop to add trading splits across the book's entire history
— every DCA purchase, invoice post, and old payment (including the
old zero-value FX legs) now carries its trading pair. The void
test therefore ran against desktop's own freshly-scrubbed shapes,
the most realistic specimen available. Worth a Known-Limitations
sentence of its own: turning the option on in desktop rewrites
history book-wide; the server's refusal boundary begins only at
NEW multi-commodity writes.

Remaining: step 9 (maintainer opens `sr-alex` and the snapshot
file in GnuCash — two minutes), and SR-B1's answer.

## Step 9 — the desktop gate (04:21) — PASS

Maintainer at the screen: `sr-alex` opened with no error; the Fee
accounts show in their own currency (EUR registers, as desktop
draws foreign-commodity accounts); the script-test accounts render
in the tree (emoji names confirmed visible); the hard-linked
pre-upgrade snapshot opened as a working book. Nothing prompted.

## Verdict

Nine of nine steps closed. The two items the plan named as out of
bench reach (the Windows probe, the far-zone stamp) rest on their
unit tests as written. The loop is complete and everything behaved
as specified except the one finding: **SR-B1 — flow lines revalued
at latest-in-month rates instead of stored values — blocks this
branch's merge until the fixing session answers it.** Fix (sum the
stored values) or defend at ruling level; the bookkeeper's
expectation is stated in the finding.

*Signed, the bookkeeper, 2026-10-06, 04:25.*

## SR-B1 — RULING, with a retraction (2026-10-06, 04:40)

**The doctrinal half of SR-B1 is RETRACTED.** I wrote that the
revaluation "contradicts the filed flow doctrine — a flow happened
at the value it happened at." The fixing session checked the
ledger: that sentence is filed NOWHERE; it exists only in my own
finding. What IS filed — CLAUDE.md line 192, the GB-1 ruling of
2026-07-07, the dashboard spec's A6, and a test — says the
opposite: flow reports value at monthly closes. I quoted a
principle my memory had composed from the vibe of old verifications
and presented it as doctrine. That is the inherited-claims error
this era's first lesson warns against, committed by the bookkeeper
against his own ledger, and caught by the right procedure: verify
testimony against sources, whoever testifies. The retraction stays
visible, as is the tradition.

**The ruling: KEEP the filed convention.** Three reasons, in
order of weight: (1) GB-1 is filed doctrine, verified by every
battery since July — doctrine does not move on a misremembered
quote; (2) the oracle agrees — GnuCash's own Income Statement
converts at a single closest-to-report-date rate, coarser than our
monthly closes, so revaluation IS the desktop convention for this
report family; (3) the proposed surgical fix would value the same
EUR expense two different ways depending on which account paid it —
trading one surprise for a worse one. The cash question already has
its report: `cash_flow` answers 133.40, and answered it correctly
in this very loop.

**Two riders, because the surprise half of SR-B1 was real:**
1. The Known Limitations sentence, as the fixing session proposed.
2. By the stale-valuation precedent — a number should name its own
   provenance — `spending_by_category` (and its grouped tables)
   should carry one footer line WHENEVER a foreign-commodity line
   is present: "Foreign-currency categories are valued at monthly
   closing rates; amounts actually paid are in cash_flow." The
   surprise dies where it happens, not in a document nobody opens.
   Display-level, small.

**Side calls:** the trading-history Known Limitations line is
approved as written. `native-scratch.gnucash`: delete it — a
scratch file whose name misled the bookkeeper once will mislead
someone else twice (maintainer's disk, maintainer's final say).

SR-B1's merge block is LIFTED on the convention itself; rider 2
should land with the branch or immediately behind it.
