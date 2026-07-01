# Code Review — v1.4.0 (v1.3.1..v1.4.0)

Adversarial review of the v1.4.0 release surface, run 2026-07-01
(the day after release). Five lenses over +4,543/−757 source lines
across 28 files: FX/multicurrency math, i18n account resolution,
multi-book + server bootstrap, group-by reporting + dashboard, and
cross-cutting contract seams.

**Verification legend** — every finding carries one of:
- `REPRODUCED` — demonstrated end-to-end with a live script this session.
- `SOURCE-VERIFIED` — the failing code path was re-read line by line
  and the mechanism confirmed; no live repro run.

Findings are ranked. IDs group by lens: MB (multi-book), I18N, GB
(group-by), FX. The chokepoint analysis at the bottom maps findings
to the minimal set of fixes.

---

## Critical

### MB-1 — Failed `switch_book` tears state; retry reports success while writes go to the wrong book
`REPRODUCED` · `src/gnucash_mcp/server.py:786-799`

`_switch_book_impl` assigns `_current_path = target` **before**
`_book = _book_for(target)`, and `_book_for` can raise (the book
constructor does `Path.resolve(strict=True)` — a transiently missing
file: cloud-sync placeholder, external drive, rename). After the
failed call, `_current_path` points at B while `_book` is still A.
On retry, the no-op branch (`target == previous`) returns
`"Already on: B"` **plus book B's real orientation snapshot** — but
never assigns `_book` and never re-activates logging. Every
subsequent read/write silently operates on book A while the caller
has been convincingly told it's on B. Silent wrong-book ledger
writes; the worst failure class this server has.

Fix direction: construct and validate first, assign
`_current_path`/`_book` together only after everything fallible has
succeeded; make the no-op branch verify `_book is _book_for(target)`
(re-asserting logging) instead of trusting `_current_path`.

---

## High

### I18N-1 — Localized FX-account creation collides with an existing localized account; every retry fails identically
`REPRODUCED` · `src/gnucash_mcp/book/business.py:523-580`

Layer 3 of `_get_or_create_fx_account` checks for an existing
account at the **English** canonical path
(`Income:Foreign Exchange Gain/Loss`) but creates a **localized**
leaf — with no sibling-collision check against the localized name it
is about to create. German-chart EUR book with a pre-existing
`Erträge:Realisierter Gewinn/Verlust` (exactly the name this
machinery generates): Layer 0 empty, Layer 2 misses (the
`_FX_NAME_KEYWORDS` list at business.py:306 is English-only), Layer
3's English check misses, and it constructs a duplicate-named
sibling. `book.save()` raises piecash's opaque
"two children with the same name" `ValueError`, nothing persists, so
the slot never self-heals — **every cross-currency `pay_invoice`
fails identically forever**. Introduced by c8c3d48; pre-v1.4 English
books were protected because the checked path equaled the created
path.

Fix direction: before constructing, scan `parent.children` for the
intended (localized) leaf name — adopt it if type/commodity gates
pass and store the slot; raise a clear "pass fx_account" error if
they don't. Also add the 47 `_LOCALIZED_ACCOUNT_NAMES["fx_gain_loss"]`
strings to the Layer-2 match so the machinery can re-find names it
generated itself.

### MB-2 — Shared `GNUCASH_LOG_DIR`: second book gets zero auto-backups; either book's prune deletes the other's snapshots
`REPRODUCED` · `src/gnucash_mcp/book/backup.py:326-337, 475-512, 669-748, 786-805`

`resolve_mcp_dir` returns `GNUCASH_LOG_DIR` verbatim
(logging_config.py:199-201) — identical for every book — and
`_backups_dir()` hangs `backups/` off it. Two consequences, both
demonstrated: (a) `.state.json` is shared and keyed by stage only,
so book A's auto-backup advances all stage timestamps and book B's
`_maybe_auto_backup` never finds anything due — **B runs with no
auto-backup protection at all**; (b) `list_backups()` has no stem
filter (`_FILENAME_RE` stem is `.+`), so prune operations act on the
mixed listing — retention slots are shared and `unlink` can delete
the *other* book's snapshots, including manual "pre-tax-filing" ones.
Filenames embed the stem, so the loss path is starvation + pruning,
not overwrite. This sharpens the v1.5-backlog "log/backup-dir
collision" item — the backup half is materially worse than the known
audit-interleave half.

Fix direction: per-book subdirs under `GNUCASH_LOG_DIR`
(`{log_dir}/{book_name}.mcp/`), or filter list/prune/state by
`self.book_path.stem`. The subdir design fixes MB-3 for free.

---

## Medium

