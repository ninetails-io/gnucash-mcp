# Comment Doctrine

House style for code comments and docstrings in this repository. Written for
maintainers who were not present for the project's history — comments must
speak to them. The git history, `CHANGELOG.md`, and `specs/` preserve the
past; comments do not need to.

---

## The rules

### 1. A comment states a constraint the code cannot show

An invariant, an external system's behavior (GnuCash/piecash quirks), or a
warning against a plausible wrong simplification. Present tense, about the
code as it is today.

### 2. History lives in git, CHANGELOG.md, and specs/ — never in comments

No provenance: who found a bug (Copilot, the bookkeeper, a review), which
PR/commit/review pass produced a change, what the code used to do — unless
rule 3 applies.

### 3. Pre-fix narratives survive only as guardrails

Keep a "before the fix, X happened" story if and only if it warns against an
attractive simplification a competent maintainer might plausibly reintroduce
(e.g. "the obvious abs() here inverts overpayments"). Compress to the minimum
that makes the trap visible. Otherwise cut — the regression tests carry the
memory now.

### 4. Pointers must be live

Review-finding IDs ("(C7)", "(SB-6)", "(adversarial pass 2, A5)", "R-2")
resolve nowhere for a new reader. Replace each with nothing, with a
self-contained restatement, or with a reference to a real test/helper — and
never cite a test or helper you have not personally verified exists.

### 5. Docstrings lead with the contract

What the function does, its arguments' semantics, its failure modes.
Refactor history ("these two methods were 90% duplicated") and commit-plan
narration do not belong in docstrings at all.

### 6. A comment that is false about the present code is a bug

Mid-refactor narration that says work "happens later" when it landed weeks
ago, or describes a data structure as empty when it is populated, gets fixed
first and committed under `fix(comments):`.

### 7. The null edit is a valid disposition

Classification is the work; editing is its output. A sweep is not measured
by diff size. A large share of this codebase's comments are excellent and
must survive untouched.

### 8. A comment earns its length

About 40% of this codebase is commentary. Length is a cost the reader
pays on every visit, so a block over ~6 lines must earn each paragraph —
each one stating a distinct constraint, trap, or contract fact. Concretely:

