# Bookkeeper report — native template transactions (feat/scheduled-native-templates)

Run: 2026-09-09/10, book `samples/native-scratch.gnucash` (byte copy of Alex at HEAD; KEEP IT — it is the crash repro), branch `feat/scheduled-native-templates` @ `f28085f`, v1.4.4, 87 tools, bounced. GnuCash 5.12 as the second instrument (Steve at the GUI). Bookkeeper: Abe VII (Cowork).

## Verdict: BLOCKED — native write/read/post round-trips with desktop; migration produces a template desktop cannot open (crash); occurrence math ignores the recurrence rows

Fingerprint confirmed: Alex's 17 inherited schedules read `recipe: legacy`.

## 1. Fresh schedule visible in desktop — PASS
- `create_scheduled_transaction` "BK Native" (monthly from 2026-09-01, Streaming +42.50 memo "probe" / Checking −42.50, description "Native probe", notes "hello"). Verbose: `recipe: native`, paths, memo, notes. Create response `next_occurrence: 2026-09-01` (on the rule).
- Desktop: opened with no notices. SX Editor listed "BK Native"; template ledger showed 42.50 leaving Checking into Streaming, "Native probe" in the payee/description field, "hello" in notes, memo "probe" on the Streaming line.

## 2. Since-Last-Run posts it — PASS (with the known legacy hazard demonstrated)
- SLR offered all 18 schedules as one block (no per-schedule choice); OK'd. Result: exactly one real transaction, `f23fed80` 2026-09-01 "Native probe", Streaming +42.50 memo "probe", Checking −42.50, notes "hello".
- Server afterwards: `last_occurrence 2026-09-01`, `next 2026-10-01`, `instance_count 1`; `create_transaction_from_scheduled(transaction_date=2026-09-01)` refused: "not after last occurrence 2026-09-01 … (possibly by GnuCash desktop)". First time that message described a real event.
- Hazard, as predicted by the plan: SLR advanced all 16 un-migrated legacy schedules with nothing posted; every one lost its overdue state (Estimated Tax → next 2026-10-15). Real for any user until first-write migration happens.

## 3. Desktop-made schedule instantiates here — PASS on recipe, FAIL on date
- Steve created "BK Desktop": start 2026-09-09, **monthly on the 15th**, Dining 25 memo "Date night" / Checking 25, description "Desktop Probe", notes "Notes field", auto-create left on.
- Server verbose: `recipe: native`, splits debit-first with memo, `start_date 2026-09-09`, `next_occurrence 2026-09-09`, `instance_count 1` (no transaction behind it — desktop counts creation).
- `create_transaction_from_scheduled` (no date) posted `260a7007` on **2026-09-09**; splits/memo/notes correct; audit line present.
- **Finding (blocker-class):** raw rows: `schedxactions.start_date 20260909`; `recurrences (mult 1, month, period_start 20260915, weekend_adjust forward)`. Desktop's occurrence anchor is the recurrence row; the server used `start_date` + its own frequency word. It posted a date the schedule never had, and wrote `last_occur 20260909`, so desktop's next SLR will still post 09-15 → double entry. Second specimen: Steve's "BK Linux" carried TWO recurrence rows (monthly on the 20th AND the 5th — GnuCash composite recurrence); the server reported plain `monthly` from 09-20 and would never post a 5th. Occurrence math must walk the recurrence rows (anchor day, mult, period type, weekend adjust, multiple rows), not `start_date`+label. Server-written schedules only agree because the server writes `period_start = start_date` itself.

