# Review d42400e..e99d46e: input validation, business logic, desktop parity, tool surface

Repro scripts are in this folder. Run each from here with
`uv run --project /Users/stephen/Projects/gnucash-mcp python -W ignore <script>`.
`_fx.py` builds the conftest books (`test_book`, `business_book`) with the suite's pre-upgrade guard switched off.

## Findings

### I-1. SERIOUS, CONFIRMED: two ordinary transaction writers skip the IV-20 text gate (NUL and width included)
- **Where:** `book/core.py:7579` `update_transactions` (line 7624 checks only `num`/`link`) and `book/core.py:5330` `enter_statement` (line 5423 checks only `num`/`link`).
- **update_transactions:** `description` and `notes` never reach `_check_text`. ESC, BEL and backspace are stored, and so are a NUL and a 3000-character description. With the NUL, SQLite's `length()` reads 2, which is the original IV-20 symptom. The single-row `update_transaction` refuses the same text. Repro: `r1_controls.py`, `r1b_nul_width.py`.
- **enter_statement:** a created row's `description` and `notes`, and a claim row's `notes`, land unchecked: ESC, NUL, and over-width text all committed. Only `raw` is caught, through the split-memo check.
- **Dry-run mismatch:** that `raw` check runs only at commit. A dry run classifies the line NEW with no warning, and the commit then rejects it, which contradicts the "dry-run rehearses commit" contract. Repro: `r1d_statement.py`.
- **Fix:** in `update_transactions`' per-row validation, call `_check_text` for `description` (`_TEXT_WIDTH`) and `notes` (`_SLOT_TEXT_WIDTH`). In enter_statement, add the same checks beside the `num`/`link` checks for `description`, `notes` and `raw`, so dry run and commit refuse alike.

### I-2. SERIOUS, CONFIRMED: with "Use Split Action Field for Number" on, the duplicate screen treats engine words in split actions as conflicting numbers, so a real duplicate is committed
- **Where:** `book/core.py:4132` (`cand_nums = [txn.num] + [s.action for s in txn.splits]`). Also `_statement_cand_nums` at `book/core.py:5665`.
- **What happens:** with the option on, the batch screen counts EVERY split action of a candidate as a number. That includes the vocabulary the engine and the server write into actions: "Payment" on the A/R or A/P leg of every payment (gncOwner.c:859 writes it unconditionally), "Buy"/"Sell", and "Lot Link".
- **Result:** a batch row with Num 1234 against a recorded customer payment reads `DADx` (MEDIUM, `review_required`) instead of `DAD` HIGH, and it is created. Two transactions on the date. With the option off, or with no Num on the row, the same row is rejected HIGH.
- **Statement path:** it reads only the statement account's own split action. It is still hit by payments the pre-C67 server wrote on option-on books, which have "Payment" on the bank leg: the line becomes NEW (auto-filled) instead of MATCH.
- **Repro:** `r7_numsignal.py` (dry run, option on vs `--off`), `r7b_commit_and_statement.py` (commit: 2 transactions), `r7b_commit_and_statement_stmt.py` (statement NEW instead of MATCH).
- **Fix:** compare numbers per register, as the statement path does: a row's `act` cell against the candidate's split action on the SAME account, and the row's Num against `txn.num`. Do not count a candidate split's action as a number when it is the payment/lot-link vocabulary on a lot-linked A/R or A/P leg (type `P`/`L` transactions).
  - Desktop translates "Payment", so key on the transaction type, not on the word.

### I-3. MINOR, CONFIRMED: enter_statement claim rows edit a document's read-only posting transaction
- **Where:** `book/core.py:6370` (the claim loop: `_set_num`, `notes`, `doc_link`).
- **What happens:** a claim row writes `num`, `link` and `notes` onto the claimed split's TRANSACTION without `_refuse_posting_record` / `_require_editable`.
- **Example:** a card statement that claims an employee voucher's company-card split (that split lives in the posting transaction) changed the posting's Num from the voucher ID `000001` to `AUTH 88213`, and added a `notes` slot and an `assoc_uri` slot. `update_transactions` refuses the same edit ("posting record … read-only").
- `num` and `link` are new in this diff; `notes` predates it. CLAUDE.md says the posting record is read-only on every path, and `tests/test_posting_record_guard.py` ATTEMPTS has no claim entry.
- With the option on, `_set_num` overwrites the card split's action, which is the document ID by C67.
- **Repro:** `r9_claim_posting.py`.
- **Fix:** when the claimed split's transaction is a posting record, refuse claim-row annotations (`num`/`notes`/`link`, and probably `raw`→memo) with the shared refusal. Reconciling it stays allowed, as in desktop's reconcile window. Add the case to ATTEMPTS.