- **Chokepoint rationale lives once, on the chokepoint.** A call site
  gets at most a one-line pointer ("converted via
  ``_convert_invoice_amount``; see its docstring"), never a
  re-explanation. If two blocks explain the same rule, one of them is
  a pointer.
- **Trap stories are one to two sentences:** name the wrong move, name
  the consequence. The regression suite carries the reproduction; the
  comment only has to make the trap visible (rule 3 still governs the
  floor — never compress past the point where the trap disappears).
- **Args entries exist only when they add semantics** — units, valid
  ranges, sentinel values, cross-field constraints, failure modes. An
  entry that restates the parameter name, the type hint, or the
  default verbatim is cut.
- **One example per format docstring.** Pick the most representative;
  delete the variations.
- **Don't narrate control flow or structure** the code or its section
  headers already show ("then we loop over…", "skeleton: 1. open the
  book…").
- **Don't re-document the callee.** A docstring that summarizes what a
  helper it calls already documents gets a cross-reference instead.

Rules 1–3 dominate rule 8: an invariant, a piecash quirk, or a live trap
is never deleted to hit a length budget.

---

## Type specimens

Concrete examples from this codebase, quoted as found at sweep time
(2026-06-11), showing each rule applied.

### KEEP — rule 3 guardrail: the overpayment guard

`src/gnucash_mcp/book/business.py` (pay_invoice):

```python
# ── Overpayment guard (adversarial pass 2, C2) ────────
# The lot balance is signed: positive for A/R invoices,
# negative for A/P bills, flipped for credit notes.
# Normalize to "amount still owed" via effective_is_bill
# and reject any payment beyond it. Pre-fix nothing
# compared amount to remaining: an overpayment drove the
# lot negative, lot-close (== 0 exactly) never fired, and
# downstream abs() calls inverted the sign — a customer
# who overpaid by $1,000 rendered as still OWING $1,000
# in the collections list.
```

Invariant plus a disaster story that guards against the "obvious" sign
handling. A maintainer simplifying the sign normalization would plausibly
reintroduce the bug, so the narrative stays. Strip only the
"(adversarial pass 2, C2)" tag — a pointer that resolves nowhere (rule 4).

### KEEP — rule 1 domain knowledge: the credit-note slot block

`src/gnucash_mcp/book/business.py`:

```python
# ── Credit-note slot helpers ─────────────────────────────────
#
# GnuCash stores the credit-note flag in slots, not as a
# column: KVP key ``credit-note``, integer value ``1`` for
# credit notes, slot absent for normal documents. The flag
# is owner-type-agnostic — a customer invoice or vendor bill
# with ``credit-note=1`` is the credit-note form of that
# document, with reversed posting direction at post time.
# [...]
```

Likewise the Job CRUD header in the same file ("Jobs are a
customer/vendor-level grouping over invoices or bills. Three things make
them simpler than the other business entities…"). GnuCash/piecash knowledge
that exists nowhere else — not in piecash docs, not derivable from the code.
Untouchable class.

### CUT — rule 2: diff narration addressed to a long-gone reviewer

`src/gnucash_mcp/book/business.py` (pay_invoice cross-currency block):

```python
# R-2: ``post_invoice`` and ``pay_invoice`` share
# the cross-currency rate-lookup + quantization
# chokepoint via ``_convert_invoice_amount``. Pay
# consumes both the converted quantity AND the
# rate (the rate feeds ``_compute_fx_gain_loss``
# below). ``stale_meta`` is collected so the response
# can surface ``fx_stale`` when ``force`` overrode the
# freshness guard; ``_convert`` keeps its 2-tuple shape
# so the unpack sites below are unchanged.
```

"R-2" resolves nowhere; "keeps its 2-tuple shape so the unpack sites below
are unchanged" is a diff explanation for a reviewer who has already merged
and moved on. The parts that describe present behavior (the chokepoint, the
rate feeding FX gain/loss) survive only if restated as present-tense facts.
Likewise every "Copilot PR #88 review caught…" attribution: the fix speaks
for itself; the finder belongs in git history.

### REWRITE — rule 6: narration that is false about the present code

`src/gnucash_mcp/book/business.py` (Taxtable CRUD header):

```python
# A multi-entry taxtable (e.g., GST 5% + PST 7%) produces N tax
# splits per line at posting time, one per entry to its own
# account — see commit 3's posting math. This commit is pure
# data CRUD; the wire-up into entries (commit 4) and into
# ``_get_invoice_entries_and_total`` (commit 3) happens later.
```

The wire-up landed weeks ago. "Commit 3" and "commit 4" identify nothing a
reader can find. And `src/gnucash_mcp/server.py` (MODULE_GROUPS):

```python
# Lets ``--modules=core`` (or any group name) expand to a set of underlying
# module keys. Today the dict is empty — no behavior change vs. the
# pre-restructure baseline. Populated in subsequent commits as the
# role-aligned partition (Core / Personal / Portfolio / Investor /
# Freelancer / Business) takes shape.
```

The dict is populated five lines below the comment claiming it is empty.
Both get restated in present tense, first, under `fix(comments):`.

### REWRITE — rule 5: docstring that leads with refactor history

`src/gnucash_mcp/book/business.py` (`_add_entry`):

```python
"""Shared implementation behind ``add_invoice_entry`` and
``add_bill_entry``. The two methods were 90% duplicated
(the only differences: which side of the entries-table
column pair gets the price/account, the owner_type code,
the allowed account types, and the response id key). This
helper takes ``owner_type`` (2=customer invoice, 4=vendor
bill), looks up the per-doc config in ``_ENTRY_CONFIG``,
and writes the entry.

**Taxtable wire-up (commit 4 of the v1.3 taxtable arc):**
[...]
"""
```

"The two methods were 90% duplicated" is the story of why the helper was
extracted — git's job. "(commit 4 of the v1.3 taxtable arc)" is commit-plan
narration. The contract content (owner_type semantics, taxtable behavior,
the tax_included-without-taxtable failure mode, the refcount side effect) is
good — it gets reorganized contract-first with the history removed.

---

## Special cases

- **`tools/*.py` docstrings are the MCP wire surface.** They are tool
  descriptions sent to clients, tuned for LLM consumption, and validated by
  the bookkeeper-review loop. They are out of scope for style sweeps; any
  change to them is a behavior change, not a comment edit. `#` comments in
  tools files follow the normal rules.
- **"bookkeeper" as a generic role** ("the surface a bookkeeper wants to
  scan") is legitimate audience rationale and stays. References to *the*
  bookkeeper's testing history ("bookkeeper-flagged that…") are provenance —
  rule 2 applies.
