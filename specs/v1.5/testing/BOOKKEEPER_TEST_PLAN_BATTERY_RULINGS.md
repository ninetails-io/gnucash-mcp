# Bookkeeper live loop — battery rulings (feat/battery-rulings)

Six checks, two bounces, ~10 minutes. Branch under test:
`feat/battery-rulings` (rulings 6, 4(b), and the ruling-1 sunset).
Order matters — the disarm state is consumed by the first
`switch_book`, so run the steps as numbered. All successful writes
go to demo books only; the write attempted while disarmed never
executes, so it is safe regardless of the active book. With only
ONE configured book, stop after step 1 and report that — steps 2–5
only exist with 2+ books.

1. **Startup notice, once.** Immediately after the bounce, call any
   read (`get_book_summary`). The FIRST result must open with a
   `⚠ Server (re)started` line naming the active book and saying
   writes are disarmed until `switch_book` confirms. A second read
   must be clean. Judge the copy as its editor: does it read as an
   instruction the model naturally acts on in its opening turn?
2. **Disarmed write refuses clean.** Before any `switch_book`,
   attempt a small write (`create_price` on a demo commodity is a
   good probe). Expect `error_type: active_book_unconfirmed`, the
   message naming the active book, `switch_book`, and the available
   books — and verify nothing happened: no audit entry, and no
   rate-limit consumption (an immediate retry after arming should
   not be throttled).
3. **Failed switch does not arm.** `switch_book` to a nonsense name
   → error listing available books. Retry the step-2 write → still
   refused.
4. **Real switch arms + book stamp.** `switch_book` to a demo book
   (alex). Expect the CONTEXT RESET banner. The probe write now
   succeeds AND the response carries `"book": "alex.gnucash"`.
   Clean up the probe. Error responses should NOT carry the stamp —
   if convenient, provoke one and check.
5. **No-op switch confirms.** Second bounce. First call:
   `switch_book` to the already-active book → expect
   `Already on: …`, then a probe write succeeds. The no-op is a
   confirmation; this step exists to prove that branch.
6. **The two business refusals, on Sabine.** `switch_book` to
   sabine.
   - **4(b):** `pay_document` with `apply_discount=true`,
     `dry_run=true` on an eligible invoice (create a quick
     billterm-with-discount + posted invoice if needed). With no
     `discount_account` passed and none designated, expect a
     refusal naming locale `'de'` and directing to pass
     `discount_account`. Then pass one explicitly (any German
     expense/income leaf) and confirm the dry-run proceeds.
   - **Ruling 1:** attempt to post a document to an A/R//A/P whose
     commodity differs from the document currency. Expect a
     `validation_error` refusal naming the per-currency fix — and
     verify it landed in VALIDATION: the document is still open,
     unposted, and immediately postable to the matched-commodity
     account. No half-posted state, no transaction, no lot.

**Report:** pass/fail per step with actual response text where it
deviates; the standing question — anything routed around; and an
editorial verdict on the two pieces of commissioned copy (notice
and refusal), acceptance bar: "if a user notices the confirmation
happening, the copy failed."


---

## BOOKKEEPER REPORT — loop executed 2026-09-01

- **1. Startup notice: copy PASS; once-latch PASS per process — defeated by the host's twin-spawn** (both trees 13:57:27; my two reads served by different twins, each firing honestly). Silver lining: per-twin disarm means the unconfirmed twin REFUSES writes — ruling 6 armors the twin-routing hazard. Suggest: latch + arm state via shared sidecar marker so the pair acts as one. EDITORIAL STANDOUT: when the first post-restart call was itself `switch_book`, the notice downgraded to the ℹ form with the coaching omitted — the warning erased itself at the moment it became moot. Bar met: the confirmation happened inside an action already being taken.
- **2. PASS.** Disarmed write → `active_book_unconfirmed`, names book + coaching + available books. Audit log: zero entries. No token consumed (armed retry unthrottled).
- **3. PASS.** Nonsense switch errors listing books; retry still refused.
- **4. PASS.** Real switch arms; success carries `"book":` stamp; provoked error carries none; cleanup delete stamped.
- **5. PASS.** Post-bounce no-op (`Already on: books.gnucash`) armed; probe write (current NAV, then deleted — zero residue) succeeded with stamp.
- **6. Ruling 1: PASS.** USD→EUR-A/R post refused in VALIDATION with the mechanism + per-currency fix; document remained open, entries intact, immediately posted to 1407 USD A/R. No half-state.
- **6. Ruling 4(b): FAIL.** `pay_document apply_discount=true dry_run=true`, no `discount_account`, none designated, on Sabine (SKR03): dry-run PROCEEDED and proposed `would_create_accounts: ["Aufwendungen 2/4:Sales Discounts"]` — the English, expense-sided default in a German chart. Locale gate did not classify 'de'. Hypothesis for the repro: DATEV class-numbered top-levels ("Aufwendungen 2/4", "Erlöse u. Erträge 2/8") defeat exact-name locale inference. Repro artifacts left labeled: billterm "2/10 Net 30 (R6 test)", invoice 000014 (EUR, posted, discount-eligible), invoice 000015 (USD, posted to 1407).
- Routed around: nothing.
- **Verdict: rulings 6 and 1 signed off. 4(b) returns for one fix + one re-probe** (the dry-run call above is the 30-second re-test).
