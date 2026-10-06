# Scoped review of the 1.5 close-out (2026-10-05)

Three Opus readers over the diff `develop` took in #200
(`d42400e..e99d46e`), one dimension each, every finding reproduced
before it was reported or marked PLAUSIBLE with the reason:

- `SCOPED_REVIEW_2026-10-05_STORAGE.md` — storage, connections,
  concurrency, data safety (S-1 to S-10).
- `SCOPED_REVIEW_2026-10-05_MONEY.md` — money math and reports
  (M-1 to M-7).
- `SCOPED_REVIEW_2026-10-05_INPUT.md` — input validation, business
  logic, desktop parity (I-1 to I-15).

Totals: 6 SERIOUS, 21 MINOR, 5 NIT; no BLOCKER. Fixed on
`fix/1.5.0-scoped-review`; tests in
`tests/test_review_remaining.py` (`TestScopedReviewStorage`,
`TestScopedReviewMoney`, `TestScopedReviewInput`,
`TestThePreUpgradeMarkerNamesTheSnapshot`) unless noted.

## Resolution

| Item | Grade | Disposition |
|---|---|---|
| S-1 pid probe kills on Windows | SERIOUS | Fixed, `767e3c9`. One `_pid_alive` (`_format.py`), `OpenProcess` on Windows; the `gnclock` holder note uses it too; grep-locked. |
| S-2 trading refusal blocks every schedule | SERIOUS | Fixed, `767e3c9`. The refusal is piecash's own trigger, a quantity imbalance. |
| S-3 pre-conversion copy prunable | SERIOUS | Fixed, `767e3c9`. The auto-backup's copy is hard-linked under the manual label. |
| S-4 / M-6 marker names a withdrawn file | MINOR | Fixed, `767e3c9`: the marker reads `snapshot: none (nothing to convert)`. |
| S-5 label dropped on an unchanged book | MINOR | Fixed, `4e536e0`: the note names the label that was not applied. The skip itself stands (bookkeeper concurrence). |
| S-6 shared intent file, fixed temp name | MINOR | Fixed, `767e3c9`: one intent per pid; `mkstemp`. |
| S-7 / I-8 void and unvoid refused in a trading book | MINOR | Fixed with S-2. |
| S-8 dispose skipped if close raises | NIT | Fixed, `767e3c9`. |
| S-9 a `?` in the book path | NIT | Open; pre-existing. Listed under Known limitations. |
| S-10 startup check waits on a lock | NIT | Fixed, `767e3c9`: a one-second timeout. |
| M-1 dashboard budget line at one rate | SERIOUS | Fixed, `4e536e0`; agreement test with the report. |
| M-2 flow lines don't add up | MINOR | Fixed, `4e536e0`: the (category, month) cell is the rounding grain everywhere. |
| M-3 lot gain rounded three ways | MINOR | Fixed, `4e536e0`. |
| M-4 fine desktop schedule amount refused | MINOR | Fixed, `4e536e0`. |
| M-5 far-zone transaction dates | MINOR | Fixed, `4e536e0`: transactions bind through `_neutral_time`. |
| M-7 first budget keeps a snapshot | MINOR | Fixed, `767e3c9`: a stamp alone is not a conversion. |
| I-1 update and statement skip the text gate | SERIOUS | Fixed, `4e536e0`. |
| I-2 payment wording counted as a Num | SERIOUS | Fixed, `4e536e0`: `_num_bearing_actions`, keyed on transaction type and account type. |
| I-3 claim annotates a posting record | MINOR | Fixed, `4e536e0`: `_refuse_posting_record` on the claim. |
| I-4 free text elsewhere unguarded | MINOR | Fixed, `4e536e0`: `_check_control_chars` behind every writer; parametrized test over twelve fields. |
| I-5 / I-6 lookalike skeleton | MINOR | Fixed, `4e536e0`: joiners kept where they draw, blanks that are not spaces dropped. |
| I-7 `move_account` lookalike | MINOR | Fixed, `4e536e0`. |
| I-9 forced void unmarked in the audit | MINOR | Fixed, `4e536e0`. |
| I-10 unpost note wording | MINOR | Fixed, `4e536e0`. |
| I-11 FC-20 message's account types | MINOR | Fixed, `4e536e0`. |
| I-12 `list_accounts` hidden | MINOR | Fixed, `4e536e0`: `[HIDDEN]`. |
| I-13 hidden not inherited | MINOR | Fixed, `4e536e0`: `_is_hidden` walks the parents. |
| I-14 audit `None → True` | NIT | Fixed, `4e536e0`. |
| I-15 fixture's Opening Balances slot | NIT | Fixed, `4e536e0`. |

Verification at `4e536e0`: full suite 3287 passed, 36 skipped;
PostgreSQL gate 90 passed; MariaDB gate 91 passed; every reviewer's
reproduction script re-run against the fixed code.