## 4. Legacy migrates on write — PASS on the server, FAIL in desktop (crash)
- Deviation: SLR in step 2 had consumed Estimated Tax's July occurrence, so the migrating call posted **2026-10-15** (`fc323eae`), not 07-15. Response `template_migrated: true`; audit "recipe migrated to native template rows (GnuCash-readable)"; verbose `recipe: native`, legs debit-first.
- **Desktop: opening "Estimated Tax Payment" in the SX Editor crashes GnuCash 5.12.** (It lists fine; opening the editor crashes.) GnuCash left a stale `gnclock` row (dead pid) that blocked the server; cleared by hand.
- Diff, migrated template vs a fresh native with the same recipe ("BK Diff", left in the scratch book):
  | | migrated (Estimated Tax) | fresh native (BK Diff) |
  |---|---|---|
  | template account name | "Estimated Tax Payment" (SX name) | SX guid string |
  | template account commodity | USD (`5a8d6eb3…`), scu 100 | GnuCash **template** commodity (`13d992f1…`), scu 1 |
  | template split denominators | value/quantity denom 100 | denom 1 |
  | split reconcile_date | `1970-01-01 00:00:00` | NULL |
  | sched-xaction slot frames | correct | correct |
  Migration mutates the legacy template account and reuses its splits; it must rebuild the template account (template commodity, guid name) and splits exactly as `create` does and repoint `template_act_guid`. Prime suspect for the crash: the USD commodity on the template account.

## 5. Delete is safe for target accounts — PASS, one leftover
- `set_account_slot Expenses:Streaming apr=0`; deleted BK Native; `apr` survived. Dining had no slots. Deleted BK Desktop and BK Linux; 17 schedules remain; the three posted transactions remain; only template splits left are Estimated Tax's.
- **Leftover:** deleting BK Linux removed one of its two recurrence rows and orphaned the other (`recurrences` row for `0caa862d…`, anchor 20260905, no schedule). Composite recurrence again, on the delete side.
- Final desktop open not completed (crash in step 4 came first).

## Editorial: `recipe` and `problems`
Keep `recipe` through the migration era and drop it from the default row once every schedule in a book is native — collapse it to a book-level line in `get_book_summary` ("3 schedules on legacy recipe") and keep it in verbose. `problems` earns a permanent place ONLY if it says something a user can act on ("2 recurrence rows, server honors 1"; "template account not desktop-openable"); an empty `problems: []` on every row is noise.

## Standing question — routed around
- Cleared a stale desktop lock by hand after the crash (no server tool; correct given a dead pid, but the doctor-class instance/lock check from Aug 26 keeps earning its place).
- `pgrep` shows two live gnucash-mcp trees under the Claude app plus a third from `~/.local/bin` — the twin situation from 2026-08-26 is back.
- Otherwise nothing; every step ran as written except where desktop state made the written expectation impossible (noted inline).

## Required before merge
1. Occurrence engine reads the recurrence rows (all of them), not `start_date` + frequency label; weekend adjust honored; `frequency` in responses derived from the rows and honest about composites.
2. Migration rebuilds the template container the way `create` does (template commodity, guid-named account, fresh splits) — verified by opening the migrated schedule in the SX Editor.
3. Delete removes every recurrence row for the schedule.
4. Re-run steps 3–5 on a fresh scratch copy after the fixes; plus a new probe: desktop schedule "monthly on the Nth" with N ≠ start day, and one composite (two recurrences).

Signed: Abe VII, bookkeeper. Native round-trip works both ways for server-shaped schedules; desktop-shaped schedules and migrated ones do not yet. Do not merge.

---

# Round 2 — 2026-09-10, branch @ `64e1853`, fresh scratch `samples/native-r2.gnucash` (Alex at HEAD), bounced. Steve at the GUI.

## Verdict: PASS (5/5) — both acceptance gates met. Merge.

