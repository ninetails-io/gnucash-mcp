# Bookkeeper Test Report — GB-1 + v1.4 review fixes

**In response to:** `BOOKKEEPER_TEST_PLAN_GB1.md`
**Branch under test:** `feat/gb1-per-period-rates` (editable install; liveness proven behaviorally via B1)
**Tested:** 2026-07-08 → 09, live MCP server, Claude Fable 5 as bookkeeper
**Books:** /tmp copies of alex / linwei / sabine, multi-book config, debug on, `GNUCASH_LOG_DIR` set

## Verdict

**Sign-off gate met: Tracks A and B fully green.** Every check was
executed twice — an initial full pass, then a checkpointed clean
re-run after a mid-session network incident (see F7) — and every
figure matched digit-for-digit across both runs. GB-1's core
invariant (grand total identical across single-period and all
`group_by` modes) held in every probe. No data loss, no drift.

## Results

| Check | Result | Notes |
|-------|--------|-------|
| A1 | PASS | alex spending TOTAL 63,290.64 — byte-exact, both runs |
| A2 | PASS | linwei spending TOTAL 192,293.59 — byte-exact |
| A3 | PASS | sabine spending TOTAL 29,967.70 — byte-exact |
| A4 | PASS | alex cash flow 109,784.79 / 47,365.88 — byte-exact, both runs |
| A5 | PASS | linwei cash flow 205,734.30 / 201,556.36 — byte-exact |
| A6 | PASS | sabine cash flow 59,689.33 / 39,499.49 — byte-exact |
| A7 | PASS* | No suspicious warnings; formal close needs a captured v1.4.0 baseline. Sabine's `Ausgleichskonto-EUR` suspense warning appears to be *correct* strict-matcher output — confirm it is not new |
| B1 | PASS | EU Travel = 325.00 (100×1.05 + 200×1.10). Clean run: exact on first read. See F3/F4 for the price-collision prerequisite |
| B2 | PASS | Grand TOTAL 28,971.30 identical across single / month / quarter / year |
| B3 | PASS | EU Travel monthly columns 105.00 / 220.00 / 0.00 |
| B4 | PASS | balance_sheet(2026-03-31) values EUR Wallet at −300 EUR @ 1.20 = −360.00 — as-of semantics deliberately preserved |
| C1 | PASS | Ragged start → `2026-01*` header + partial-coverage footnote. Bonus: `group_by="year"` on a Q1 range correctly stars `2026*` |
| C2 | PASS | Calendar-aligned dates → no markers anywhere |
| D1 | PASS | `switch_book("lin")` → CONTEXT RESET banner naming previous book + orientation line; repeat with full filename → bare `Already on:` |
| D2 | PASS | Unique prefix resolves silently; `switch_book("zzz")` → clean validation error listing all books |
| D3 | PASS | Per-book `{log_dir}/{book}.gnucash.mcp/audit|debug|backups` trees confirmed on disk for all three books; audit headers written on activation; write entries accurate and ordered; **failed writes are audited as ERROR lines**; DELETE PRICE entries preserve the destroyed value |
| D4 | PASS | Top-level BANK `Açık Hesap` → no suspense/imbalance warning, runway honest. Bonus: creation response warns about root-level placement |
| D5 | PASS | Parent `is_retirement="true"` excludes child balance from runway liquid (−1,000.00 exactly); child `"false"` overrides parent and restores it exactly. Verified end-to-end in both runs |
| D6 | SKIP | Per plan — unit/oracle coverage deemed sufficient |

## Findings (fixable follow-ups; none met the blocking bar)

