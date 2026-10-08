# Bookkeeper report — close-out loop (`fix/1.5.0-numbers`)

Run 2026-10-05 evening. Branch tip `7ca1ba7`. Bench rig, private
stdio servers, `REPO` at the checkout; 1.4.4 served from a git
worktree at tag `v1.4.4` (`434fce2`). Book: `closeout-alex.gnucash`,
scratch copy of the working-tree multi-currency Alex.

## Server-side steps

**1. Lines add up — PASS.** Assets 862,031.84, liabilities
410,768.91, equity 451,262.93: every section's lines sum to its
total to the cent, A = L + E exactly, `net_worth` (451,262.93)
equals A − L, dashboard shows 451,263. Checked by script, not by
eye. (The 644.57 gap does not exist in the server's own sheet —
consistent with its attribution to GnuCash's report valuation.)

**2. Void and unvoid (server half) — PASS.**
- Reconciled split: refused, naming `Assets:Current Assets:Checking
  Account` and `force=true`. With force: voided, warning that the
  reconciled balance no longer matches the cleared statement.
  Unvoided clean.
- Notes round trip: "Umbrella insurance premium, quarterly (Q3)"
  restored exactly after void/unvoid.
- No-notes round trip: notes read "Voided transaction" after —
  GnuCash's own behavior. RULING REAFFIRMED: it stands; parity
  beats tidiness.
- Bonus: the forced void was this copy's first write, so the full
  1.4.4→1.5 conversion rode along and ended `book_marked_converted:
  "1.4.4"` — the FC-20 mark, observed live.

**4. Backups — PASS.** Second call with a DIFFERENT label:
`status: unchanged`, first file's path, no new file. After one
transaction: a new file. Folder additionally held one `monthly`
stage backup (C29's stage check firing on first write) and the
`.pre-1-5-upgrade` marker (see flags).

**7. Interrupted write — PASS on the plan's second arm;
INTERRUPTED line not provoked.** Three kill attempts against 600-
and 5000-row batches: one batch finished first and left its own
audit entry (`600 created, 0 rejected`), the other two died before
the write began and left zero partial rows. The book never held a
fragment of any batch — all-or-nothing from the outside, which is
the stronger property. The `INTERRUPTED` audit line itself could
not be reached by external timing (the write window is too narrow);
it rests on its unit test.

**8. Not a GnuCash book — PASS.** Verbatim: "not-a-book.gnucash is
a SQLite file but not a GnuCash book (it has no versions table).
Pick the file GnuCash saves, in the sqlite3 format." Startup
refusal, no "Unsupported table versions".

**12. An old server's write — PASS.** Real 1.4.4 code (worktree)
created a schedule on the converted book. The next 1.5 schedule
write answered `old_server_write: "A server older than 1.5 has
written to this book since version 1.4.4 converted it…"` plus
`templates_migrated: 1`, and the dashboard warns: "An older server
(1.4.x) wrote to this book after its conversion (seen 2026-10-05):
budget amounts it set on income, liability, or equity accounts may
carry the wrong sign…"

## Flags

1. **`get_server_config` still reports `Version: 1.4.4`** on this
   branch — flagged before 1.5.0 and still unfixed. Bump before any
   tag.
2. The `.pre-1-5-upgrade-closeout-alex` marker in backups/ holds
   only a timestamp matching the `monthly` stage file to within
   50 ms. If the monthly file doubles as the pre-upgrade snapshot,
   fine, but the C30 promise was a LABELLED snapshot — a reader of
   this folder cannot tell which file is the pre-conversion copy.
   Cousin to confirm or fix the labelling.
3. Argot, coached in one round each: `net_worth` requires
   `end_date`; `get_balance` rejects `account`.

## Litter manifest (closeout-alex)

600 "Bulk line" transactions (Cash → Miscellaneous, $1 each),
Closeout coffee (CO2's duplicate was correctly rejected, HIGH),
two schedules ("Old server gym" 1.4.4-written, "New server water"),
Cash deliberately overdrawn −307.50, conversion applied, void
round-trips net zero.

## GUI steps (pending, maintainer at the screen)

2d (GUI void → server unvoid), 3 (hidden), 5+6 (read-only period +
Num column, one invoice serving both), 9 (MariaDB emoji — awaiting
confirmation of WHICH database; nothing touched), 10 (Docker:
daemon status recorded above at run time), 11 (trading-accounts
refusal on a fresh scratch copy).

## Session 2 results (17:30–17:50, maintainer at the screen for GUI halves)

**2d (GUI void → server unvoid) — server half PASS.** The
maintainer voided "Closeout tea" from the register (reason
"Software testing", 17:26); the server unvoided it clean. GUI
restored/editable check in visit 2.

**3 (hidden) — partial, process not product.** Server set hidden;
GUI showed it hidden and Edit Account ticked (maintainer confirms).
The untick did not reach disk (`accounts.hidden` still 1), so the
server truthfully still reports hidden — redo in visit 2 and
re-read.

**5 (read-only period) — server half PASS.** Invoice 000047 posted
and paid 2026-08-26 (40 days back): both responses carry
`read_only_period` naming the date and the book's read-only date
2026-09-05. Register check in visit 2.

**9 (MariaDB emoji) — PASS.** Verbatim: "This book's
`transactions` table is utf8mb3 (as GnuCash desktop creates it)
and cannot store the character '🍩' (U+1F369). Remove it and
retry; nothing was written." Search confirms zero rows. Bonus
sightings: the startup notice masks the password in the mysql URI
(`gnucash:***@…`), and an earlier attempt met desktop's lock with
the clean `lock_error` naming host and PID — the locking etiquette
live, unscripted.

**10 (Docker) — PASS.** Image builds (552MB); `id` in-container is
uid 10001(gnucash), not root; a piped MCP session wrote "Docker
demo write" to the mounted demo book and the audit entry landed on
the volume. Incidental good refusal: setting both
`GNUCASH_BOOK_PATH` and `GNUCASH_BOOK_URI` is refused with a clear
mutual-exclusion message (the image ships a default BOOK_PATH —
worth a README line).

Remaining: visit-2 GUI verifications (tea restored, 000047
read-only in register, Num column, hidden untick redo) and step 11
(trading-accounts tick, then server refusal check).

## Final results (17:50–17:58)

- **2d GUI half — PASS.** "Closeout tea" restored, editable;
  notes still read "Voided transaction" post-unvoid (the no-notes
  case, GnuCash-faithful, as ruled).
- **3 — PASS.** Untick redone and saved; `get_account` now returns
  the account with no hidden field. The server reported the disk
  truthfully at every point; the earlier miss was the GUI edit not
  landing, not a server defect.
- **5 GUI half — PASS.** 000047's posting shows read-only in the
  register; editing refused.
- **6 — PASS.** Maintainer verbatim: "Ref is 000047. TRef is
  'Invoice'" — the invoice ID on the posting line with the option
  on, matching the engine twin's rows.
- **11 — PASS.** Trading-accounts book (`Use Trading Accounts =
  t`): the USD→EUR entry refused verbatim — "This book uses
  trading accounts, and this transaction spans more than one
  commodity (EUR, USD). The server does not yet write the trading
  splits GnuCash writes for it, so the write is refused; nothing
  changed. Enter it in GnuCash desktop. Same-currency entries are
  unaffected." — and the same-currency expense was created. The
  round-2 ruling, implemented to the letter. (Also collected:
  create_account teaches leaf-name + parent when given a ':' path.)

## Verdict

**Twelve of twelve steps closed; the close-out loop is CLEARED,
on two conditions:** the `Version:` string (still 1.4.4) is bumped
before anything tags, and the cousin answers the pre-upgrade
snapshot labelling flag (or amends C30's promise to match what the
marker-plus-monthly-file actually provides). The Docker default
`GNUCASH_BOOK_PATH` deserves one README line. Everything else
observed today behaved as specified, several refusals taught their
own cures, and two mechanisms demonstrated themselves unscripted:
the lock etiquette (twice, naming host and PID) and the duplicate
rig (flagging the bookkeeper's own litter).

*Signed, the bookkeeper, 2026-10-05.*

## Addendum — step 11, the stock-purchase case (18:35)

The fixing session flagged that on a trading-accounts book an
ordinary stock purchase is also cross-commodity, so the refusal
covers it — the case most likely to surprise a user. Specimen now
on record: a VTSAX buy on `trading-alex` refused with the same
message, naming "(USD, VTSAX)", nothing written. Correct per the
ruling: GnuCash writes trading splits for security buys too, so
writing one without them is the same wrong-rows defect.

**Rider:** the Known Limitations entry must name the common case
in plain words — "in a book with Use Trading Accounts on, stock
and fund purchases (not just currency transfers) must be entered
in GnuCash desktop" — so a user meets this boundary in the
documentation before they meet it in a refusal. No code change;
the refusal already names the ticker.
