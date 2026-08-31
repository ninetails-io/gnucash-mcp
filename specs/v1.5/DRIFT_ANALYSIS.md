# Drift workup — the three demo books, measured 2026-08-30

Read-only analysis of the committed books (scratch copies), feeding
GENERATOR_MODERNIZATION_SPEC's policy constants and repair pass.
Regenerate: the sqlite workup script in the 2026-08-30 session; all
figures in account commodity, non-voided splits.

## Alex (USD, through 2026-07-18) — the flagship pathology

| Account | State | Diagnosis |
|---|---|---|
| Checking | $47,740 | ~3.5× any sane buffer; the pile |
| Savings | $90,000 | Second pile, earning nothing |
| Chase Sapphire | **−$17,881** vs $12,000 limit, 21.49% APR | **OVER LIMIT**; drifting **+$668/mo** (charges $1,368/mo vs fixed $700 "pay-in-full") |
| Business Amex | −$4,047, 24.49% APR | Flow balanced ($375 = $375) but the Aug-2025 late-arc balance was NEVER amortized — a fossil |

**Repair (first continued month +1):** pay Chase in full ($17,881,
"balance payoff — catching up after the summer"); pay the Amex
fossil ($4,047). Checking absorbs both (→ ~$25.8k), then staged
sweeps to buffer over 2 months. **Policy constants:** buffer
$12,000; card payment = statement balance (both cards, true PIF);
monthly surplus → savings 40% / VTSAX 60% quarterly.

## Lin Wei (CNY, through 2026-07-18) — the revolver drifting toward the cliff

| Account | State | Diagnosis |
|---|---|---|
| 支票账户 (Checking) | ¥193,574 | The pile, CNY edition |
| 储蓄账户 (Savings) | ¥150,000 | Pile two |
| 招商银行 card | −¥47,817 of ¥80,000, 18.25% | Drifting **+¥3,164/mo** toward the limit (charges ¥5,014/mo vs ¥1,850/mo payments) |
| 工商银行 card | −¥7,231, +¥522/mo | **Zero payments in 90 days** — nobody pays this card at all |
| 汇丰 HKD card | −¥6,460 | Static |

**Repair:** she is the DELIBERATE revolver (interest burden is
load-bearing for debt demos) — repair to her bound, not to zero.
Pay 招商 down ¥7,817 → ¥40,000 (50% utilization bound); start
工商 monthly payments (catch-up ¥1,500, then ≥ charges); leave 汇丰.
**Policy constants:** buffer ¥40,000; 招商 = revolver at ≤50%
utilization, minimum-plus payments; other cards PIF; thin sweeps
(she stays cash-tight by persona).

## Sabine (EUR, through 2026-07-30) — the healthy control

| Account | State | Diagnosis |
|---|---|---|
| 1100 Postbank | €17,681 | Mild accumulation only |
| Ausgleichskonto-EUR | **€48.50** | The known imbalance blemish |
| Cards | none | — |

**Repair:** smallest arc — a modest sweep policy (buffer €8,000,
surplus → Sparkonto), and the repair narrative CLEARS the €48.50
imbalance with a dated reclassification ("Korrektur — ungeklärte
Differenz aufgelöst") — the i18n oracle gets a clean bill of
health and the dashboard loses its one ⚠ honestly.

## Drift-velocity table (why constants can never work)

| Card | Charges/mo (90d avg) | Fixed payment | Drift/mo |
|---|---:|---:|---:|
| Alex Chase | $1,368 | $700 | **+$668** |
| Lin Wei 招商 | ¥5,014 | ¥1,850 | **+¥3,164** |
| Lin Wei 工商 | ¥522 | ¥0 | **+¥522** |

Every fixed payment is wrong in proportion to how alive the
spending generators are. The policy layer (payment = f(statement
balance)) zeroes this entire table by construction.
