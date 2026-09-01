# Bookkeeper Re-Probe — enter_statement round 3 (post-adversarial)

Status: **LOOP CLOSED 2026-08-26 — all probes PASS (P5 re-probe
verified at `19f4a7f`); certification closed whole; Statement
Week: GO. Signed: the bookkeeper.** The hang investigated mid-round was
amended to a Claude Desktop dispatch-layer wedge (a second,
unrelated MCP server hung identically); the gnucash server is
exonerated — it answered every dispatched call in <500ms.
Defense-in-depth roadmap banked from the round: startup PID +
spawn-time logging, a doctor instance self-count, the SAWarning
startup filter, and a commit hash in get_server_config (running
interpreter ≠ HEAD was the round's deployment-gap lesson).

Branch: `feat/enter-statement` at `a10b3a0` (PR #154). Bounce the
server first. Your signoff stands for the workflow; this pass
re-verifies the surfaces that CHANGED under it after a five-agent
adversarial review (nine verified HIGHs, all fixed). Re-read the
`enter_statement` docstring after the bounce — the parameter list
changed.

## ⚠ Breaking change for your procedures file

**`force=true` no longer exists.** It split into two independent
flags (maintainer ruling — the single flag let an untied-base
landing silently disable duplicate detection in exactly the
partial-re-entry scenario the guard exists for):

- `force_base=true` — land onto an untied opening base. The tie
  discrepancy is recorded; **twin detection stays ON**.
- `force_duplicates=true` — create past exact twins you have
  adjudicated as distinct. The base gate stays on.

Update any Statement Week procedure that says `force`.

## Probes

1. **Force independence (the ruling's point).** Re-run your T5-a
   shape with `force_base=true` on a statement that re-prints an
   already-reconciled line as a bare create. Expected: REJECTED,
   the note coaching the match-row no-op — NOT a triple entry.
   Then a clean-base statement with a deliberately duplicate
   create under `force_duplicates=true`: lands, and both the
   summary ("LANDED UNDER FORCE (duplicates)") and the audit
   header ("(FORCED: duplicates)") say so.
2. **The rehearsal is now the landing.** Dry-run a payload with an
   exact unreconciled twin left unclaimed: the line's NOTE should
   carry the commit rejection's coaching VERBATIM ("claim it with
   match=…, or force_duplicates=true"), and the tie footer should
   count it ("N row(s) this payload would refuse at commit").
   Then commit the same payload and confirm the note text matches
   word for word — dry-run and commit now run one shared
   disposition resolver; any wording divergence is a bug.
3. **Claims announce themselves.** A dry-run row carrying a
   `match` cell should read class MATCH with note "will claim
   <guid>" — no refusal language on a row that resolves.
4. **Forced rehearsal.** Dry-run with `force_duplicates=true`:
   guard notes vanish and the projection models the forced
   landing (what commit will actually do), not the unforced one.
5. **[FIXED post-R3 — 30s re-probe]** Your P5: the refund shape.
   Re-run the +104-refund-vs-−104-payment probe: expected
   review_required (MEDIUM, desc+date), signals WITHOUT 'A',
   signed amt columns showing the 208 gap, split_match
   "partial". A true transfer duplicate (no category leg) still
   HIGH-blocks on magnitude — probe that too if quick.
5b. **Candidates table upgrades** (both surfaces): batch
   `duplicates` rows now carry `state` (y/c/f/n), SIGNED amounts
   (a deposit is no longer a payment's twin at delta 0), and
   `cur` names the candidate's currency exactly when its frame
   differs from your proposal's (with `amt_delta` blank there).
   Statement candidates sort nearest-correspondence-first within
   each confidence tier. Multi-line notes/memos from OFX imports
   render as `\n` escapes in ONE row — flag any shattered or
   phantom rows immediately.
6. **Effects footer**: now `account / delta / commodity`, and
   review_required rows are excluded (their deltas are homework,
   not projection). Homework line for evidence-only tables reads
   "No rows need adjudication — the listed candidates are
   evidence only."
7. **Your ensemble-bug class, extended.** The invisible-character
   rejection now covers all ten line-separator exotics and the
   prices TSV too. If your generator can be coaxed into emitting
   one, confirm the error names the character and the correct
   row. Also: the `amt1` lazy header spelling now parses, and a
   typo'd fixed column errors by name.

## What did you route around?

Standing section — anything you worked around instead of
reporting is an unfiled bug report, including anything about the
two-flag force feeling wrong in live use.

## Known residuals (unchanged, documented in spec §11)

±$1 non-zero-delta MEDIUMs on genuinely-new lines (deltas expose
them); 14-day identical-amount payroll below the recurrence
threshold (cadence inference is the v-next); claim rows show
blank cat_new/split_match (their proposal side is genuinely
unknown).
