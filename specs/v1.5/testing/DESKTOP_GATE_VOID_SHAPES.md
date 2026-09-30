# Desktop gate — void in GnuCash's shape (test/slot-shapes)

Branch `test/slot-shapes` at `91ea3eb`, 2026-09-29. Driven by
Claude's desktop control at the screen, storage read back by sqlite.

**Why:** `tests/test_slot_shapes.py` (written first, `6d0f9ec`) found
that `void_transaction` wrote its originals as strings under
`void-former-value` and an invented `void-former-quantity`, with a
`void-time` GnuCash's parser rejects. Desktop had never recognized a
server void. The fix (`91ea3eb`) writes `xaccTransVoid`'s shape and
converts old voids on the next write.

**Book:** `~/Projects/abe-bench/alex-void.gnucash`, a copy of the
round-2 bench book, which carried the sample's one pre-fix void
("Payment to Wrong Vendor (mis-routed)", 2025-03-15, 500.00). A fresh
"Gate void probe" (33.33) was voided from the server; that write
reported `voids_migrated: 1` and rewrote the 2025 void in place.

| step | result |
|---|---|
| Open in GnuCash desktop | clean; the reminders fire; Since Last Run cancelled, nothing posted |
| Find "Gate void probe" | both splits `v`, amounts 0; Transaction menu: Void greyed, **Unvoid enabled**, Delete greyed (read-only) |
| Unvoid it in desktop | 33.33 restored on both splits, state `n` |
| Find "Payment to Wrong Vendor" (converted legacy void) | both splits `v`; **Unvoid enabled** |
| Unvoid it in desktop | 500.00 restored on both splits, state `n` — the amount the string slots held |
| Save, quit; sqlite | `void-*` rows: 0; both transactions `n`, value and quantity at denominator 100; the two `trans-read-only` markers cleared, the 56 invoice postings' kept |

**Verdict: PASS.** Desktop reads a server void as its own and its
Unvoid restores the originals — for a void written today and for one
the converter rewrote.

Route-around: macOS's invisible Emoji & Symbols window blocked
clicks on part of the register; keyboard navigation (Up, ⌃L in the
file chooser) and the global Find from the Accounts tab got around
it. A register-scoped Find only refines the current results.
