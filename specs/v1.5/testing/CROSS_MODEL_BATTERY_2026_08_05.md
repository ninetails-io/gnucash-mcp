# Cross-model test battery — 2026-08-05/06

Server under test: `fix/business-doc-lifecycle` with develop merged
(annotations, verbose framing, CLI strictness, and the three Codex
fixes all in place). Tester: an outside-model bookkeeper session
(non-Claude), given the battery cold. Companion to
`../CODEX_TEST_FINDINGS.md` — this run verifies those fixes.

## Battery (as issued)

Part 1 (undirected): orient and summarize; groceries for the last
three months; outstanding invoices and biggest debtor; five most
recent checking transactions. (Stealth measurement: whether the new
verbose framing keeps a foreign model on compact output.)

Part 2 (directed, disposable `XM-` objects): (5) get_invoice on an
unposted credit note reports credit_note; (6) identity survives
post→unpost, delete_credit_note works; (7) cross-owner
apply_credit_note error names both owners; (8) voucher posts to A/P
with no owner_type; (9) wrong-owner_type voucher post error teaches
the fix; (10) bill post/pay round-trips A/P; (11) batch entry +
reconcile_all without GUID lookups; (12) void/unvoid balance
round-trip; (13) full cleanup with dependency unwinding.

Part 3: unclear docstrings; what did you route around; 1-10 input
and output design ratings.

## Results

**All nine directed tests PASS**, with the new error messages quoted
back verbatim as evidence (owner-mismatch names both owners with
kind + name; voucher not-found teaches owner_type='employee').

Verbose framing behavior change confirmed, self-reported: "I used
compact default outputs wherever possible and only used structured
JSON where aggregation or exact fields were materially helpful."
(The pre-fix battery had a foreign model flipping verbose=true on
every call.)

Ratings: input 9/10, output 8/10. Strength: compact defaults + TSV
batch interfaces. Weakness: document-family surface is slightly
non-orthogonal — callers must infer which generic tools cover credit
notes/vouchers unless every docstring says so.

## Findings → actions (all fixed on this branch)

1. get_invoice docstring now names the full document family
   (invoices, bills, vouchers, credit notes).
2. unpost_invoice docstrings (tool + book) now name vouchers and
   credit notes.
3. spending_by_category documents inclusive dates / no calendar
   snapping.

## Observations, no action

- Tester routed around the default book (switched books.gnucash →
  alex): for outside-model runs, configure GNUCASH_BOOK_PATH with
  scratch books ONLY — never list the real book where an untrusted
  session can switch_book into it.
- Scratch Alex carried ZZZ residue from the earlier Codex battery
  (expected; the git-restore recommendation in CODEX_TEST_FINDINGS
  stands for the repo working tree).
- Checking's 992 unreconciled splits (frozen-sample drift) pushed
  the tester to isolate reconcile tests in a fresh account — sound
  instinct, worth keeping in future battery instructions.
