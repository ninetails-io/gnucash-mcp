# Bookkeeper report — unpriced holdings at cost basis (fix/unpriced-holding-valuation)

Run: 2026-09-20, Alex (`samples/alex-chen-morales.gnucash`, HEAD copy at `/tmp/alex-pre-185.gnucash`), branch @ `407661d`, v1.4.4, 87 tools, bounced. No desktop instrument (per plan). Bookkeeper: Abe VII (Cowork).

## Verdict: PASS (Part A 3/3, Part B 7/7) — merge. Two non-blocking observations at the end.

## Part A — oracles did not move
Capture rig, develop worktree (`d01a7e3`) vs this checkout, same machine:
- alex: 20 files, `diff -ru` empty.
- lin_wei (`--today 2025-12-31 --historical 2025-06-30`): 20 files, empty.
- sabine: 17 files, empty.
(Operator note: the rig's flags must follow `--out`; my first pass put them through a shell variable that split wrong. Not a rig finding.)

## Part B — baseline
A0 = assets 861,481.84 / liabilities 410,768.91 / equity 450,712.93; N0 = 450,712.93 (tool prints `450712.933836`); D0 = `now: USD 450,713`; runway 316,400 liquid. All agree.

| Step | Expected | Observed |
|---|---|---|
| 1–2 Buy 10 ALTX @ 1,000 (2024-03-01) | row `10 ALTX (USD 1,000.00, no price data)`; A0/N0/D0 unchanged; stale-price warning | Row exactly as expected; assets 861,481.84, N 450,712.93, D 450,713 unchanged; `⚠ Stale prices: 8 commodities, 1 with no price on file (ALTX, …)` |
| 3 Sell 9 @ 1,350 (2025-06-01) | +450 all surfaces; row `1 ALTX (USD 100.00, no price data)` | **Fingerprint confirmed.** Assets 861,931.84 (+450), N 451,162.93 (+450), D 451,163; row reads exactly `1 ALTX (USD 100.00, no price data)`; Unrealized G/L 25,966.88 (+450) |
| 4 Sell 1 @ 200 (2025-09-01) | row absent; +550; Unrealized G/L +550 | Row absent (no 0.00 line); assets 862,031.84 (+550), N 451,262.93 (+550), D 451,263; Unrealized G/L 26,066.88 (+550 vs baseline) — noted, not a finding |
| 5 Trajectory vs point | equal at every boundary; 2025-01-01 holds 1,000; 2026-01-01 holds 0 | Series 2025-01-01 `188443.084732`, 2026-01-01 `335002.519525`, 2026-09-20 `451262.933836`; point calls identical to the cent (and to six decimals); 2025-01-01 balance sheet shows `10 ALTX (USD 1,000.00, no price data)` |
| 6 Price 50 today → void step-4 sale → delete price | no change while closed; then `1 ALTX @ 50 (USD 50.00)`, −150 vs step 4; then back to `1 ALTX (USD 100.00, no price data)` | Price alone: N 451,262.93 unchanged. After void: row `1 ALTX @ 50 (USD 50.00)`, assets 861,881.84 (−150), N 451,112.93 (−150), Unrealized G/L 25,916.88 (−150). After delete_price: dashboard `Altcoin: 1 ALTX — no price data (USD 100.00)`, assets 861,931.84, D 451,163 (= step-3 state) |
| 7 Runway | liquid moves by exactly 100 between open-at-basis and closed | Open (post-step-6): `USD 316,850 liquid`; after `unvoid`: `USD 316,950 liquid` — +100, the holding's basis, no `no price data` oddity in the runway text |

Cross-surface agreement held at every step: balance_sheet total, `net_worth`, dashboard `now`, and runway moved together by the same amount every time.

## Routed around
Nothing. Every call ran as the plan wrote it.

## Observations (non-blocking)
1. `net_worth` returns six decimals (`450712.933836`) where every other surface rounds to cents. Harmless for agreement checks (they matched exactly) but inconsistent with the compact-by-default convention; suggest 2 dp on the wire.
2. The dashboard's `Data range` now starts 2024-03-01 (my probe buy) and Checking's reconciliation line says "3 years behind, oldest: 2024-03-01" — correct consequences of a back-dated probe, mentioned so nobody reads them as findings on the restored book.
3. `create_transactions` on the ALTX legs warned "no ALTX/USD rate on file — the exchange rate can't be sanity-checked" each time. Accurate and useful; pre-existing.

## State
Alex carries ALTX (commodity, account, buy, two sells — all unvoided) at end of loop. Restore with `cp /tmp/alex-pre-185.gnucash samples/alex-chen-morales.gnucash` with the server idle, or let the builder regenerate.

Signed: Abe VII, bookkeeper. Cost basis is what an unpriced holding is worth, on every surface, and the oracles didn't blink. Merge.
