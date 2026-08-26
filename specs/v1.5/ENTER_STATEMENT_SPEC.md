# enter_statement — one-shot statement entry (v1.4.4 headliner)

Status: **implemented on `feat/enter-statement` (2026-08-24) —
rulings 1–9 shipped, ruling 10 deferred to its own branch per the
§10 phasing note. Three-agent adversarial review run and fixed on
the branch; §11 records the implementation's flagged deviations
for maintainer review.** The body is the buildable design; §9–§10
record rulings, provenance, and the declined/gated items. Retargeted from v1.5 to v1.4.4 on 2026-08-21:
the 1.4.x line's theme is bulk operations, and this tool is its
capstone.
Origin: parked in the 2026-07-25 session ("entry + reconcile in ONE
session/save"); revived and ruled after a live Cash App PDF-statement
probe surfaced the four-step friction firsthand.
Rulings recorded here: statement as verifiable first-class input;
every line through the judgment pass, exact matches included;
dry-run rows carry the matched transaction's full annotation and
date; commit is one atomic open/save that refuses wholesale on a
failed balance tie.

---

## 1. Problem

Entering a bank/card statement today is four calls with an
intelligence step wedged between them:

1. `create_transactions` (`dry_run=true`, no split cells) — the
   auto-fill matcher "predicts" splits and memos per line and
   surfaces duplicate candidates.
2. **The judgment pass** (LLM + user, outside the server): read the
   original lines against the matched records, adjudicate what's a
   duplicate, adapt annotations, present a confirmation table.
3. `create_transactions` (`dry_run=false`) — enter and categorize.
4. `get_unreconciled_splits` → `reconcile_account` — collect split
   GUIDs, reconcile against the statement balance.

Steps 1, 3, and 4 are server-mechanical. Step 2 is irreducible —
it IS the intelligence, and the server should serve it, not absorb
it. The 3→4 gap is the real defect: entry and reconciliation are
separate acts, so a crash, a distraction, or a balance surprise
between them leaves a half-landed month — transactions entered but
unreconciled, with no record that they came from a statement.

The unlock: when the input is a **complete statement** (opening
balance, closing balance, every line between), reconciliation stops
being a separate act. It is simply what "commit" means.

## 2. Shape — dry-run, judgment, commit

`enter_statement` collapses the plumbing to two calls around the
unchanged judgment pass:

```
enter_statement(dry_run=true)   → classification + prediction table
[LLM judgment + user confirmation — unchanged, better served]
enter_statement(dry_run=false)  → enter + claim + reconcile, atomic
```

The statement itself is first-class input:

```
enter_statement(
    account,             # ref: path, %short, or GUID
    statement_date,      # ISO — the statement's closing date
    opening_balance,     # decimal string
    closing_balance,     # decimal string
    lines,               # TSV block, batch-grammar dialect (§3)
    dry_run=True,        # DEFAULT TRUE — rehearsal-first
)
```

`dry_run` defaults **true** (like `prune_backups`, unlike
`create_transactions`): a statement is a document-level operation
and the rehearsal is the workflow, not an option.

### Self-consistency gate (both modes, before anything else)

`opening_balance + Σ(line amounts) == closing_balance`, or the
call rejects: the statement contradicts itself and no
classification is trustworthy. This turns "the statement is
complete" from an assumption into a checkable claim.

A second precondition check compares `opening_balance` against the
account's current reconciled balance. Mismatch is a WARNING in
dry-run (prior statement may be unentered — a legitimate state),
and blocks commit unless `force=true` (reconciling onto a base
that doesn't tie produces a `y` state that means nothing).

## 3. Line grammar

The `lines` TSV reuses the batch header-is-the-schema grammar —
same parser lineage, same column vocabulary (`ref`, `date`,
`description`, `notes`, `memo`, `qty`, split pairs), plus two
statement-specific columns:

- `raw` — the verbatim statement line. Lands on the bank-leg split
  memo (provenance, per the annotation convention). In dry-run
  pass the LLM typically supplies ONLY `ref, date, raw, amount` —
  transcription, not interpretation.
- `match` — commit-mode only (§5): the split GUID this line claims
  instead of creating a new transaction.

Rows with no split cells flow through the auto-fill matcher
exactly as in `create_transactions` — that machinery
(`_collect_create_signals`) is reused, not duplicated.

## 4. Dry-run output — the judgment pass's working table

One correlatable table, joined on `ref`, one row per statement
line, classified:

- **NEW** — no existing-transaction candidate. Carries the
  auto-fill prediction (splits, memo) when the matcher found a
  same-description precedent, marked `auto_filled_from:<guid>`.
- **MATCH** — an existing UNRECONCILED split on this account with
  candidate-grade correspondence (amount/date). The row is a
  **self-contained comparison** (ruling 9): the matched
  transaction's complete annotation — description, notes,
  bank-leg memo, post date, short GUID — side by side with the
  statement line's values, plus computed `date_delta_days` and
  `amount_delta` (raw values AND deltas; a `-0.05` is unreadable
  without $47.95 vs $48.00), plus the candidate's category
  accounts and a `split_match` verdict (`exact`/`partial`/
  `none`) computed over the NON-statement-account split sets —
  the statement account is shared by construction and carries no
  signal; the category legs are the comparison. The caller never
  joins back to its own input to reconstruct either side.
  Ruling: exact matches are NOT auto-claimed — every line flows
  through judgment. The context is deliberate: a July 31 line
  matching a June 30 transaction whose memo reads "July rent"
  should let the model suggest "August rent" for the new
  instance — the server cannot make that inference, so it must
  ship the evidence that lets the model make it.
- **OVERLAP** — matches an already-RECONCILED split (prior
  statement's tail). Default disposition: skip — the line is
  already landed and tied. Surfaced, never silently dropped.
- **AMBIGUOUS** — multiple candidates. All candidates listed with
  the same full-comparison columns as MATCH, sorted by
  descending risk (strongest correspondence first, stable
  tie-break on candidate GUID) — the reader's model of the list
  must not form on the harmless entries. Judgment picks or
  rejects.

Ruling 8's status vocabulary is native here: the class IS the
review status. NEW is this tool's honest `would_create`;
MATCH/AMBIGUOUS are `review_required` by construction; OVERLAP
is a surfaced skip. No projected-action label can masquerade as
clearance because no class asserts one.

Plus a projected-tie footer: what the reconciled balance would be
after commit, against `closing_balance`, with the discrepancy if
any. The rehearsal is a FULL rehearsal — a dry-run that ties is a
commit that will tie.

When any MATCH/AMBIGUOUS/OVERLAP rows exist, the envelope also
carries a `review_token` (ruling 10): a content-derived hash
binding book identity, the normalized statement (balances +
lines), the candidate set (GUIDs + comparison data), detector
version, and the deterministic ordering. Stateless by design —
nothing is stored server-side; commit recomputes it from current
book state. An all-NEW dry-run emits no token and commit demands
none: ceremony proportional to risk.

### Summary header (ruling 6, 2026-08-21)

The response OPENS with a count line; the classification table
sits between it and the tie footer. The header counts the classes
and assigns the work:

```
Dry run: 45 lines — 12 NEW, 30 MATCH, 2 OVERLAP, 1 AMBIGUOUS.
31 rows need adjudication — rule each MATCH/AMBIGUOUS against
the table before committing.
```

**The clearance principle:** the server states what it verified;
it never phrases an unverified judgment as clearance. Counts are
facts and may headline. "0 blocking", "safe to commit", and any
cleared/uncleared dichotomy over MATCH/AMBIGUOUS rows are
judgments the judgment pass has not made yet — banned vocabulary.
A satisficing consumer treats a verdict-shaped header as
permission to skip the rows it summarizes, which un-serves the
one step this tool exists to serve. The ONLY verdict-shaped line
in the response is the projected tie, because that is an
arithmetic fact the server actually checked. When a class is
genuinely empty, saying so plainly ("no duplicate candidates") is
a verified fact and allowed.

## 5. Commit semantics — one open, one save, or nothing

The judgment pass produces the commit-mode `lines`: NEW rows now
carry interpreted `description`/`notes` and confirmed splits
(adapted by the model, confirmed by the user); MATCH rows carry
`match=<split guid>`; OVERLAP rows are dropped or kept as `match`
rows pointing at the reconciled split (idempotent no-op). The
commit grammar IS the disposition table (ruling 10's principle,
native here): each line's commit form — claimed, created-despite-
candidate, or dropped — encodes an explicit adjudication.
Ambiguous correspondence must be resolved explicitly in the
commit representation; a dry-run MATCH/AMBIGUOUS `ref` that is
simply absent from the commit lines (rather than dropped-as-
OVERLAP or resolved) rejects before any write.

When the dry-run emitted a `review_token`, commit requires it.
The server re-runs classification against CURRENT book state and
recomputes the token; mismatch → `review_stale` plus a refreshed
classification table, **no write** — a candidate that appeared
between rehearsal and commit is caught, not sailed past. Same
statement, unchanged book → same token: the rehearsal-commit
bond is deterministic, not stored, so it survives the two-
process topology (each server copy computes it identically).

In one book session:

1. Create every NEW row (existing batch validation chokepoints:
   `_validate_transaction_splits`, duplicate screening, quantum
   checks).
2. Claim every MATCH — no new transaction; the referenced split is
   verified to belong to the statement account and to be
   unreconciled (voided splits reject, per `_is_voided`).
3. Reconcile every statement-touched split (created + claimed) at
   `statement_date` — the same tie-then-mutate discipline as
   `reconcile_account`: compute the resulting reconciled balance,
   compare to `closing_balance`, and if it does not tie, **refuse
   before mutating**. Nothing persists. No half-landed months.
4. Save. One audit entry of a new `("statement", "ENTER")` type
   rendering the document event: account, period, N created,
   M claimed, closing balance, tie status — not N disconnected
   CREATE lines (dispatch-table entry required, per contract
   test). Per ruling 10's audit payoff, the entry also renders
   the per-line adjudications: each claimed line with its
   candidate GUID, each line created despite a surfaced
   candidate, each OVERLAP skip, and the `review_token` — the
   judgment pass's outcome enters the permanent human-readable
   record instead of evaporating in conversation history.

Book transactions inside the period that are NOT on the statement
(pending checks, in-flight ACH) are naturally untouched — only
statement-touched splits reconcile, and the tie still closes
because the statement's own arithmetic already balanced. This is
`except_guids` semantics without the parameter.

Commit response: the batch-convention results table (`ref`,
status ∈ `created`/`claimed`/`skipped_duplicate`, short GUID),
headed by the document line — account, period, N created,
M claimed, K skipped — and closed by the VERIFIED tie:
`Reconciled: <count> splits @ <statement_date>; closing balance
<currency> <amount> — tied.` The tie line is verdict-shaped and
allowed: it is the arithmetic fact the commit just enforced
(clearance principle, §4). Statement-native amounts echo nowhere
— the caller has them; the response reports outcomes.

A consequence worth naming: `y` acquires a stronger meaning on
this path. `reconcile_account` ties to a balance someone asserts;
`enter_statement` ties to a document the server verified was
internally complete. Reconciled-by-statement means
tied-to-evidence.

## 6. Relationship to existing tools

- `create_transactions` — unchanged in grammar; remains the tool
  for non-statement bulk entry (opening balances, migrations,
  manual batches). `enter_statement` shares its parser and signal
  machinery via the existing chokepoints; divergence between the
  two grammars is a bug. The rule extends to output (§9–§10):
  summary header, comparison columns, and the review/token
  machinery render through shared helpers, so the two dry-run
  surfaces evolve together — `create_transactions` inherits the
  review protocol per ruling 7/10 phasing, it doesn't fork it.
- `reconcile_account` — unchanged; remains the tool for
  reconciling already-entered books against an asserted balance.
- `get_unreconciled_splits` — no longer needed on the statement
  path (dry-run MATCH rows carry the GUIDs); untouched otherwise.
- The two-tools question (`create_transaction` vs batch): this
  tool may render it moot for the dominant workflow, as the
  2026-07-25 letter predicted. Re-litigate after live use, not
  before.

## 7. Rulings (all five, 2026-08-11)

1. **Match candidate criteria — the existing DAD scoring, window
   31 days.** Candidacy reuses `_collect_create_signals`'s
   description/amount/date confidence scoring (no second matcher),
   with the window at 31 days rather than the duplicate screen's
   30. The extra day is load-bearing: a monthly pattern (June 30
   "July rent" vs a July 31 line) must SURFACE as a candidate so
   the judgment pass can rule "new instance, adapt the annotation"
   — the MATCH table is deliberately a superset of true
   same-event matches, and judgment separates claim from
   new-with-adapted-memo. Candidate noise is the feature.
2. **Claimed-split annotation updates — yes.** A MATCH row may
   carry `notes`/`memo` cells that update the claimed
   transaction's annotations; landing the raw statement line onto
   a bank-leg memo it never had is provenance, the point of the
   exercise. Claiming is therefore a write to an existing
   transaction — the `("statement", "ENTER")` audit entry must
   render annotation diffs on claimed rows, not just creations.

3. **Foreign-currency statements — deferred.** v1 requires the
   statement currency to be the account's commodity in the book's
   default currency. A USD statement against a CNY book is a
   follow-up (the batch `cur` machinery exists; the tie check
   would need to compare in statement currency).
4. **Amount sign convention — statement-native, transformed by
   account class.** Line amounts AND the opening/closing balances
   are transcribed exactly as the statement prints them; the
   server applies the sign transform for the account's class,
   resolved by `GNCAccountType` (never name, per the i18n
   invariant). Asset-class (BANK/CASH/ASSET) statements already
   agree with the split convention — pass-through. Liability-class
   (CREDIT/LIABILITY) statements print charges positive and
   balances as amount-owed — the server negates both, so a printed
   charge lands as a negative split and a printed "$500 owed"
   closing balance ties against a book balance of −500. The model
   transcribes verbatim, zero mental sign-flips: this eliminates
   the transcription-error class, and the self-consistency gate
   (§2) checks in statement-native signs before any transform.
5. **Module placement — core, transactions sub-module.** Same
   "touches money" argument that moved reconciliation into core
   in v1.4. Tool registration rides `tools/core.py` beside
   `create_transactions`; `TOOL_MODULES` entry under the
   transactions sub-module.

## 8. What this is not

- Not an OFX/CSV/PDF parser — transcription from the document to
  the TSV is the model's job (it is already good at it; the Cash
  App probe entered a PDF image set with no parser).
- Not a replacement for judgment — the confirmation table is a
  feature, not friction. The tool exists to make the two calls
  around it trivial and the landing atomic.
- Not business-document aware — invoices/bills settle via
  `pay_invoice`; a statement line that pays an invoice is a MATCH
  against the payment transaction like any other.

## 9. Addendum — dry-run response rulings (2026-08-21)

Provenance: an outside-model reviewer (ChatGPT, connected live to
the sample books via the Glama sandbox) ran a 45-row
`create_transactions` dry-run on Alex's book and critiqued the
response. The report validated the core design — `ref`-as-data
called "the strongest design choice"; the two-table join; the
suppress-LOW/label-MEDIUM ruling read exactly as intended (it
classified coffee-shop MEDIUMs as pattern noise without reaching
for `force`) — and surfaced improvements, triaged here.

6. **Summary header, homework-framed** — specified in §4. The
   risk that shaped the framing: the reviewer's own suggested
   header ("0 blocking … safe to commit with force=false") is a
   free pass — a satisficing model reads a cleared verdict and
   skips the candidate rows. In-house evidence that response
   framing steers foreign-model behavior: the verbose-reframing
   episode, where an outside model changed its output habits
   because our descriptions reframed compact as complete. Hence
   the clearance principle (§4): counts headline, judgments
   don't, the tie is the only verdict.

7. **`create_transactions` inherits, same branch.** The batch
   dry-run gains the same three upgrades: a summary header
   (would-create/rejected counts + a candidate homework line,
   never a clearance line), a `max_confidence` column in the
   results table (saves the two-table join for the common
   decision; the candidate table remains for the real one), and a
   projected per-account effects footer (the rehearsal-
   completeness principle this spec's tie footer already
   embodies, minus the tie — batches assert no closing balance).
   Rendering goes through a shared helper; §6's rule extends
   from grammar to output — divergence between the two dry-run
   surfaces is a bug.

Declined from the same review, with reasons: per-row `validated`
reason padding and per-row currency echo (blank-means-fine is
measured byte economy — median tool response is ~121 bytes;
the envelope can state the effective currency once), native
structured arrays in place of TSV-in-JSON (`verbose=true` is the
house answer; the reviewer's own ratings conceded "machine
usability: excellent"), an in-response signal-code legend (lives
in the tool description, which the reviewing model itself decoded
without one), a category-distribution echo (summarizes the
caller's own input back at it), and commit-recommendation lines
("safe to commit with force=false" — the clearance principle,
ruling 6).

## 10. Addendum — duplicate-review protocol rulings (2026-08-24)

Provenance: the same outside-model reviewer, cross-examined by
the maintainer ("would you have pushed through creating those
medium dupes?"), retracted its structured-arrays critique
(conceding the TSV envelope), admitted its "safe to commit"
shorthand was unsound, and — asked what presentation would have
prevented the gloss — produced the review-queue proposal ruled
here after a second round of design exchange. The declined
native-arrays item in §9 stands retracted by its own author.

Architectural note on the stateful piece: an initial objection
assumed server-held pending-batch state, which the multi-copy
topology (two clients, two server processes) genuinely breaks.
The adopted design instead carries dispositions in the caller's
commit request and recomputes verification state from current
book contents — stateless multi-copy operation preserved. State
lives in the conversation; verification lives in the server.

8. **`review_required` status.** The clearance principle (ruling
   6) applied per-row: a projected-action label must not
   masquerade as clearance. `would_create` stays on
   candidate-free rows — there it is an honest, verified
   clearance. Any row with ≥1 duplicate candidate reports
   `review_required` instead; `rejected` + `reason` continues to
   cover HIGH blocks and validation errors. No parallel
   compat column — one status, one meaning.

9. **Self-contained candidate comparisons.** The caller never
   joins the candidate table against its own input mentally.
   Columns: `ref`, `candidate_guid`, confidence, proposed +
   existing description, proposed + existing date,
   `date_delta_days`, proposed + existing amount,
   `amount_delta`, proposed + existing category accounts,
   `split_match`, signal code. Raw values AND deltas both — a
   `-0.05` delta is unreadable without $47.95 vs $48.00, and
   candidate tables are small; this is the right place to spend
   width. `split_match` (`exact`/`partial`/`none`) compares the
   NON-PAYMENT split sets — the batch's payment account is
   shared by construction and carries no signal; the category
   legs are the comparison (the review's own Chewy/fuel case:
   MEDIUM on date+amount, decisively distinct on category).
   Candidates sort by descending risk (strongest correspondence
   first), stable tie-break on (`ref`, `candidate_guid`) — the
   reader's model of the list must not form on the harmless
   entries.

10. **Disposition acknowledgment protocol (stateless).** Active
    only when `review_required` rows exist — zero-candidate
    batches commit exactly as today, no token, no ceremony.

    Flow: dry-run returns a content-derived `review_token`
    binding book identity, normalized batch contents, candidate
    GUIDs + comparison data, detector version, and the
    deterministic candidate ordering. Commit requires the token
    plus one disposition per (`ref`, `candidate_guid`) pair:

        ref  candidate_guid  decision          reason
        A06  7ee458e4        distinct          repeat merchant; 28 days apart
        A31  51bbd002        same_transaction  same merchant/amount; date off by one

    - `distinct` (reason required) — clears that pair.
    - `same_transaction` (reason required) — the proposed row is
      NOT created; it lands in results as `skipped_duplicate`
      with the candidate GUID (the OVERLAP semantics of §4,
      generalized — a correct adjudication is never punished
      with a batch rejection).
    - `uncertain` — blocks commit; the escalation path is
      `get_transaction` on the candidate, inspect splits,
      re-dispose.
    - Any missing disposition — reject before any write.

    Commit reruns detection and recomputes the token from
    CURRENT book state. Mismatch → `review_stale` + a refreshed
    review table, no write: a MEDIUM candidate that appeared
    between rehearsal and commit is caught, which the current
    design cannot do. The token derives from the signal sweep
    commit already runs — no new pass (and this ships AFTER the
    batch signal hoist; the sweep is the known p95 tail).

    Honest limit, on the record: the protocol guarantees CONTACT
    with the evidence, not quality of judgment — a careless
    caller can still write `distinct` mechanically. But one
    disposition per pair, a nonempty reason, candidate-specific
    identifiers, and a matching token raise the cost of careless
    approval and make failures inspectable afterward.

    **Audit is the largest payoff:** the commit's audit entry
    persists the hash, each (`ref`, `candidate_guid`,
    decision, reason), detector confidence/signals, and whether
    `force` was used. The adjudication reasoning — today
    evaporating in conversation history — enters the permanent
    human-readable record for the first time.

    **Flagged for a bookkeeper ruling before adoption:** the
    protocol's `force` division (acknowledgment proves
    adjudication; `force` only authorizes creating despite a
    HIGH match adjudicated `distinct`) tightens the DIRECT
    commit path — today `force=true` with no dry-run bypasses
    HIGH blocks wholesale, and that is a live workflow. Right
    design, but it changes bookkeeper-validated behavior;
    per `feedback_bookkeeper_validates_base_cases`, it does not
    ship on spec alone.

    Implementation phasing is the maintainer's call: rulings
    8–9 are cheap riders on the §9 dry-run work; ruling 10 is
    its own branch, sequenced after the signal hoist.

## 11. Implementation notes — flagged deviations (2026-08-24)

Recorded by the implementing session for maintainer review; each
is deliberate and test-locked, none silent. Reverse any of them by
ruling.

1. **Ruling 1's "no second matcher," amended by its own rent
   case.** Statement candidacy is an account-scoped scan, not a
   `_collect_create_signals` pass: the collector's ≥2-signal
   admission rule would NOT surface the June-30-rent-vs-July-31
   line (amount signal only), which ruling 1 requires to surface.
   The scan admits the amount signal alone — sound in the narrow
   universe of one account's splits. The A/D thresholds themselves
   are shared module constants (`_MATCH_AMOUNT_TOLERANCE`,
   `_MATCH_DATE_TIGHT_DAYS`) read by both matchers, so the signal
   semantics cannot drift; only the admission rule differs, on
   purpose.
2. **OVERLAP means the same event, exactly.** Classification and
   the commit guard define overlap as amount-exact (to the
   commodity quantum) AND date within the tight window. Amount-fuzz
   overlap misclassified the genuine next-month line as already
   landed (adversarial-review finding). Fuzzy reconciled
   candidates still ship as evidence rows under NEW — that is the
   annotation-adaptation case working as designed.
3. **`force` also bypasses the closing tie, recorded not silent.**
   Forcing past an opening gap G mathematically guarantees a
   closing discrepancy of G, so opening-force without tie-force
   would be a dead parameter. A forced untied landing reports and
   audits `DISCREPANCY … landed under force`. Unforced commits
   refuse wholesale per §5.
4. **The commit guard is stricter than §5's letter.** A bare
   create whose EXACT twin (amount + tight date) exists unclaimed
   — unreconciled or reconciled — rejects unless forced: it is the
   one double-entry the tie cannot catch. This tightens the
   direct-commit path ahead of ruling 10's protocol; the same
   bookkeeper-ruling flag applies.
5. **Claim memo updates ride `raw`.** The statement grammar has no
   per-line memo cell; a claim's bank-leg memo becomes the
   verbatim `raw` line (§3's provenance convention). `notes` cells
   update the claimed transaction's notes as ruled.
6. **Zero-line statements reject** ("statement has no lines"), with
   `reconcile_account` as the pointed workaround. A no-activity
   statement is a real monthly occurrence — needs a ruling if the
   workaround isn't acceptable.
7. **Claim exactness is unconditional.** A match cell naming any
   split — reconciled included — whose amount differs from the
   line rejects at the row (wrong-GUID paste protection); the
   reconciled no-op path applies only after the amounts agree.

### Maiden-voyage amendments (2026-08-24, bookkeeper findings)

8. **Invisible line separators reject by name.** The maiden
   flight's one open bug: `str.splitlines` shatters a row on
   U+2028/U+2029/NEL/VT/FF hiding inside a cell, and the resulting
   mid-group error truthfully blames the row while unable to name
   the invisible byte (four deterministic failures, a night of
   bisection, every retyped reduction passing). All TSV parsers
   now split on real newlines only and reject the exotic
   separators loudly, naming the character and row.
9. **Claim rows end at their last fixed column.** Stray cells
   beyond it get a claim-specific error instead of falling into
   the group chunker's mid-group miscount.
10. **MATCH/AMBIGUOUS gate on MEDIUM+ correspondence.** LOW
    (single-signal) candidates still surface — ruling 1's
    superset — but no longer drive the class: a line whose best
    candidates are amount-only lookalikes is NEW with evidence
    attached, not AMBIGUOUS (the invented-ATM case: three weak
    lookalikes must not adopt a genuinely new line).

### Test-plan-round amendments (2026-08-24 late, bookkeeper)

11. **Recurring signature never drives the class (T6).** desc +
    amount at ≥21 days' remove (`_RECURRENCE_MIN_DAYS`) is the
    monthly-pattern signature — last month's HOA, not this line's
    twin. Such candidates ship as evidence under NEW. Short
    clearing drift (≤~3 weeks) with desc+amount still MATCHes —
    the maiden flight's date-drift claims depend on it.
12. **Commit rejections coach inline (T5b).** The row's error —
    including the "claim it with match=…, or force" next move —
    rides the results note column; the rejection envelope has no
    separate warnings table to get stranded away from.
13. **LOW display ruled best-evidence-only** (bookkeeper's ruling
    at the implementer's request). Lines with ≥1 MEDIUM/HIGH
    candidate suppress their LOW amount-coincidences with a
    breadcrumb in the cands column ("1 (+18 LOW suppressed)") and
    a `show_all=true` escape hatch; LOW-only lines keep their
    evidence (the rent case is load-bearing). Candidate ordering
    refined: risk desc, then |amt_delta|, |date_delta| asc, then
    the stable (ref, guid) tie-break. Supersedes the interim
    "cap ~5" note.
14. **Batch candidates carry `state` (T7).** The shared comparison
    table's state column fills on the batch surface with the
    candidate transaction's most-anchored split state (y > c > n)
    — a reconciled candidate is entered AND tied, which is
    decisive for the duplicate call.

### Signoff round (2026-08-24, Abe VI — certified for Statement
### Week)

15. **Twin guard precedes counter-split validation** (the signoff's
    one carried item, fixed same day): commit processes claims
    first, then runs the twin guard BEFORE auto-fill/counter-split
    validation on create rows — a bare raw line whose exact twin
    exists gets the claim coaching, not an auto-fill complaint.
16. **Verified-empty phrasing counts claimable candidates**: when
    evidence rows exist but nothing is MEDIUM+, the homework line
    reads "No claimable (MEDIUM+) candidates — the listed rows are
    evidence only" rather than contradicting the visible table.

Standing residuals (documented, adjudication-contained,
non-blocking, per the signoff): (a) a ±$1 nonzero-amt_delta
candidate can still reach MEDIUM → MATCH on a genuinely-new line
(wire-fee shape) — the delta column exposes it instantly; a
possible refinement caps amt_delta≠0 at LOW. (b) identical-amount
14-day payroll sits below `_RECURRENCE_MIN_DAYS`; cadence
inference (3+ priors at ~Nd spacing ⇒ delta≈N is the pattern's
own signature) is the v-next fix, wanting the same
per-description history as the batch signal hoist.
