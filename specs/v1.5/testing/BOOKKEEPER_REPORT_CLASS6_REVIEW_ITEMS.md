# Bookkeeper report — class-6 review items (fix/class-6-review-items)

Run: 2026-09-26, copies of the three committed demo books; branch at
`b0b81ae` (harness saved afterwards), develop at `6a1f368` (#193)
for every "before". Run by the maintainer's Claude Code session
standing in for the remote bookkeeper: every Part B call went through
the MCP tool layer over stdio (`specs/v1.5/testing/class6_loop/`),
not through book methods.

## Verdict: PASS (Part A 3/3, Part B 25/25, Part C clean). Two fixes landed on the branch during the loop; three notes, none blocking.

## Part A — the oracles did not move
Capture rig, develop worktree vs branch head, fresh copy per side:
Alex 20 files, Lin Wei 20, Sabine 17. The only differing line in each
is `book_path` (separate copies by design). Rerun at `b0b81ae` after
the loop's two fixes: same result.

## Part B — Alex, 25/25
- **Step 0.** Alex has no sold-out lot, so the sell was made: 2 AAPL at
  cost via `create_transactions` with `qty`, assigned into
  `AAPL buy 2026-05-10`; the lot auto-closed.
- **1, delete — PASS.** Unforced: `Transaction has splits in lots: AAPL
  buy 2026-05-10 (Assets:Investments:Brokerage:AAPL). Deleting reopens
  them (cost basis, or an invoice's payment).` The list form adds
  `(nothing deleted)`. The sell survived both.
- **2, void/unvoid — PASS.** Voided: `is_closed: false`, and the
  open-only `list_lots` lists it. Unvoided: closed.
- **3, replace_splits — PASS.** Unforced refused; forced warned
  `Removed splits from lots: AAPL buy 2026-05-10 (…)` and the lot
  reopened. Missing quantity: `Split for
  'Assets:Investments:Brokerage:AAPL' requires 'quantity'…`.
  Imbalance: `Splits do not balance: total is -86.64`.
- **4, forced delete — PASS.** `lot_splits_affected: 1`; lot open at
  2.0000 shares, 586.64 basis.
- **5, invoice 000016 — PASS.** Unforced delete refused naming
  `Invoice 000016 (Assets:Accounts Receivable)`. Voided payment:
  `posted`, 3,500.00 due, back on the outstanding list. Unvoided:
  `paid`.
- **6, bill 000006 — PASS.** Refused, then forced; the bill reads
  `posted`, 450.00 due.
- **7, NULL description — PASS.** Search, a create dry run (which
  still found a real Corner Store near-duplicate, so the sweep read
  the whole book), and the dashboard all answered.
- **8, audit log — PASS after fix 2.** See below.

**Fingerprint.** On develop the same script stops at step 0:
`unexpected_error: AttributeError: 'NoneType' object has no attribute
'lower'` — one NULL description and no transaction can be created.
Without the NULL row, develop's first unforced delete removed the
lot-held sell.

## Fixes that landed during the loop
1. **`0732498` — lot refusals name the lot.** `replace_splits`' refusal
   named only the account while delete named the lot title; an
   invoice payment read as `Assets:Accounts Receivable`, not
   `Invoice 000016`. Four sites now share `_lot_split_names`.
2. **`b0b81ae` — a forced delete says so in the audit log.** The
   renderer ignored `reconciled_splits_affected` and
   `lot_splits_affected`, so the forced delete of the bill payment
   read exactly like an ordinary delete. Now:
   `Forced: 1 split in a lot (the lot reopens)`. Forced deletes past
   the reconciliation gate were silent the same way before this
   branch.

## Part C — GnuCash's own engine
`gnucash-cli` (GnuCash 5.15) ran its Balance Sheet on the looped copy
and on a pristine copy: both loaded, exit 0, the same 108 warnings on
each (entry discount-how strings, from the committed book). The
differences, all explained:
- Checking +450.00, Accounts Payable +450.00 — the forced delete of
  the bill payment, as intended.
- AAPL −1,253.02 — GnuCash priced 31 shares at 293.32, not 333.74.
  The step-0 sell, entered at cost, left a `type='transaction'`,
  `user:split-register` price dated 2026-07-20, and it outlived the
  deleted sell. The server skips those prices by design and still
  says 333.74. Test artifact; see note 1.

The server's `balance_sheet` on the looped copy matches GnuCash to
the cent on Checking (48,190.65), Accounts Payable (900.00), and total
liabilities (411,218.91).

## Routed around
- **`get_document` doesn't name a document's payments.** To void
  invoice 000016's payment I searched by customer and picked the
  3,500 A/R credit dated after posting. The delete refusal naming
  `Invoice 000016` confirmed the pick after the fact. A "void this
  bounced payment" workflow wants the payment GUIDs on the document.
- **`list_documents` shows a paid document as `posted`** (000016 and
  000006) while `get_document` says `paid`. I had to open each
  document to find paid ones.
- **`list_lots` needs an account.** Finding a sold-out lot anywhere
  meant listing accounts first; there were none anyway.

## Notes
1. Deleting a transaction leaves the `type='transaction'` price piecash
   created with it, and GnuCash's reports then value the commodity at
   that price. Whether desktop removes such prices on delete is
   unchecked; not this branch's code.
2. Alex's bill lots are titled `Invoice 000006`. The server now writes
   GnuCash's `Bill 000006` (`_doc_type_string`, from
   `gncInvoiceGetTypeString`); the committed book predates that.
3. Desktop GUI editing was not exercised; Part C loaded the book
   through GnuCash's engine headless. The only storage value this
   branch writes that it didn't before is `lots.is_closed = -1`, which
   is GnuCash's own `LOT_CLOSED_UNKNOWN`.

Signed: Claude (Opus 5.5), Claude Code, for the bookkeeper. Every lot
refusal names its lot, every override shows in the log, and one NULL
no longer stops the book. Ready for a PR.
