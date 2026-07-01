# Adversarial Code Review Methodology

A recipe for finding the bugs a deferential reviewer misses. Born from
the v1.3 pre-release review of gnucash-mcp, where a first deferential
pass found 2 ship-blockers and the adversarial follow-up found 13 more
plus 12 high-priority items the first pass had positively asserted
were *not* problems.

## When to use this

Use this when **all four** of these hold:

1. The surface is large enough that a single linear read will cluster
   findings near the start and tail off near the end (this project's
   26.5k LOC qualified; a single 500-LOC file does not).
2. You don't fully trust your own first read — either because you
   wrote the code, or your model family wrote it, or the codebase is
   surrounded by confident commentary and predecessor notes that bias
   you toward generosity.
3. The cost of a missed correctness bug is high (financial data,
   medical data, security boundary, money math, anything that
   propagates silently).
4. You can spend tokens to buy skepticism you can't otherwise
   generate.

Take any of those away and this is overkill. For a narrow bugfix or a
review of a contained patch, a single careful read is the right
shape. The pattern matches the task only when you genuinely need
breadth + skepticism + cross-confirmation.

## The methodology

```
Phase 1: Load everything
Phase 2: Six adversarial passes (the specialists)
Phase 3: Refute each finding
Phase 4: Consolidate
```

The original v1.3 review ran each phase via parallel subagents; that
pattern works on smaller-context models but costs ~4-6× tokens
because every agent re-reads the codebase from scratch. **On a
large-context model that can hold the whole codebase at once
(roughly: anything with a 500k+ token window for a project this
size), Phase 1 happens once and Phases 2-4 reuse that loaded context.
This is the version of the recipe to use now.**

---

## Phase 1: Load everything

Load the entire source tree, all spec docs, the README, CHANGELOG, and
CLAUDE.md / contributor guides into context. On a 26.5k-LOC project
that's roughly 150-250k tokens depending on encoding; well within a
1M context window.

For gnucash-mcp specifically:

```
src/gnucash_mcp/**/*.py        # ~16k LOC main source
tests/                         # ~28k LOC tests (sample, don't read all)
specs/*.md                     # design docs and prior reviews
CLAUDE.md, CLAUDE.local.md     # contributor guides
README.md, CHANGELOG.md        # external surfaces
pyproject.toml                 # versions & dependency pins
```

Then list any predecessor reviews. Read their headlines. **Do not** let
their conclusions anchor your own — they were written by reviewers
who had the same blind spots you're trying to compensate for. The
right use of prior reviews is to see what *kind* of things were
flagged and to deliberately look for what was missed.

After loading, write a one-paragraph "what I'm holding" note to
yourself — file inventory, rough size of each module, places where
prior reviews said "this looks right" (those are your priority
targets to disbelieve).

---

## Phase 2: Six adversarial passes

Each specialist gets its own section in a working file (e.g.
`specs/CODE_REVIEW_<version>_pass_<N>.md`). Don't merge them yet —
keep the perspectives clean. Run them in any order; the order below
is roughly cheapest-first.

For each pass, output structure:

```
## Pass <N>: <name>
### Mindset (one sentence)
### Findings
- <severity> <id> — <one-line>
  - file:line
  - quote (5-15 lines)
  - bug (what's wrong, with a concrete scenario)
  - fix (specific, not "consider doing")
  - mark CONFIRMED if you walked the code; mark NEEDS_VERIFICATION otherwise
### Considered and ruled out (with citations)
```

### Specialist 1: Math walker

**Mindset:** Assume every report is wrong. Don't read it line by
line; derive what the *output* should be from first principles, then
trace backward and see if the code produces that output on a concrete
input.

**Method:** For each report function, pick a concrete book state
(numbers, dates, currencies, accounts). Compute what the report
should return. Then read the code and trace what it actually returns.

**Target list for gnucash-mcp:**

- balance_sheet, net_worth, cash_flow
- spending_by_category, income_by_source
- debt_payoff_plan
- get_budget_report
- vendor_spending_report, get_outstanding_invoices, get_job_report
- calculate_lot_gain
- get_book_summary trajectory anchors

