# GnuCash Account Naming & Internationalization — Reference

Investigated against the GnuCash `master` source (github.com/Gnucash/gnucash). The goal: know
which account names are locale-dependent so LLM/MCP tooling (which assumes English names like
"Assets" or "Imbalance") can be made locale-robust.

## TL;DR

1. **Account *types* are language-independent enums; account *names* are localized free text.**
   Never key logic off a name. The reliable identifier is `<act:type>` / `GNCAccountType`
   (`ASSET`, `LIABILITY`, `EQUITY`, `INCOME`, `EXPENSE`, `BANK`, `CASH`, `CREDIT`, `STOCK`,
   `MUTUAL`, `TRADING`, `RECEIVABLE`, `PAYABLE`, `ROOT`, …). These strings are stable across
   every locale.

2. There are **two independent translation sources**, and they do **not** always agree:
   - **First-run wizard account charts** come from XML template files
     (`data/accounts/<locale>/acctchrt_*.gnucash-xea`). Each locale's files are translated
     by hand, independently of the message catalog.
   - **Runtime auto-created accounts** (Imbalance, Orphan, Trading, Opening Balances,
     Retained Earnings, Orphaned Gains) are named via `gettext` (`po/<lang>.po`) at the moment
     they're created, using the *running* GnuCash locale.

   Because these are separate, the top-level "Income" account in the German chart is
   **"Erträge"** (from the template) while the `.po` translation of the word "Income" is
   **"Ertrag"**. Do not assume the template name equals the catalog translation.

3. **For gain/loss accounts, prefer the explicit KVP link over any name/type guess** — see the
   dedicated section below. There is no FX/gain *type*; GnuCash files realized gains under
   `INCOME`.

## Auto-created accounts (the ones tools most often hard-code)

Source: `libgnucash/engine/Scrub.cpp`, `libgnucash/engine/Account.cpp`,
`libgnucash/app-utils/gnc-ui-util.cpp`.

| Account | How the name is built | Type assigned | Separator | Parent |
|---|---|---|---|---|
| Imbalance | `_("Imbalance") + "-" + <currency mnemonic>` → `Imbalance-USD` | `ACCT_TYPE_BANK` | `-` (no spaces) | root |
| Orphan | `_("Orphan") + "-" + <currency mnemonic>` → `Orphan-USD` | `ACCT_TYPE_BANK` | `-` (no spaces) | root |
| Orphaned Gains | `_("Orphaned Gains") + "-" + <mnemonic>` → `Orphaned Gains-USD` | `ACCT_TYPE_INCOME` | `-` (no spaces) | root |
| Trading (root) | `_("Trading")`, then `:<namespace>:<commodity>` children | `ACCT_TYPE_TRADING` | n/a | root |
| Opening Balances | `_("Opening Balances")`; multi-currency: `… + " - " + <mnemonic>` | `ACCT_TYPE_EQUITY` | ` - ` (spaces) | Equity-type acct |
| Retained Earnings | `_("Retained Earnings")` | `ACCT_TYPE_EQUITY` | ` - ` (spaces) | Equity-type acct |

Key details:
- The **currency suffix is the ISO mnemonic** (USD, EUR, JPY …) and is **never translated**.
  Only the leading word is localized.
- **Separator differs**: Imbalance / Orphan / Orphaned Gains use a bare hyphen
  (`Imbalance-EUR`); the equity accounts use a spaced hyphen (`Opening Balances - EUR`). The
  unsuffixed form is used when the account currency equals the book's default currency.
- **Imbalance, Orphan, and Orphaned Gains are all direct children of the root account**
  (`construct_account()` / `GetOrMakeOrphanAccount()` both do "Hang the account off the root").
  This makes `parent == root` a locale-stable structural signal. Disambiguate by type:
  `BANK` → Imbalance/Orphan, `INCOME` → Orphaned Gains.
- Trading sub-accounts (`Trading:CURRENCY:EUR`, etc.) use the commodity **namespace** and
  **mnemonic**, which are not translated.

### GnuCash's own code keys off translated names too

`gnc_find_or_create_equity_account()` finds the parent by calling
`gnc_account_lookup_by_name(root, _("Equity"))` and verifying `type == ACCT_TYPE_EQUITY`.
It first tries the raw English msgid, then the translated form, then falls back to the root
account. So GnuCash assumes the top-level Equity account carries the localized word "Equity" —
but it self-heals via the type check and root fallback. Lesson for your tooling: match by
**type first**, optionally use the localized name only as a tiebreaker.

## FX / realized gain-loss account detection

