# Bookkeeper report — PR #172 battery (+ #171 owed items)

Run: 2026-09-04, book `pr172-scratch.gnucash` (copy of alex-chen-morales), branch `fix/class-5-review-items`, v1.4.4, 87 tools.

## Verdict: 14/15 as specified, 1 qualified, 3 route-around findings (§F)

## A. Placeholder legs in scheduled templates — PASS (3/3)
- A1 refusal verbatim: `Account 'Expenses' is a placeholder and cannot receive splits — post to one of its children: 'Expenses:Groceries', 'Expenses:Dining', 'Expenses:Medical' (+11 more)`
- A2: refused template absent from list (17 inherited schedules only).
- A3: real child created (`38186848`).

## B. Blank date cell — PASS (2/2)
- B4 refusal verbatim: `row 2 (ref '2'): date '' is not a valid YYYY-MM-DD date` — row + ref named, no results table.
- B5: fixed date → normal 3-row would_create envelope + effects.

## C. Duplicate billterms — PASS (adapted; see §F-1)
- Scratch inherited `Net 30` from Alex, so the create leg used `Net 45` (created ✓).
- Dup vs fresh: `Billterm already exists: 'Net 45' (due in 45 days)`
- Dup vs inherited (bonus): `Billterm already exists: 'Net 30' (due in 30 days)` — the plan's exact expected quote.
- list_billterms: exactly one of each. No dupes.

## D. Owner resolution (#171 owed) — PASS with one qualified
- D9 voucher arc (employee 000002 "Quinn Testerly"): posted `560543d9`, paid `dfc85ace`.
- D10 job arc (customer 000005 "Meridian Skylight Co", job 000004, invoice 000047): posted `f874ba77`, paid `483b5ece`.
- D11 **PASS**: payment descriptions = party names ("Quinn Testerly", "Meridian Skylight Co"); posting descriptions likewise the party name, not "Invoice NNN". Job-attached invoice resolves through job → customer. #171 debt paid.
- D12 **PASS**: pre-payment, `get_outstanding_documents(customer_id=000005)` showed `000047 Meridian Skylight Co (job:000004)`.
- D13 **QUALIFIED**: deletion blocked, but refusal cites invoices, not the job: `Cannot delete customer with posted invoices: 000047. Void them or issue credit notes first.` Probe (customer w/ job only): job guard exists and speaks perfectly: `Cannot delete customer with jobs: 000005. Delete the jobs first (delete_job), or retire the customer with active=false.` → **guard-ordering finding**: invoice guard masks job guard when both apply. Suggest citing all blockers in one refusal, or checking jobs first.
- D14 **PASS**: `Cannot delete employee with posted vouchers: 000001. Void them or issue credit notes first.` — but see §F-3.

## E. Cross-tool owner name — PASS
Invoice 000048 (unpaid, job-attached): `get_document` → owner_name "Meridian Skylight Co" + job object; `list_documents(job_id)` → "Meridian Skylight Co (job:000004)"; `get_outstanding_documents(customer_id)` → same. Unanimous. Paid 000047 correctly excluded from outstanding.

## F. Route-arounds (each a report per instructions)
1. **Plan assumption vs inherited state**: scratch copies of real demo books arrive with billterms (`Net 30` pre-existed). C adapted to `Net 45` + bonus inherited-dup test. Plan-note, not a server bug — but future batteries should either use a barren book or the plan should name a term unlikely to exist.
2. **`add_document_entry` account-not-found has no suggestions**: I guessed `Income:Consulting Income`, got bare `Account not found: Income:Consulting Income`, and had to `list_accounts` + retry (the "called twice" pattern). Sibling tools (batch entry, statements) suggest candidates on account misses. Uplift the entry tools to match.
3. **Impossible remedy in employee refusal copy**: D14's text offers "issue credit notes" — credit notes are customer/vendor instruments; employees have no credit-note path in `create_document` (party_type for credit notes: customer|vendor). Copy fix: employee variant should say "Void them first."

## Litter manifest (scratch book, safe to delete whole file)
Schedule 38186848; billterm Net 45; parties 000002 (employee), 000005, 000006 (customers); job 000004, 000005; voucher 000001; invoices 000047 (paid), 000048 (posted, unpaid); payments dfc85ace, 483b5ece; B-section dry-runs wrote nothing. `pr172-scratch.gnucash` is disposable — rm when #172 closes, or keep for the next battery.
