# Bookkeeper report — split amount precision (fix/amount-precision)

Run: 2026-09-27, remote bookkeeper (Abe VI) on Ixion. Branch at `22c7867`
(code: `1af4209`) via detached worktree `/tmp/wt-precision`; every call
through the MCP layer over stdio (`class6_loop/mcpcall.py`, `REPO`
patched to the worktree). Books: fresh copies of committed Alex and
Lin Wei extracted from the branch (`git show 22c7867:samples/…`) into
`~/Projects/abe-bench/`. Ixion's production servers stayed on develop
throughout; no registered book touched.

## Verdict: Part B 8/8 PASS on Alex + CNY variant PASS on Lin Wei. Part C: sqlite + engine-load PASS; **desktop GUI edit-save-reopen gate still open** (nobody at the screen — see below). Five route-around notes.

## Part B — on the Alex copy
1. **Sub-cent batch refused — PASS.** Row rejected verbatim: `Split for
   'Assets:Current Assets:Checking Account': -12.345 carries finer
   precision than USD allows (2 decimals) — re-check the transcription`.
   `search_transactions` after: 0 of 0 — nothing created.
2. **Ordinary amounts — PASS.** `12.35` (`8bda924c`) and whole `12`
   (`7cca107e`) created normally.
3. **Shares round half up — PASS.** qty `6.81415` → **6.8142**
   (`a0793eb5`); `6.81414` → **6.8141** (`d09a5696`).
4. **replace_splits refuses sub-cent — PASS.** `10.005` refused with the
   same sentence; transaction unchanged (12.35 intact, same split guids).
5. **Payments refuse sub-cent — PASS.** `pay_document` 000018 @ 100.005:
   dry run and real both refused, identical sentence; invoice untouched
   (due 3,500.00, payments []). **Lin Wei variant — PASS**: paying CNY
   invoice 000014 @ 100.005 names **CNY**: `…finer precision than CNY
   allows (2 decimals)…` (plan correction confirmed).
6. **Statements refuse sub-cent — PASS.** `enter_statement` line:
   `line 1: amount: -12.345 carries finer precision…` — ref named.
7. **Void/unvoid round-trip — PASS.** 12.35 restored exactly; audit log
   shows VOID with "Was:" split actions and UNVOID with "Restored:"
   split actions; the replace_splits refusal renders as an ERROR line.
8. **Business writes still post — PASS.** Invoice A9001 (Emerald):
   create → entry 250.00 → post → pay full → `paid`, payments array
   populated. Credit note C9001: create (applies_to 000019) → entry
   100.00 → post → apply → 000019 shows `amount_paid 100.00, due
   3,400.00`, payments crediting `from: Credit Note C9001`.

## Part C — storage
- **sqlite denominators — PASS (strict).** All value_denoms **100**,
  including the whole `12` stored `1200/100` (the old code's `12/1` is
  exactly what this fix kills). VTSAX quantities `68142/10000` and
  `68141/10000`.
- **GnuCash engine load — PASS.** `gnucash-cli` 5.x (app bundle) ran
  Balance Sheet on the looped copy: exit 0, no errors.
- **Desktop GUI gate — OPEN.** The plan requires opening the looped copy
  in GnuCash desktop, checking 12.35 / 12.00 / 6.8142, and surviving an
  edit-save-reopen. No one is at Ixion's screen. Options: the bookkeeper
  attempts it via desktop control (screen-lock permitting), or the
  maintainer runs it on return. The looped copy is preserved at
  `~/Projects/abe-bench/alex-precision.gnucash` for exactly this.

## Routed around
1. **Logs-sidecar refuses world-writable dirs** (`/private/tmp`,
   mode 0777): a good guard, but it means test books can't live in /tmp;
   the bench moved to a user-private dir. Setup docs for the harness
   should say so.
2. **`apply_credit_note`'s argot differs from its siblings**: creation
   speaks `id`/`applies_to_id`, application demands
   `credit_note_id`/`applies_to_invoice_id`. The pydantic error coached
   the retry (the "called twice" pattern). Suggest accepting the
   creation-side names or renaming for symmetry.
3. **`mcpcall.py` hardcodes `REPO`** while `loop.py` honors the env var;
   patched a copy. One-line fix for the harness.
4. **Posting to the committed Alex fired a live migration** inside the
   post_document response (`templates_migrated: 17,
   invoice_links_migrated: 108, book_stamped`). Harmless here, but a
   first write mutating 100+ rows beyond its own scope deserves a line
   of release-notes visibility whenever samples predate the format.
5. **`gnucash-cli` isn't on PATH** — lives in the app bundle
   (`/Applications/Gnucash.app/Contents/MacOS/`). Environment note.

Litter manifest (bench copies only, production untouched): Alex copy —
4 probe transactions, invoice A9001 (paid), credit note C9001 (applied
to 000019), void/unvoid audit entries. Lin Wei copy — no writes (dry-run
refusals only). Both under `~/Projects/abe-bench/`; disposable after the
GUI gate closes.

Signed: Abe VI, the remote bookkeeper — every sub-cent refused at every
door, every stored fraction GnuCash-shaped, and the message knows what
currency it's speaking. One gate awaits a human (or a brave screen
session).

## Round 2 — addendum (steps 9–13), 2026-09-27

Branch at `2f4645b`, fresh copy of committed Alex
(`abe-bench/alex-precision-r2.gnucash`), repo's own `mcpcall.py` via
`REPO` env (fix `b1756e0` verified in use — no patching needed).

9. **PASS.** "Zurich Probe AG" created with CHF; commodity listed;
   storage fraction **100**.
10. **PASS.** SEK, no fraction → created, fraction 100. NOK @ 10000 →
    refused verbatim: `The ISO 4217 fraction for NOK is 100, and
    GnuCash stores the currency that way. Omit fraction, or pass 100.`
    XYZ → refused: `XYZ is not an ISO 4217 currency code — GnuCash's
    currencies come from the ISO table. Use another namespace for a
    non-currency commodity.` ACME (NASDAQ, no fraction) → 10000.
    (Note: `fullname` is required by the schema; the plan's shorthand
    omits it. Not a defect — a plan nit.)
11. **PASS.** Old names (`credit_note_id`/`applies_to_invoice_id`) now
    schema-rejected naming both as not permitted — the exact inversion
    of round 1's finding; `id`/`applies_to_id` applied C9002 (50.00) to
    000019 → due 3,450.00, payments crediting `from: Credit Note C9002`.
    Round-1 route-around 2: **RESOLVED** (`7b4a3bf`).
12. **PASS.** Invoice A9002 created with unseen JPY; storage fraction
    **1** (ISO minor unit, not the currency default).
13. **PASS.** `list_documents` bills-only and vendor-side return the
    same 8 rows; A9003 post → unpost → `get_document` reads `open`.
    Owner-side refactor (`507cb9f`) exercised across all five tools
    with rounds 1+2 combined — no behavior change observed.

Storage cross-check: `commodities` table reads JPY|1, CHF|100, SEK|100,
ACME|10000, USD|100. Round-1 route-around 3 (mcpcall REPO): **RESOLVED**
(`b1756e0`). The migration side-effect (route-around 4) fired again on
this fresh copy, as expected for pre-format samples.

**Part C desktop GUI gate remains the only open box** — both rounds'
litter sits in `abe-bench/` awaiting it, per the plan.

Countersigned for round 2: Abe VI. The currencies come from the ISO
table now, and the tools speak one argot.