**F1 — `switch_book` is invisible in logs.** Priority fix.
Switches appear only as anonymous `Book opened (readonly=True)`
lines in the *target* book's debug log — no `MCP request:
tool=switch_book` entry, no audit entry, nothing in the departed
book. The active-book change is the single event a multi-book
audit trail most needs. Recommend an explicit entry in both
books' audit and debug logs ("switched away → X" / "switched in
← Y"). This gap turned a five-minute question into an hour of
forensics during this loop.

**F2 — Write-mode open precedes validation.** A `set_account_slot`
call that failed on account resolution still opened the book
readonly=False, which triggered a full monthly backup (3.4 MB)
for a no-op. Backup-before-mutation is the right ordering; the
fix is validate-then-open. Mitigating credit: the failed write
*was* recorded in the audit log as an ERROR line, which is
excellent.

**F3 — Undocumented same-date price tie-break.** With two prices
on the same commodity/currency/date (differing source), the
month-close resolver silently preferred `user:market_data` /
type `last` over `user:price` / type `nav`. Observed:
100×1.1919 + 200×1.1805 = 355.29 where the plan expected 325.00.
Whatever the rule is (source priority? type? insertion order?),
document it — or define it deliberately.

**F4 — Test plan premise ("alex has EUR already").** Having the
EUR commodity means having EUR *price history*: alex ships with
`user:market_data` prices on the exact month-end dates Track B
plants. B1's expected 325.00 requires deleting the three
colliding month-end market prices first. Amend Track B step 3 to
begin with that deletion (validated order in the re-run), or
strip those prices from the sample fixture.

**F5 — Mixed timestamp conventions.** Backup filenames are UTC
(`20260709T0551...` for a 22:51 PDT event) while audit/debug
day-files are local-dated (`2026-07-08.txt`). Confusing near
midnight; pick one, or label both.

**F6 — Client multi-spawns the server.** Claude Desktop started
2–3 `gnucash-mcp` processes within seconds on each relaunch
(observed 22:47 ×2, 23:47 ×3 in debug logs). Not a server bug,
but multiple live processes on the same SQLite books is a lock-
contention risk. Worth a README note; the open-per-request
design limits the exposure window.

**F7 — Methodology: turn loss leaves unrecorded server effects.**
A client network timeout mid-turn, followed by the user's "Try
Again," erased the conversation record of tool calls that had
already executed server-side. Result: the session resumed with
the active book silently different from the bookkeeper's model
(the erased turn had legitimately called switch_book). No data
damage occurred — duplicate detection and idempotent slot writes
contained the replay risk — but the failure mode is real.
Codified mitigations, field-tested in the re-run:
1. After any network error or retried turn, re-verify
   `get_server_config` + `get_book_summary` before trusting state.
2. In write-heavy sessions, checkpoint with `create_backup`
   (manual stage) before each write phase, and end the client
   turn at each checkpoint so completed work is committed to the
   conversation record.
Worst case under this protocol was a one-command rollback.

## Reads-oddly ledger (passing, but noted per plan §Reporting)

- Existing config in the wild had a path-typo book entry
  (`Users/...`, no leading slash) that the server nonetheless
  loaded — path normalization is more forgiving than expected;
  confirm that is intentional.
- Zero-total categories are dropped from `group_by` tables
  entirely rather than shown as 0.00 rows (e.g. Education
  vanishes when its only activity predates a ragged range
  start). Consistent, but a reader comparing two tables may
  miss it.
- A failed write consumed the monthly-backup trigger (see F2) —
  content was identical to pre-state, so harmless, but surprising.
- Date-relative summary fields (runway, days-behind, overdue
  counters) legitimately shift across local midnight mid-session;
  future loops comparing summaries should anchor on
  date-independent fields (liquid total, transaction count).

## Verification evidence

- Initial run: full plan order, all tracks, 2026-07-08 22:47–23:24 PDT.
- Incident: turn loss at ~23:18–23:20; reconstructed from per-book
  debug logs (D3 infrastructure made this possible — the audit
  trail investigating its own test session).
- Clean re-run: pristine alex re-copy verified by byte size +
  live transaction count; checkpoints CP1–CP3 as manual backups
  (`rerun-CP*` labels, integrity-checked); every figure matched
  the initial run digit-for-digit, confirming report determinism
  independent of fixture construction order.
- linwei.gnucash and sabine.gnucash mtimes confirm zero writes to
  either book across the entire session (one failed, audited
  write attempt on linwei changed nothing).

## Recommendation

Open the PR from `feat/gb1-per-period-rates` into develop. Fix
F1 before this branch runs against a real book — silent book
switches and an audit trail that cannot see them is the one
combination this loop demonstrated to be genuinely dangerous.
F2–F5 are ordinary follow-ups; F7's protocol belongs in the next
revision of the bookkeeper test plan template.

— Fable, 2026-07-09