### I18N-2 — Layer 3's canonical-path find bypasses the type/commodity gates, then stores a designation Layer 0 will forever reject
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/business.py:524-531`

A non-default-commodity account sitting at
`Income:Foreign Exchange Gain/Loss` (e.g. EUR-denominated on a USD
book) is adopted unvalidated; `_compute_fx_gain_loss`
(business.py:1005-1008) then books a *default-currency* delta into
it with no conversion ($42 booked as €42 — the exact corruption
Layer 1 rejects loudly). And `_store_designated_account` persists
the designation that `_resolve_designated_account`'s commodity gate
(business.py:388) refuses on every later call — a permanent
store/reject disagreement that silently re-routes through Layer 3,
repeating the mis-booking each time.

Fix direction: apply the same INCOME/EXPENSE + default-currency gate
to the Layer-3 find before returning/storing. Folds into the I18N-1
chokepoint rewrite.

### I18N-3 — English-literal "retirement" classification survived the i18n sweep; localized books overstate runway
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/core.py:544-557`

`_is_in_retirement_subtree` keys on the English word "retirement" in
the fullname; used by low-cash (core.py:667) and the runway liquid
pass (core.py:1218). A zh_CN `资产:投资:退休金` or German
`Aktiva:Altersvorsorge` never matches — penalty-locked retirement
balances count as liquid and `get_book_summary` runway is overstated
(same bug shape the history records for Alex's 401k: 95 → 124 days).
The 8f16fbb sweep removed "mortgage"/"loan" but missed this one; not
flagged as a known deferral in any spec.

Fix direction: the slot-based `is_retirement` flag the docstring
itself proposes (matches the `loan_term_months` precedent), or a
per-locale word table like the Imbalance/Orphan one.

### GB-1 — Single-period and group-by modes use different rate anchors; grand totals disagree on foreign-currency books
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/reporting.py:268-271 vs :378`

Single-period `spending_by_category` / `income_by_source` /
`cash_flow` convert every split at the **range-end** anchor
(`_account_conversion_factors(book, end_date)`); the new group-by
paths convert each split at **its own sub-period's close**
(`factors_by_period`). For any foreign split with rate movement
inside the range, `spending_by_category(jan, jun)` ≠ the grand total
of `spending_by_category(jan, jun, group_by="month")`. Same tool,
same range, two totals — the exact cross-tool-agreement class
`TestCrossToolPriceAgreement` exists to prevent. (Per-period close
is arguably the *more* correct policy for flows; the divergence, not
the policy, is the bug. `vendor_spending_report` is immune — both
modes read the posted ledger.)

Fix direction: a design call. Either single-period adopts per-period
(well, per-split-date) valuation — shifts bookkeeper-validated
numbers — or group-by adopts the range-end anchor for the Total
column semantics. Whichever wins, lock with a mode-agreement
regression test.

### MB-3 — Shared `GNUCASH_LOG_DIR`: audit entries from two books interleave in one daily file under the first book's header
`SOURCE-VERIFIED` · `src/gnucash_mcp/logging_config.py:316-357`

Append-mode, so no data loss, but entries carry no book marker and
the `Book:` header is written only when the file is new — an audit
trail where book B's writes sit under book A's name. The audit log
is the bookkeeper's review surface. `--help` documents
`GNUCASH_LOG_DIR` with no multi-book caveat.

Fix direction: the MB-2 per-book-subdir fix retires this; otherwise
emit a book-boundary marker line on switch.

### MB-4 — `_activate_logging` failure after the singleton repoint: switch succeeds internally but reports failure
`SOURCE-VERIFIED` · `src/gnucash_mcp/server.py:797-801`

`_book = _book_for(target)` precedes `_activate_logging(target)`,
which can raise (`resolve_mcp_dir` `ValueError` on group/world-
writable parent or foreign-uid `.mcp` — plausible when book B lives
in a shared directory and A doesn't). `safe_tool` renders that as an
error, so the user believes the switch failed — but every tool now
operates on B, with audit handlers still attached to A's file and
`_server_state` stale. Mirror image of MB-1.

Fix direction: same chokepoint rewrite as MB-1 — validate the new
book's log dir *before* repointing, or roll back on failure.

---

## Low

### GB-2 — Partial first/last sub-periods render unlabeled as full columns; `Avg` divides by the full period count
`SOURCE-VERIFIED` · `src/gnucash_mcp/_format.py:49-83, 100-140` —
a report starting Jan 15 shows a "2026-01" column half the size of
its siblings with no marker, and the Avg run-rate is dragged by the
partial. Fix: mark partial columns (e.g. `2026-01*`) with a footnote,
and/or compute Avg over complete periods only.

### I18N-5 — Imbalance/Orphan matcher unions all ~100 locale prefixes for every book
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/_base.py:995-1052` — a
legitimate root-level BANK account starting with a common word from
*any* locale (Turkish "Açık…", Vietnamese "Thừa…") is misclassified
as suspense: dashboard warning + silent exclusion from runway/
low-cash, understating liquidity. Fix: require the `-<CUR>` suffix
shape (Scrub.cpp emits `_("Imbalance")-<CUR>`) and/or intersect with
the book's inferred locale.

### I18N-7 — Audit/debug `FileHandler` lacks `encoding="utf-8"`
`SOURCE-VERIFIED` · `src/gnucash_mcp/logging_config.py:339, 373` —
under a C/POSIX locale (common for daemonized MCP servers) audit
lines containing the new localized account names
(`已实现获利(亏损)`, `Erträge:…`) hit `UnicodeEncodeError` inside the
handler and are dropped to stderr. Pre-existing, but v1.4's
localized auto-created names guarantee non-ASCII flows through it.
Fix: pass `encoding="utf-8"` to both handlers.

### MB-5 — Separator-only `GNUCASH_BOOK_PATH` (e.g. `":"`) crashes import with a raw traceback
`REPRODUCED` · `src/gnucash_mcp/server.py:830-835, 1059-1064` — the
all-segments-empty case raises plain `ValueError`, but both guard
sites catch only `_BookPathError`; the designed `SystemExit(2)`
fail-fast never runs. Fix: catch both, or raise `_BookPathError`
for the empty-after-split case.

### MB-6 — Duplicate-filename validation is case-sensitive while switch matching is case-insensitive
`REPRODUCED` · `src/gnucash_mcp/server.py:649-659 vs :769-784` —
`Ledger.gnucash` + `ledger.gnucash` in different dirs validate fine
but every prefix matches both → unconditional "ambiguous" error; the
non-first book is permanently unswitchable. Loud, no data risk.
Fix: compare `path.name.lower()` in the uniqueness check.

### I18N-4 — Locale-table hygiene: three entries never match
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/_base.py:1075, 1088` —
`bg` ASSET has a trailing colon; `hi` ASSET/LIABILITY have trailing
spaces; account names are `.strip().lower()`ed at compare but table
words only `.lower()`ed. Consequence cosmetic (weaker locale votes).
Fix: normalize both sides + correct the three entries.

### I18N-6 — `ambiguous_fx_account` notice names the English path on localized books
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/business.py:510-520` — the
notice claims routing to `Income:Foreign Exchange Gain/Loss` while
the actual destination is the localized leaf; the LLM relays a path
that doesn't exist. Fix: build the message after resolution (folds
into the I18N-1 rewrite).