### I-4. MINOR, CONFIRMED: the rest of the free-text surface still takes control characters
- **Where:** `book/core.py` `create_account` / `update_account` (description, notes); `book/admin.py:157` `set_account_slot` (value); `book/business.py:4624` `create_customer` / `create_vendor` (name, address); `create_invoice` / `create_bill` (notes); `add_invoice_entry` (description); `book/business.py:10293` `create_job` (name); `book/budgets.py:561` `create_budget` (name); `book/scheduling.py:771` `create_scheduled_transaction` (name).
- **What happens:** all of these accepted `\x1b[31m\x08`. Party `notes` is gated (`business.py:4467`). IV-20 named "slot values" explicitly.
- **Repro:** `r1_controls.py` (account fields, slot), `r1c_business.py`.
- **Fix:** route every user free-text field through `_check_text` (a third duplicate of the same rule is the trigger in the house rules), and lock it with a grep test over the write methods' text parameters.
  - Consider also refusing the bidi overrides U+202A–202E and U+2066–2069 in descriptions and memos. They reorder register text the same way they do names (the Trojan-Source class).

### I-5. MINOR, CONFIRMED: `_name_skeleton` refuses names that look different on screen
- **Where:** `book/_base.py:779` (`_name_skeleton`, which drops every Cf character).
- **Refused as a lookalike of an existing sibling** (the earlier name was accepted, the second refused):
  - Persian `می‌خواهم` (ZWNJ, unjoined) vs `میخواهم` (joined).
  - Emoji family `👨‍👩‍👧` (one glyph) vs `👨👩👧` (three).
  - Flag tag sequences `Trip 🏴󠁧󠁢󠁥󠁮󠁧󠁿` (England) vs `Trip 🏴󠁧󠁢󠁳󠁣󠁴󠁿` (Scotland). The tag characters U+E0020–E007F are Cf, so both reduce to a plain black flag.
  - Devanagari half-form `क्‍ष` (ZWJ) vs conjunct `क्ष`.
- The docstring's claim "two names with one skeleton are indistinguishable on screen" is false for all four.
- **Repro:** `r3_lookalike.py`, first block.
- **Fix:** keep the tag block (U+E0000–E007F) out of the drop. Drop ZWJ/ZWNJ only when both neighbours are characters on which they have no visible effect (e.g. Latin/Greek/Cyrillic/ASCII), not next to emoji or joining/Indic scripts.

### I-6. MINOR, CONFIRMED: lookalikes the skeleton misses, and names with no visible characters
- **Where:** `book/_base.py:772` / `book/_base.py:779`.
- **Accepted beside the plain spelling:**
  - `Grocery<NBSP>Store` beside `Grocery Store`.
  - `Groc<U+034F CGJ>eries` (U+034F is category Mn, so not dropped).
  - `Groceries<U+FE0F>` (a variation selector).
  - `Groceries<U+3164>` (Hangul filler).
- **Accepted as visible:** names consisting only of U+3164 or U+2800 (braille blank) pass "no visible characters".
- **Repro:** `r3_lookalike.py`, second block.
- **Fix:** map Zs to a space and drop default-ignorable code points (`Default_Ignorable_Code_Point`: CGJ, variation selectors, U+3164, U+115F/1160, U+FFA0) in the skeleton. Treat U+2800 and the fillers as invisible for the emptiness check.

### I-7. MINOR, CONFIRMED: `move_account` brings a lookalike next to its twin
- **Where:** `book/core.py:7052`.
- **What happens:** the move checks only exact `sibling.name == account.name`.
- **Example:** an existing `Expenses:Holding:Gro<ZWJ>ceries` (desktop-made, or written before this release) moved under `Expenses` beside `Groceries` without objection. The result is two indistinguishable siblings, which `create_account` / `update_account` now refuse.
- **Repro:** `r3_lookalike.py`, last block.
- **Fix:** call `_refuse_lookalike_name(account.name, sibling.name, new_parent)` in the move's sibling loop.

### I-8. MINOR, CONFIRMED (void) / PLAUSIBLE (unvoid, same mechanism, not run): the trading-accounts refusal blocks voiding a desktop-made cross-currency transaction, and names the wrong cure
- **Where:** `book/_piecash_shapes.py:565` (`_transaction_validate`).
- **What happens:** a void zeroes the amounts, which sets piecash's `_recalculate_balance`. The guard then refuses an existing USD/EUR transaction that carries correct trading splits. Every commodity's value and quantity are zero after the void, so `normalize_trading_accounts` would add nothing.
- The message says "Enter it in GnuCash desktop", which is wrong for a void. Unvoid restores amounts that already balance per commodity, by the same path.
- **Repro:** `r8_trading_void.py` (void refused; a description-only edit is allowed).
- **Fix:** let the write through when every non-trading commodity's quantity already nets to zero together with its trading splits, i.e. `calculate_imbalances` shows no per-commodity imbalance, so piecash would write nothing. Or say "void/unvoid it in desktop" when the change is a void.