**Concrete scenarios to walk for each:**

- Same-day boundary transactions
- Future-dated transactions and future-dated prices (different rules)
- Voided transactions in the period
- Cross-currency: aggregate over accounts in 2+ currencies, rate moved
  between as-of and today
- Partial sales in a lot, voided buy in a lot
- Empty book, single-transaction book, leap-year boundaries

**Things that should make you suspicious:**

- A function that takes `as_of` but only uses it in *one* place
  inside the function
- A `_rates_as_of(book)` call with no second argument when the
  surrounding function has an as_of parameter
- A helper called `_latest_*` used inside a function with `_at(date)`
  semantics
- Code that says "this is fine because helper X handles it" — read
  helper X; don't trust the comment

### Specialist 2: Anti-pattern hunter

**Mindset:** Grep aggressively. For each known bug category, find
every match and classify each as BUG / DEFENSIBLE /
NEEDS_VERIFICATION.

**Patterns to grep (this list is gnucash-mcp-specific; generalize
when porting):**

1. `split.quantity` summed without conversion across accounts
2. `book.prices` walked without `_is_market_price` filter
3. `==` on Decimal where comparison should be tolerant
4. `float(`, `/`, `math.` on monetary values
5. `< as_of` vs `<= as_of` (off-by-one boundaries)
6. `for x in book.<plural>:` linear scans where indexed query would
   work
7. `book.session.add()` after relationship-mediated parent assignment
   (orphan inserts)
8. `book.flush()` mid-transaction-build (NOT NULL on orphan Splits)
9. Iteration over `book.accounts` without `_template_account_guids`
   filter
10. `book.accounts(fullname=name)[0]` CallableList slot-assert bug
11. Raw SQL with f-string / `.format()` interpolation of user input
12. `except Exception:` swallowing real failures
13. `os.path` operations on user-supplied paths
14. Missing `force=False` default on destructive ops
15. Reconciled-split protection bypass
16. `_resolve_account` bypassed by direct `book.accounts.get()`
17. Slot writes with non-string non-int values
18. Audit-log staging not consumed
19. Write paths that don't call `_verify_*`
20. `@audit_log` decorator absent on tools
21. Mutable default arguments
22. Test-only imports in production code
23. Hardcoded "USD"
24. `Decimal(float)` construction
25. `datetime.now()` / `date.today()` at the wrong layer

**Generalizing this list:** the patterns are the failure modes of the
specific libraries and domain (piecash, accounting math). When
porting this methodology to another project, write a fresh list of
"things that could plausibly bite us if anyone wasn't careful." That
list is the value here, not the specific 25.

### Specialist 3: State / concurrency / integrity hunter

**Mindset:** Stop trusting the comments that say "this invariant
holds." Find sites where state could be corrupted under realistic
conditions.

**Specific probes for gnucash-mcp:**

- **Threading-local audit staging.** `_stage_audit_before` /
  `_consume_audit_before`. What happens if a tool stages but raises
  before consume? Nested tool calls? Async race? The MCP server's
  thread model matters.
- **Write verification coverage.** Enumerate every `book.save()`
  site; cross-reference to `_verify_*` calls. Anywhere a save lacks
  verification is either (a) a documented ORM-trust path or (b) an
  invariant gap.
- **Voided-split semantics.** The claim is `state=='v' ⇒ value==0`.
  Find sites that filter on one without the other. Construct a state
  where they disagree — could that ever happen via a partial-failure
  path?
- **Two-session writes.** Find every place that opens the book twice
  in one logical operation. If session 1 commits and session 2
  fails, what state is left?
- **Lot accounting under partial / voided / negative-quantity
  conditions.**
- **Lock handling.** What happens when `gnclock` is stale? When the
  retry exhausts? When the process is SIGTERM'd mid-context?
- **Backup race conditions.** Concurrent backups, prune-while-create,
  disk full mid-write.

**Things that should make you suspicious:**

