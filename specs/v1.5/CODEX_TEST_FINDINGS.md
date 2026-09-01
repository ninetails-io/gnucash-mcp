# Cross-model test findings — ChatGPT Codex battery, 2026-08-05

First deliberate foreign-model bookkeeper run: ChatGPT Codex against
the repo checkout's `samples/alex-chen-morales.gnucash` (local MCP,
v1.4.2, all modules), ~10 minutes, majority of the 111-tool surface
exercised. Overall verdict: functional 8/10, payload design 8.5/10,
"ready to ship." Three workflow-correctness findings, all in the
business module's credit-note/voucher corner. Targeted at the 1.4.4
patch release alongside the annotations + CLI-strictness branches.

## F1 — apply_credit_note error message is self-contradictory (CONFIRMED)

Reported: applying `ZZZ-CN-1` to `ZZZ-INV-POST` failed with
"credit note belongs to `000005` but target belongs to `000005`,
and they must match."

Mechanism (confirmed by code read, `book/business.py`
`apply_credit_note`, ~line 6166): the same-owner check correctly
compares `owner_guid`s, but the error message renders each owner's
**ID string**. Entity ID sequences are per-type, so customer
`000005` and vendor `000005` are different entities with identical
IDs — a true mismatch prints as a contradiction. The tester created
both a disposable customer and a disposable vendor, which is exactly
the collision.

Fix shape: include the owner type label (and ideally name) in the
message: "belongs to vendor '000005' (Acme LLC) but target belongs
to customer '000005' (…)". Audit sibling messages: the create-side
applies-to check (~line 4221) compares by **ID string**, not GUID —
that one may be a REAL comparison bug (cross-type ID collision would
false-PASS), stricter than the message cosmetics. Grep for other
`owner.id` comparisons; the check-vs-act class.

## F2 — credit-note identity lost after unpost (REPRO NEEDED)

Reported: `unpost_invoice("ZZZ-CN-1")` succeeded, then
`get_invoice("ZZZ-CN-1")` returned `type: "invoice"` and
`delete_credit_note` rejected it; only `delete_invoice` worked.

Code context: credit-note-ness is the GnuCash `credit-note` slot
(int 1) read via `_get_is_credit_note`. `unpost_invoice` itself
reads the flag BEFORE deleting lot/txn and returns
`type: "credit_note"` correctly — but a comment there warns slot
reads "can flake ('Multiple rows returned with uselist=False')
while lots/transactions are being deleted in the same session."
Hypothesis: the unpost path corrupts, duplicates, or cascades away
the invoice's slot row, so later sessions misread the flag.
Reproduce on a scratch book: create CN → post → unpost → reopen →
`_get_is_credit_note`; inspect the slots table directly for the
CN's obj_guid before/after.

## F3 — voucher posting unreachable via post_invoice (REPRO NEEDED)

Reported: voucher create/add-entry/delete all work, but
`post_invoice(id="ZZZ-VOUCH-1")` → "Document not found", and no
voucher-specific post tool exists.

Code context: `post_invoice`'s docstring offers only
customer/vendor for `owner_type`; the no-filter finder path should
still match a voucher row by ID. Candidate causes: (a)
`create_voucher` auto-assigns IDs from `counter_exp_voucher` and
ignores/replaces the caller's `id` (so "ZZZ-VOUCH-1" was never the
stored ID); (b) `_find_invoice` excludes owner_type=5 somewhere;
(c) voucher posting genuinely unsupported → then it's a docs +
error-message gap at minimum. v1.3 letters imply posting math
handles the voucher quadrant, so (a) is the leading suspect.

## Payload notes (no action required, recorded for the record)

Cross-model grades: TSV batch entry A ("excellent for batch
entry"); short GUIDs A- ("materially improves payload size");
create_prices B+ (strict optional-column ORDER is easy to trip
over — possible future leniency); get_unreconciled_splits verbose
on large accounts C+ (pagination correct, still heavy — known).

## Test residue

The run wrote into the working-tree copy of
`samples/alex-chen-morales.gnucash` (repo checkout). Leftovers:
posted/paid ZZZ-INV-POST and ZZZ-BILL-1, customer 000005, vendor
000005, `Expenses:ZZZ MCP TEST 2026-08-05`, voided txn 87585ada.
Samples are frozen oracles — `git restore` the file rather than
hand-cleaning (also discards pre-existing drift; the committed
version is the reference).