### I-9. MINOR, CONFIRMED: a forced void is audited exactly like an ordinary one
- **Where:** `logging_config.py:1209` (`_fmt_transaction_void`).
- **What happens:** the formatter renders only reason and before-state. `force=True` and the response's reconciled-accounts `warning` never appear. Delete has a `Forced:` line (`_forced_overrides_line`), and enter_statement prints `(FORCED: …)`.
- **Repro:** `r5_void.py` prints both renderings: identical.
- **Fix:** render a `Forced: reconciled splits in <accounts>` line when `params.force` is set and `after_state.warning` is present.

### I-10. MINOR, CONFIRMED: the unpost read-only note describes a transaction that no longer exists
- **Where:** `book/business.py:8145`.
- **What happens:** with `dialog="Unpost command"` the sentence ends "its register will show this transaction as read-only". Unpost DELETES the posting transaction (repro shows none left).
- **Repro:** `r10_unpost_note.py`.
- **Fix:** give the dialog form a variant for unpost, e.g. "removes a transaction dated … inside the closed period; GnuCash's Unpost command does not check the option either".

### I-11. MINOR, CONFIRMED (message): the FC-20 warning names three account kinds; five are affected
- **Where:** `book/_base.py:2996` and `:3007`.
- **What happens:** both texts say budget amounts "on income, liability, or equity accounts" may carry the wrong sign. `_BUDGET_CREDIT_NORMAL_TYPES` (the sign the 1.5 storage applies, and that a 1.4.x magnitude violates) is {INCOME, LIABILITY, PAYABLE, EQUITY, CREDIT}. A credit-card or A/P budget line written by the old server is just as wrong, and the warning tells the user not to look there.
- **Repro:** `r13_fc20_message.py`.
- **Fix:** say "income, liability, credit card, payable, or equity accounts", or build the list from the set.
- Every other new message checked names a GnuCash concept by type or option name; none depends on an English account name.

### I-12. MINOR, CONFIRMED (tool surface): `list_accounts` does not show a hidden account as hidden
- **Where:** `book/_base.py:1177` (`_account_to_compact_line`).
- **What happens:** after `update_account(hidden=True)`, the listing reads exactly as before (`r4_hidden.py`). Only `get_account` carries `hidden: true`. A caller hiding a set of closed accounts cannot verify the result in the surface it navigates by.
- **Fix:** add `HIDDEN` to the annotation list beside `PLACEHOLDER`.

### I-13. MINOR, CONFIRMED: hiding a parent leaves its children in the reconciliation backlog
- **Where:** `book/core.py:560` (also `:1471`).
- **What happens:** desktop's `xaccAccountIsHidden` (Account.cpp:4157) treats an account under a hidden parent as hidden. The server reads only the account's own flag. After `update_account("Assets:Old Bank", hidden=True)`, `Assets:Old Bank:Savings` (zero balance) still shows "dormant / never reconciled / 2 unreconciled". Hiding the leaf itself excludes it.
- The reading predates this diff, but hiding a parent is now one tool call away, and hiding the parent is how desktop users usually close a group of accounts.
- **Repro:** `r12_hidden_parent.py`.
- **Fix:** an `_is_hidden(account)` helper that walks the parents, used at both sites.

### I-14. NIT, CONFIRMED: the audit line for hiding a visible account reads `Hidden: None → True`
- **Where:** `logging_config.py:1390`.
- **What happens:** the comparison defaults `hidden` to False, but the printed before-value is `before.get(key)`, and `_account_to_dict` omits `hidden` for a visible account.
- **Repro:** `r4_hidden.py`.
- **Fix:** print `before.get(key, False)` for `hidden`.

### I-15. NIT: the GnuCash-made fixture is not quite what File > New plus the hierarchy assistant makes
- **Where:** `tests/fixtures/gnucash_new_book.scm`.
- **What's checked and correct:**
  - The FFI signatures match the 5.12 headers: `qof_session_new(QofBook*)`; `qof_session_begin(…, SessionOpenMode)` with mode 2 = `SESSION_NEW_STORE` (correct for a fresh path); `xaccAccountSetPlaceholder(Account*, gboolean)` as int; `gnc_account_append_child(parent, child)`; `xaccAccountSetType` codes 0/2/4/8/9/10/11/12.
  - The template root comes from `sxtg_book_begin`, and the `versions` rows are GnuCash's (`r11_tables.py`). The fixture works: `tests/test_gnucash_created_book.py` passes, 3 tests.