## 1. Desktop anchor — PASS
- Steve created "BK Anchor" in the SX editor: start 2026-09-09, monthly on the 15th, Dining 25 / Checking 25, description "Date Night Retro", notes "Dress like the 80s", auto-create off.
- Server: `frequency: monthly`, `start_date 2026-09-09`, `next_occurrence 2026-09-15`. Instantiate (no date) → `63eaffd6` on **2026-09-15**; list then `next 2026-10-15`; disk `last_occur 20260915`, recurrence `(1, month, 20260915, none)`.
- Desktop reopen: Since-Last-Run offered nothing for BK Anchor (Steve's eyes). Gate 1 met.

## 2. Composite — PASS
- "BK Twice": monthly on the 5th AND 20th from 2026-09-01. Server: `frequency: composite (2 rules)`, `next 2026-09-05`. Three no-date instantiations → 09-05, 09-20, 10-05; list then `next 2026-10-20`. Deleted here: `SELECT COUNT(*) FROM recurrences WHERE obj_guid=…` = 0; zero orphan rows book-wide.

## 3. Migration opens in the editor — PASS (gate 2)
- Instantiate legacy "Estimated Tax Payment" (no date) → `ce1c6159` on 2026-07-15 (the real July catch-up on an unconsumed copy), `template_migrated: true`.
- Rebuilt container on disk: template account named `95c4dbce…` (schedule GUID), commodity `template/template`, scu 1; two splits value/quantity 0/1, no reconcile date; old legacy template account gone. Identical shape to a fresh `create`.
- Desktop: SX Editor → Estimated Tax Payment **opened without crash**, showing the correct $4,200 transfer between its two legs (Steve's eyes).

## 4. Weekend adjust — PASS
- Steve set BK Anchor to day 12, weekend adjust "forward". Server label `monthly, weekends forward`; `next 2026-10-12` (a Monday by itself, so not yet exercised). Walked two more: 10-12, 11-12 (Thu); next then **2026-12-14** — the Monday after Saturday Dec 12. Exercised.

## 5. Dashboard line — PASS
`Scheduled: 18 recurring, 13 overdue ⚠ (oldest 48 days), none further due in next 7 days, 16 on legacy recipe (migrates on first write)`. `problems` absent when empty.

## Routed around
Nothing. All writes on `native-r2`; production and the committed samples untouched. Round-1 scratch `native-scratch.gnucash` retained as the crash repro; both scratch files are untracked and can be discarded once the cousin no longer wants the repro.

## Note for the changelog / release notes
The 1.2–1.4.4 legacy shape is invisible-but-advanced to desktop's Since-Last-Run until each schedule's first write migrates it. Users on those versions who open GnuCash and click OK on Since-Last-Run will silently lose the overdue state of every un-migrated schedule (demonstrated round 1). The release note should say: run one instantiation per schedule (or the migration sweep, if one ships) before the next desktop session. Whether an UNTOUCHED legacy template crashes the editor was not tested; if it does, that is a hotfix for 1.4.x, not a 1.5 item.

Signed: Abe VII, bookkeeper. Server-shaped, desktop-shaped, composite, weekend-adjusted, and migrated schedules all agree with GnuCash 5.12 on dates and open in its editor. Merge.

## Addendum (2026-09-10): the real legacy population is the production book
All 8 schedules in Steve's `books.gnucash` read `recipe: legacy` — all written under 1.4.4 this week (7 rebuilt 09-05 after the UCU Checking rename; the biweekly paycheck 09-09). Alex's 17 are generator-made and never mattered to anyone. Consequences:
1. Until migrated, one OK on desktop's Since-Last-Run silently advances all 8 with nothing posted (rent, storage, autopays, paycheck). Standing instruction: Cancel.
2. Migration-on-first-write posts a transaction. Correct for the paycheck (due 09-10) — and that instantiation is the live cross-commodity test (VFIFX leg, quantity 6.1954) the plan marked unit-locked only. Wrong for the other 7 (3 already posted this month, 4 still upcoming): converting them by instantiation means posting early. **Required: a migrate-without-posting path (sweep or per-schedule), so a real book can be converted in one step after the bounce with nothing posted.**
3. Bookkeeper will not touch production schedules until merge + bounce + Steve's go; first action then is the paycheck through the migrated path, verifying the VFIFX leg on disk before anything else.