There is **no dedicated account type** for FX or realized gains — GnuCash files them under
`ACCT_TYPE_INCOME`, and the **same account holds both gains and losses** (a loss is negative
income; the default account's description is literally "Realized Gain/Loss"). So type alone
(INCOME) cannot identify a gain/loss account. Use this tiered strategy, most reliable first.

### 1. The KVP slot link — authoritative, locale-proof

Source: `xaccAccountGainsAccount()` (`Account.cpp:4789`). Any account that throws off
lot-based gains stores a **per-currency pointer** to its designated gains account:

```
slot path:  lot-mgmt → gains-acct → <commodity-unique-name>
slot value: GUID of the gain/loss account
```

(`KEY_LOT_MGMT = "lot-mgmt"`, `Account.cpp:68`; `<commodity-unique-name>` is e.g.
`CURRENCY::USD`.) Read this slot on the source account → you have the exact gains account, no
name/type guessing. Collect every account referenced by any such slot across the book → that is
your complete set of in-use gain/loss accounts. piecash exposes account slots, so this is
directly available.

### 2. Split-level linkage — empirical

Source: `Split.cpp` (`PROP_GAINS_SPLIT`, `PROP_GAINS_SOURCE`, ~L95). Individual gains splits
carry:
- `gains-source` KVP → the originating (capital) split
- `gains-split` KVP → reverse pointer
- a `gains` status flag (`GAINS_STATUS_*`)

Find the gains splits, read which account they post to → recovers the gain/loss accounts even on
a book where the slot in (1) was never written.

### 3. Default-account shape — heuristic fallback

When no slot exists, GnuCash auto-creates the account via `GetOrMakeOrphanAccount()`
(`Account.cpp:4745`):
- **Name:** `_("Orphaned Gains") + "-" + <ISO mnemonic>` (bare hyphen) → `Orphaned Gains-USD`
- **Type:** `ACCT_TYPE_INCOME`
- **Parent:** direct child of **root**
- **Description:** `_("Realized Gain/Loss")` ← more stable signal than the name
- **Notes:** localized "Realized Gains or Losses from Commodity or Trading Accounts that
  haven't been recorded elsewhere."

Heuristic: `type == INCOME` **and** `parent == root` **and**
(`name matches ^.+-<ISO4217>$` **or** `description == _("Realized Gain/Loss")`).

### Detection order

`KVP slot (1)` → `gains-source/gains-split splits (2)` → `INCOME + root-child + -CUR name /
"Realized Gain/Loss" description (3)`. Type by itself is far too broad.

### Implications for a server that creates its own FX account

- A hand-rolled `"Foreign Exchange Gain/Loss"` INCOME account is **invisible** to GnuCash's lot
  machinery unless you set the slot. When you create it, also write
  `lot-mgmt/gains-acct/<commodity-unique-name>` on the source account pointing at it. Then
  GnuCash's own scrubbing finds it, you won't double-create against a native
  `Orphaned Gains-CUR`, and any detector resolves it deterministically.
- GnuCash **nets gain and loss into one INCOME account**. Splitting into a gain (INCOME) and a
  loss (EXPENSE) account is a deliberate divergence — fine on the write side, but when *reading*
  a native book do **not** expect the loss half under EXPENSE.
- `Orphaned Gains-CUR` shares the root-child `<word>-CUR` shape with Imbalance/Orphan, so it
  surfaces in the same sweep — distinguish by type (`BANK` vs `INCOME`).

## First-run wizard charts

- Templates live in `data/accounts/<locale>/`. Locales shipped: C, ca, cs, da, de_AT, de_CH,
  de_DE, el_GR, en_GB, en_IN, es_AR, es_ES, es_MX, fi_FI, fr_BE, fr_CA, fr_CH, fr_FR, he, hr,
  hu, it, ja, ko, lt, lv, nb, nl, pl, pt_BR, pt_PT, ru, sk, sv_AX, sv_FI, sv_SE, tr_TR, zh_CN,
  zh_HK, zh_TW (plus C = English source-of-truth). If no chart exists for the user's locale,
  GnuCash falls back to C (English) names.
- Each account in the XML has `<act:name>` (localized, free text) and `<act:type>` (the enum,
  always uppercase English). The internal Root is `<act:name>Root Account</act:name>` and is
  not translated (not user-visible).
- Example, English vs German `acctchrt_common`:

  | English (`C`) | German (`de_DE`) | type |
  |---|---|---|
  | Assets | Aktiva | ASSET |
  | Current Assets | Barvermögen | ASSET |
  | Checking Account | Girokonto | BANK |
  | Savings Account | Sparkonto | BANK |
  | Cash in Wallet | Bargeld | CASH |
  | Liabilities | Fremdkapital | LIABILITY |
  | Credit Card | Kreditkarte | CREDIT |
  | Income | Erträge | INCOME |

  Note these are the **template** strings — independent from the `.po` catalog below.

## Translation reference (from `po/<lang>.po`, gettext msgstr)

These are the **runtime / catalog** translations — exactly what auto-created accounts use, and
the type-label translations GnuCash shows in its UI. (61 message-catalog locales ship in total.)

| English | de | fr | es | it | pt_BR | nl | ru | ja | zh_CN | ko | pl | sv |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Imbalance | Ausgleichskonto | Non soldé | Descuadre | Sbilancio | Desequilíbrio | Niet in balans | Дисбаланс | 貸借不一致 | 不平衡的 | 대차 불일치 | Niezrównoważenie | Obalans |
| Orphan | Ausbuchungskonto | Orphelin | Huérfano | Orfano | Órfão | Verweesd | Упущенный | 不明 | 孤立的 | 고아 | Osierocone | Föräldralös |
| Orphaned Gains | Unverknüpfte Gewinne | Gains orphelins | Ganancias Huérfanas | Guadagni rimasti orfani | Ganhos órfãos | Verweesde resultaten | Упущенная прибыль | 不明な利得 | 孤立的收益 | 버린 이익 | Zyski osierocone | Föräldralösa vinster |
| Realized Gain/Loss | Realisierter Gewinn/Verlust | Gains/pertes réalisés | Ganancias/Pérdidas Ocurridas | Profitti e perdite realizzati | Ganhos e perdas realizados | Gerealiseerde winst/verlies | Реализованная прибыль/убыток | 実現損益 | 已实现获利(亏损) | 실제 이익/손실 | Zyski/straty zrealizowane | Reavinst/-förlust |
| Trading | Devisenhandel | Mouvements | Comerciales | Trading | Comércio | Handelsportefeuille | Торговля | 通貨取引 | 贸易 | 매매 | Handlowe | Handel |
| Opening Balances | Anfangsbestand | Soldes initiaux | Saldos de Apertura | Saldi d'apertura | Saldos iniciais | Beginsaldi | Начальное сальдо | 開始残高 | 期初余额 | 잔고 | Bilanse otwarcia | Ingående saldon |
| Retained Earnings | Einbehaltener Gewinn | Report à Nouveau | Ganancias Retenidas | Utili portati a nuovo | Ganhos retidos | Ingehouden winst | Нераспределённая прибыль | 利益剰余金 | 留存收益 | 이익 잉여금 | Dochody zatrzymane | Balanserade vinstmedel |
| Equity | Eigenkapital | Capitaux propres | Patrimonio | Patrimonio netto | Patrimônio líquido | Eigen vermogen | Собственные средства | 純資産 | 所有者权益 | 자기자본 | Kapitał własny | Eget kapital |
| Assets | Aktiva | Actifs (avoirs) | Activos | Attività | Ativos | Activa | Активы | 資産 | 资产 | 자산 | Aktywa | Tillgångar |
| Liabilities | Fremdkapital | Passifs (dettes) | Pasivos | Passività | Passivos | Vreemd vermogen | Обязательства | 負債 | 负债 | 부채 | Pasywa | Skulder |
| Income | Ertrag | Revenus | Ingreso | Entrate | Receita | Opbrengsten | Приход | 収益 | 收入 | 수입 | Przychody | Inkomst |
| Expenses | Aufwand | Dépenses | Gastos | Uscite | Despesas | Kosten | Расходы | 費用 | 支出 | 비용 | Wydatki | Utgifter |

(All cells verified from the catalog. Regenerate for any of the 61 shipped locales by parsing
`po/<lang>.po` for these msgids.)

## Recommendations for LLM / MCP tooling

- **Identify accounts by `GNCAccountType`, not by name.** Build summaries, "find the Imbalance
  account", root-category detection, etc. off the type enum. This is the only locale-stable key.
- For the special runtime accounts, **match by `type` + `parent == root` + a
  `^<word>(-| - )?<ISO?>` pattern** where `<word>` is resolved from the active locale's `.po`
  (Imbalance/Orphan: BANK; Orphaned Gains: INCOME; equity ones: EQUITY). Don't string-compare to
  the English word.
- For gain/loss accounts, **read the `lot-mgmt/gains-acct` slot first**; fall back to
  gains-source/gains-split splits, then to the INCOME-root-child shape. See the FX section above.
- When you must display or create a localized name, pull it from the catalog for the book's
  locale rather than hard-coding English; and when you create a gains account, set the slot so
  the rest of the system can find it.
- Remember template names (wizard hierarchy) ≠ catalog translations; if you need the user's
  actual top-level account names, read them from the book, don't infer from the language.