- **What differs:** the hierarchy assistant's template (`acctchrt_common.gnucash-xea`) gives "Opening Balances" the slot `equity-type = "opening-balance"` (`xaccAccountSetIsOpeningBalance`). This book's account has no such slot, and nothing records the hierarchy-assistant provenance the header comment implies.
- **Fix:** set the slot via `xaccAccountSetIsOpeningBalance(a, 1)` on that account. Alternatively, reword the comment.

## Examined and sound

- **`_check_text` control-character rule:** C0 except TAB/LF/CR, plus DEL and C1, are refused on the create_transaction(s) and update_transaction paths, on split memos, and on statement and batch `num`/`link`. Tab and newline survive in notes.
- **`_validate_account_name`:** refuses the listed invisible and bidi characters; ZWJ/ZWNJ and LRM/RLM stay legal.
- **`update_account` rename:** skips self, checks lookalikes against every sibling.
- **`create_account`:** checks lookalikes.
- **No other user-named account creation path:** the business and scheduling `piecash.Account(` sites create server-named or template accounts.
- **`update_account(hidden)` storage:** writes slot `hidden`="true" (KVP string) or removes it, and the INTEGER column via `_gnc_bool`. This is `xaccAccountSetHidden` → `set_kvp_boolean_path` (Account.cpp:2555, :4151). The SQL backend's CT_BOOLEAN `hidden` column follows the property.
- **Placeholder vs hidden:** piecash's `placeholder` mapping also removes its slot when false, so the two are consistent with each other and with desktop. A slot that disagrees with the column is brought in step. `_account_to_dict` adds `hidden` only when set, and the slot registry lists `hidden` with its citation.
- **void/unvoid notes:** match `xaccTransVoid` / `xaccTransUnvoid` line for line.
  - Void copies notes whenever the slot holds a string (empty included), sets "Voided transaction", and writes `void-reason` and `void-time` (the space-separated UTC form).
  - Unvoid restores former notes only when present, otherwise leaves the void's note, and removes `void-former-notes`, `void-reason`, `void-time` and `trans-read-only`. Verified in `r5_void.py`.
- **`void_transaction` force gate:** the posting-record refusal runs first, then `_require_force_for_reconciled`, the same gate as delete/replace_splits. The tool passes `force` through. No internal caller of `void_transaction` exists that the new gate could break.
- **C67, posting:** every leg (A/R/A/P, each income/expense/tax account total, each card line, "Extra to Charge Card") takes `doc_action`, and the transaction num takes the type when the option is on. This matches every `gnc_set_num_action` call in gncInvoicePostToAccount (gncInvoice.c:1441, 1558, 1666, 1729, 1750).
- **C67, payments:** the bank leg's action is "" with the option on, which is what the dialog's non-NULL empty Num produces (gncOwner.c:836 and engine-helpers.c:116). The A/R or A/P leg, prepayment, discount and FX legs stay "Payment". Lot-link legs use the special branch (action always), so "Lot Link" is right.
- **C67, reading the option:** `_num_is_split_action` matches `qof_book_use_split_action_for_num_field` (exactly "t"). Nothing in the server looks transactions up by num or action value.
- **C67, display and search:** `r6_c67.py` shows the posted and paid rows, and `get_transaction` / list / search show `num`, `#leg`, `tnum`. Search-by-num with the option on also matches split actions, which is desktop's Number/Action find.
- **Read-only notes:**
  - post_document uses the post date.
  - pay_document uses the payment date.
  - apply_credit_note uses the link date.
  - The prepayment settle uses the dates of the payments whose transactions `_offset_lots` / `_reduce_split_to` modify.
  - Unpost uses the previous post date; a None date is skipped.
  - The schedule note is computed in phase 3 and suppressed when the instance was rejected as a duplicate.
- **"Dialog does not check the option" claims:** verified. The only `autoreadonly` readers are the register and the date cell (split-register.c, split-register-load.c, datecell-gnome.c), none of them the business dialogs or Since Last Run.
- **`_book_tables_error`:** returns None for a GnuCash 5.12-made book and for every sample book (`r11_tables.py`). The 2.6 and unknown-versions messages name the cure.
- **utf8mb3 guard (by reading only, no MySQL run):**
  - The statement-target regex covers INSERT and UPDATE.
  - `_first_four_byte_character` walks every parameter shape.
  - The guard is scoped per table, and the listener is per open (a new engine each open).
- **`engine_twin` `iso_date_feature`:** filtered only when named in the debris kinds, with an empty `features` frame dropped. The parity suites pass (`test_parity_prepayment`, `test_parity_posting`: 41 passed).