### FX-1 — Cost-basis fallback legs allow future-dated rates for past legs
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/_currency.py:695-698`,
`src/gnucash_mcp/book/investments.py:620-623` — `_market_value`'s
cost-basis fallback and the lot-decimals conversion call
`_cross_rate(..., as_of=post_date)` with default `allow_after=True`,
while `_rates_as_of` deliberately sets `allow_after=False` for past
anchors. Degradation-path-only inconsistency with the "past anchors
never price off a future quote" convention. Fix: pass
`allow_after=False` when `as_of < today` (or accept and document —
the fallback is already labeled approximate).

### GB-3 — Latent `KeyError` if a bucketed record's period label falls outside the enumerated columns
`SOURCE-VERIFIED` · `src/gnucash_mcp/book/reporting.py:306-309`,
`src/gnucash_mcp/book/business.py:7391-7394` — `period_totals[pl] += v`
indexes a dict enumerated from the requested range; guarded today by
upstream SQL date filtering, but nothing local enforces it. Fix:
`setdefault` or an explicit guard-with-comment.

### MB-7 — The unlocked `_book` global is safe only because the installed MCP SDK runs sync tools inline
`SOURCE-VERIFIED` (latent, currently unexploitable) ·
`src/gnucash_mcp/server.py:574-577` + audit decorator re-fetching
`get_book()` — the atomicity of stage→consume across `switch_book`
is inherited from a dependency's scheduling behavior (standalone
fastmcp 2.x already thread-pools sync tools). Fix: a contract test /
loud comment pinning the assumption, or capture the book instance
once at wrapper entry and thread it through.

## Info

- **MB-8** — hardlinked paths to the same physical book pass
  validation (symlinks are collapsed and rejected; hardlinks aren't),
  yielding two instances / two audit trails of one book. Exotic.
- **I18N-8** — `GNUCASH_LOCALE` is process-global while books are
  per-path; with multi-book, one override forces the same leaf
  language onto all books. Doc note at most.

---

## Verified clean (coverage record)

**FX/multicurrency** (main-session inline read): `_currency.py` in
full — staleness cap/guard/sanity three-band composition, preference
order, inverse-rate guards, `_anchor_for_as_of` forecast convention,
chain/pivot determinism (freshest-worst-leg scoring), provenance
`(via …)` consistency; `_compute_fx_gain_loss` sign quadrants,
quantization, discount-leg drift, missing-rate degradation;
`_convert_invoice_amount` chokepoint + both-legs conversion +
quantize-to-zero refusal + overpayment sign normalization in
`pay_invoice`; posted-ledger principle in
`_posting_split_in_default` / `_bill_amounts_in_default`; lot math
(state-only void filter, posting-date-rate conversion, prorated
remaining basis); budget foreign-target warning + `count(":")` depth.

**Group-by**: period enumeration boundaries (month wrap, quarter
tuple-compare, year clamp), label/bucket agreement by construction,
voided+template filtering in new iteration paths, TSV formatter
math, transfer-filter parity with single-period cash_flow, vendor
group-by mode-consistency (posted ledger both modes).

**Multi-book** (agent, verified): GUID prefix caches are
per-instance and mtime-keyed (no cross-book resolution possible);
audit staging per-instance TLS; designated-account slots per-book by
construction; module-filter contract + 107/108 counts locked;
gnclock per-operation; `os.pathsep` parsing edge cases;
`get_book_summary` marker derives from the live instance (truthful
even in MB-1's torn state).

**i18n** (agent, verified): stale/wrong-type/wrong-commodity slot
fallthrough; slot rollback on failed save; multi-book slot
isolation; `_top_level_account_of_type` determinism + ambiguity
notice; locale-vote edge cases (empty, mixed, custom, SKR03);
47-locale table lockstep; GDATE fix chokepointed at the single raw
read site, outside the broad except; discount-account collision
immunity; English-literal sweep residue (one substantive survivor →
I18N-3).

**Cross-cutting**: contract tests 96/96 green (write-verification
pairing, TOOL_MODULES completeness, dispatcher coverage);
`("transaction", "CREATE_BATCH")` audit formatter present;
`extra="forbid"` patched at `ArgModelBase` (covers all tool models
including batch rows); `_is_voided` state-only (value-zero FX splits
safe in every consumer).

---

## Chokepoint analysis — findings → minimal fixes

| Fix (one branch each) | Retires | Shape |
|---|---|---|
| **1. Transactional `switch_book`**: build + validate everything fallible (instance, log dir) *before* assigning `_current_path`/`_book` together; no-op branch verifies `_book` identity | MB-1, MB-4 | ~30 LOC rewrite of `_switch_book_impl` + regression tests for the torn-state and activation-failure paths |
| **2. Per-book `.mcp` subdirs under `GNUCASH_LOG_DIR`** (`{log_dir}/{stem}.mcp/`) | MB-2, MB-3 | `resolve_mcp_dir` change + migration note (existing flat layouts keep working single-book) + tests for state isolation, list/prune scoping, audit separation |
| **3. FX-account Layer-3 rewrite**: resolve the intended localized leaf name first; scan `parent.children` with type/commodity gates; adopt-or-clear-error; notice built after resolution; localized names added to Layer-2 keywords | I18N-1, I18N-2, I18N-6 | ~60 LOC in `_get_or_create_fx_account` + collision/adoption/wrong-commodity tests on a German-chart fixture |
| **4. Group-by/single-period rate-anchor unification** (design call needed — see GB-1) + partial-period markers | GB-1, GB-2 | Policy decision, then small; lock with a mode-agreement test |
| **5. Retirement classification by slot flag** (`gnc-mcp/is-retirement` or bare `is_retirement` per the slot-key convention) with the English word as a fallback vote, not the test | I18N-3 | ~40 LOC + zh/de fixture tests |
| **6. Small-fix sweep** (one branch): MB-5, MB-6, I18N-4, I18N-7, FX-1, GB-3, MB-7 comment/contract-test | the lows | Each ≤10 LOC |

Items 1–3 are the release-quality gate: 1 protects ledger integrity,
2 protects the backup safety net, 3 un-wedges localized business
books. Item 4 needs Stephen's design call. Items 5–6 are
patch-candidates for 1.4.1 alongside the v1.5-backlog `.gitignore`
item.

---

*Review run 2026-07-01 by Claude (Fable 5), day one back. Two
lenses ran as parallel deep-read agents (multi-book, i18n — both
with live repro scripts); FX, group-by, and cross-cutting were
reviewed inline in the main session. Every agent finding above was
re-verified against source (or carries the agent's reproduction)
before inclusion.*