- Any helper that's called *outside* the session block (then again
  inside)
- Try/finally without explicit rollback in the failure path
- Audit-log entries that go through a different code path than the
  underlying write
- `_verify_*` happening AFTER the session close (reads stale state)

### Specialist 4: Security / input boundary hunter

**Mindset:** The MCP server exposes 100+ tools to a partially-trusted
LLM client. The LLM follows the user but can hallucinate parameters
or be prompt-injected via book content (memos, descriptions, vendor
names). The attacker model: a malicious string in a transaction
description, vendor name, slot value, or imported CSV cell.

**Probes:**

- **Path handling.** Every place a user-influenced string becomes
  part of a file path. `..` segments, symlinks, absolute paths,
  trailing newlines.
- **SQL via `text()`.** Every raw-SQL call. Is any value interpolated
  via f-string / format instead of bound parameter?
- **Resource exhaustion.** Tools that return aggregates without
  limits. Slot values without size caps. Free-text fields without
  size caps. Recursive operations without depth limits. Backup
  operations that could spawn unboundedly.
- **Error leakage.** Does `safe_tool` return tracebacks? Absolute
  paths? GUIDs that shouldn't be revealed?
- **Force-flag semantics.** `force=True` somewhere — does it bypass
  more than it should?
- **Audit-log itself.** Can an attacker write to it? Read from it via
  a tool? Could a prompt injection survive a round-trip through the
  audit log?
- **CSV/OFX/XML import paths.** Major attack surface if present.

**Things that should make you suspicious:**

- A path constructed with `Path(...) / user_string` — `pathlib`'s `/`
  doesn't collapse `..`
- An f-string inside a `text(...)` SQL call
- A tool that accepts a freeform string and renders it into a shell
  command (`restore_hint` etc.)
- A length check that gates only `not value.strip()` and nothing else

### Specialist 5: Cross-tool agreement hunter

**Mindset:** Two tools, asked the same question about the same data,
should return the same answer. Unit tests can't catch divergences
because each tool's test isolates that tool.

**Method:** Build a matrix of (tool A, tool B, common quantity).
For each cell, ask: would they agree on Alex's book today? On a
historical date? On a multi-currency book?

**Specific agreement matrices for gnucash-mcp:**

- balance_sheet ↔ net_worth ↔ get_book_summary on net worth
- cash_flow ↔ spending_by_category on total outflows
- cash_flow ↔ income_by_source on total inflows
- get_outstanding_invoices ↔ balance_sheet on total A/R
- get_budget_report ↔ spending_by_category on category totals
- get_book_summary backlog ↔ get_unreconciled_splits on count
- get_latest_price ↔ get_prices ↔ list_commodities on "latest"
- _budget_headline ↔ get_budget_report on used %

**Things that should make you suspicious:**

- Two tools that look like they should use the same helper but route
  through different code paths
- Date-handling that differs subtly between tools (past vs today vs
  future-dated)
- Filters applied at one site but not the other (placeholders,
  templates, voided)
- One tool that converts currencies and a sibling that doesn't

### Specialist 6: Completeness critic

**Mindset:** Don't find bugs. Find dimensions that *weren't searched*.

**Method:** Read the prior pass reports. For each "what looks right"
claim, ask "is that claim verified, or asserted?" For each pass,
ask "what would a skeptic on this same dimension look for that we
didn't?"

**Probes:**

- Locale (decimal separators, date formats, month names)
- Time zones (`date.today()` is local time; whose local?)
- Daylight saving boundaries
- Test coverage gaps — what's not tested at all?
- Documentation drift — does the README still describe behavior
  that's changed?
- Sample books — do they exercise the current code paths?
- Performance baseline — do we have one?
- Deprecation paths — anything marked deprecated still has callers?
- Claims in CLAUDE.md / contributor guides that aren't independently
  verified (predecessor letters in particular are notorious for
  going stale)

**Output:** Recommend concrete additional probes, not "verify
multicurrency." Actionable: "Run cash_flow on Lin Wei for 2024 and
hand-verify against a spreadsheet."

---

## Phase 3: Refute each finding

After all six passes produce their findings, run a refuter pass.
**Do this even when you're holding everything in one context.** It's
the single highest-yield step in the entire methodology.

**Refuter mindset:** Default to confirmed-if-uncertain. Try to
construct a concrete counter-example that proves the bug is NOT
real. If you can't construct one, the bug stands.

**Method (single-context):** For each finding:

1. Re-read the cited code (it's already in your context — cheap).
2. Construct the most charitable interpretation. Is there a code
   path that makes this safe?
3. Construct the most adversarial input. Does the code handle it?
4. Walk the math on at least one concrete numeric scenario.
5. Verdict: CONFIRMED / REFUTED / UNCERTAIN → CONFIRMED.

**What the refuter step catches:**

- Findings the original pass cited at the wrong line (right pattern,
  wrong site)
- Findings that look like bugs but are deliberate (the source
  comment explains it, e.g., gnucash-mcp's `prune_backups` auto-stages
  behavior)
- Findings that are TRUE but have a different shape than the original
  framing (e.g., the "warning misfires" framing was wrong; the same
  code had a different real bug)
- Findings invented by hallucination

**In the v1.3 run** of this methodology, 23 of ~25 specific
adversarial claims survived the refuter step. One was reframed. One
was partially refuted but a sibling bug was found in the same lines.

---

## Phase 4: Consolidate

One file. Prioritized. With pointers back to the per-pass and
per-refuter detail.

**Recommended structure:**

```
# Code Review — <version>

## Headline (3-5 sentences: what's the bottom line?)
## Ship blockers (must fix before release)
## High-priority (correctness, security, integrity)
## Medium-priority (quality, defensiveness, polish)
## Low-priority polish
## Refactor backlog (post-release)
## Reframing of what the deferential pass claimed
## Suggested order of operations
## What the methodology change taught us
```

The "reframing" section is important. List every "what looks right"
claim from the first deferential read; mark each TRUE / FALSE based
on what survived the adversarial pass. This is the audit trail of
which previously-asserted-safe claims should be retired.

---

## How to do this in a single context window

The original v1.3 run dispatched ~17 subagents (5 deferential + 6
adversarial + 6 refuters + the consolidator). Each subagent re-read
the codebase. That cost roughly 4-6× the tokens of a single
careful linear review.

**The single-context version of this recipe:**

1. Load everything (Phase 1).
2. Run each adversarial pass (Specialists 1-6) as a separate section
   in one working document. Between passes, take a moment to *reset*
   your priors — the math walker's findings should not bias what the
   anti-pattern hunter sees. (In a single context this is harder than
   with separate agents; mitigate by writing each pass's output to
   disk before starting the next, and reading only the spec list for
   the next pass, not the prior pass's findings.)
3. After all six passes, run the refuter pass on each finding.
4. Consolidate.

**Estimated cost vs. the multi-agent version:** roughly 1/5th to
1/4th the tokens, because (a) the code is loaded once, not 17 times,
and (b) cross-references between passes happen inside one context
instead of being re-derived. Wall-clock is longer because the passes
run sequentially, not in parallel.

**Things to watch for in single-context mode that the multi-agent
version handled implicitly:**

- **Anchor bias between passes.** With separate agents, each pass
  starts with a fresh prior. In single-context, you may unconsciously
  carry forward the previous pass's "this part is fine" verdict.
  Mitigation: write each pass's output to disk; do not re-read prior
  passes' findings before starting the next pass.
- **Confirmation drift.** When you cite the same file:line in two
  passes, you're tempted to copy the prior pass's analysis instead of
  re-deriving it. Mitigation: explicitly walk the math fresh each
  time even if the code looks familiar.
- **Refuter contamination.** The refuter step is the most important
  one. Do it last, after all six passes are done — *not* inline after
  each pass. Doing it inline collapses the skeptic and verifier into
  one viewpoint.

**When to fall back to multi-agent:**

- Codebase is too large for one context (rare on modern frontier
  models for projects under ~50k LOC, but real for monorepos).
- You need cross-confirmation specifically — two independent reads
  agreeing is stronger signal than one read repeated.
- You suspect your own model has systematic blind spots (multi-agent
  with mixed models cancels some of this).

---

## What we'd skip from the v1.3 run

When applying this methodology to gnucash-mcp v1.4 (or porting it to
another project), skip these:

1. **The deferential first pass.** The five-dimension deferential
   sweep that opened the v1.3 review found 2 ship blockers; the
   adversarial sweep that followed found 13 more plus 12 high-
   priority items the first pass had positively *asserted* were not
   problems. The deferential pass was wasted work. Start adversarial.
   If you find that the codebase is genuinely fine, the adversarial
   pass will say so — and will have done so via the discipline of
   walking each path skeptically.

2. **Per-finding refuter agents.** In v1.3 we dispatched six refuter
   agents to verify clusters of related findings. On single-context
   models, refute inline as part of Phase 3 — same posture, no agent
   overhead.

3. **Overlap between specialists.** Math walker and cross-tool
   agreement hunter both touched the `_rates_as_of` pattern; state
   hunter and agreement hunter both touched `_resolve_account`
   asymmetry. The cross-confirmation was useful, but if you're
   running single-context you have one perspective anyway — pick the
   specialist whose lens is most natural for each probe and don't
   double-cover.

---

## What surprised us

Two patterns from the v1.3 run are worth flagging because they're
likely to recur on similar reviews:

1. **The "comment-says-X-handles-it" failure mode.** Multiple sites
   in gnucash-mcp had accurate, true comments that answered a
   *different* question than the bug needed answering. Example: the
   comment at `book/reporting.py:447-449` correctly states that
   `_rates_as_of(book)` excludes piecash auto-rates. That comment is
   true. It also distracted three reviewers (including me) from
   noticing that the same call doesn't filter by `as_of` date. The
   comment answered the wrong question. **Lesson:** read code as if
   the comments don't exist. If a comment is necessary to justify
   the safety, the code is suspicious.

2. **The "1/3 cluster".** Of the ~25 adversarial findings, roughly
   1/3 traced back to a single root cause repeated at multiple
   sites. The `_rates_as_of` pattern surfaced in 5 reports. Voided-
   split semantics in 5+ sites. `_is_market_price` filter gaps in 3
   sites. Once you've spotted the pattern, the consolidation work is
   "find every site that does X" — much faster than "find every
   different bug." **Lesson:** when you have 3+ findings that look
   similar, stop and look for the chokepoint. The fix is usually
   "make one helper that's used by all sites" plus a regression test
   that locks the convergence.

---

## The bias signal

The single most important diagnostic from this methodology: **when
your "what looks right" section is longer than your "findings"
section, you weren't being skeptical enough.**

In the v1.3 deferential pass, I wrote 18 bullet points under "what
looks right" and 5 ship/high findings under "concerns." That ratio
was the tell. The adversarial pass inverted it.

If your review produces that ratio, run the adversarial pass before
declaring done. It will either confirm the codebase is genuinely
clean (in which case you've earned the confidence honestly), or it
will expose what your first read missed.

---

## Appendix: ready-to-use specialist prompts

These are the prompts used to dispatch the six adversarial agents in
the original v1.3 run. They're designed for a single-context
self-prompt as much as for agent dispatch — read them as "instructions
you give yourself to enter the right mindset for this pass."

Each lives as a separate file so they're easy to lift wholesale into
another project (rewriting the project-specific probe lists):

- `ADVERSARIAL_PROMPT_math_walker.md`
- `ADVERSARIAL_PROMPT_antipattern.md`
- `ADVERSARIAL_PROMPT_state.md`
- `ADVERSARIAL_PROMPT_security.md`
- `ADVERSARIAL_PROMPT_agreement.md`
- `ADVERSARIAL_PROMPT_completeness.md`

(Generate these as a follow-up if you want them broken out; the v1.3
run's transcript has them in full and they could be lifted from there
verbatim.)
