# Adversarial Review — gnucash-mcp 1.5 release candidate

- **Target:** branch `review/v1.5-prerelease-adversarial`, commit `43353a78feaa825d77fea599597fd21fb6249d1f`
- **Date:** 2026-09-30
- **Oracle:** GnuCash 5.12 source at tag `5.12`; `gnucash-cli` 5.12 at `/Applications/Gnucash.app/Contents/MacOS/gnucash-cli`; piecash 1.2.1; SQLAlchemy 1.4.54
- **Method:** the orchestrator read no source. Wave 1 dispatched seven hostile Opus 5.5 adversaries (money math, storage shape, data safety, input validation, business logic, security, file-format/container). Their findings were deduplicated into root-cause clusters. Wave 2 gave every SHIP-BLOCKER and SERIOUS cluster to a fresh Opus 5.5 cross-examiner that saw only file, line, and claim — never the original argument — and was told to confirm or refute by reading the code and reproducing on a temporary book. Nothing was scrubbed: a refuted or narrowed claim stays in the report with the refutation.
- **Scale in the tables:** SHIP-BLOCKER / SERIOUS / MINOR / NIT. Where a cross-examiner used "moderate" or "medium", the entry sits in the MINOR tier and the examiner's word is kept in parentheses — a real defect, not a release gate.

---

## 0. Verdict

**NO-SHIP.**

Seven findings survive cross-examination at SHIP-BLOCKER, and each one was reproduced by a verifier who had not seen the argument:

1. **Invoice math disagrees with GnuCash desktop on a third to nearly half of multi-line taxed invoices** (C1). The server rounds tax per line, half-to-even, on an already-rounded pretax; desktop rounds once per tax account, half-up, on the exact value. Every posted A/R total in that population is wrong money.
2. **Entry discounts are never read** (C2). A desktop-drafted discounted invoice posts at full price; a desktop-posted one reads as partly paid with no payment on file.
3. **An invoice's posting transaction can be voided** (C4a), **rewritten with `force=true`** (C4b), or **re-dated** (C4c, SERIOUS). After a void or a forced rewrite the invoice reports `status: paid` with an empty payments list and drops off the outstanding list. Desktop's `xaccTransVoid` refuses read-only transactions.
4. **`replace_splits` deletes splits without `_strip_guid_slots`** (C4b ii), which on desktop's bidirectional capital-gains pair raises `CircularDependencyError`, and on a one-way `gains-split` silently deletes another transaction's slots.
5. **`unpost_document` ORM-deletes the posting transaction and lot without stripping GUID slots** (C5); piecash's cascade wipes every slot on the invoice, including desktop's document link (`assoc_uri`). User data is lost on a routine path.
6. **`delete_account_slot` on a frame key (`ofx`, `import-map`, `associated-account`, `lot-mgmt`) cascades into the referenced account's slots** (C8 i), contradicting the tool's own docstring.
7. **The database password reaches the LLM transcript, the audit file, and stderr** (C16a) whenever piecash refuses the URI — the classic first-setup typo on the release's headline feature. README promises the password is masked "wherever the server names the book."

The chokepoint rule the codebase is built on ("`_strip_guid_slots` before every ORM delete") is violated at three sites, and the single most common business workflow — post a taxed invoice — disagrees with desktop. The first is a class regression the house tests were supposed to catch; the second is a correctness gap in the release's core promise. Both need fixes and contract tests before 1.5 ships. Everything else in this report is real, but a release with those seven fixed and the SERIOUS tier triaged would be defensible.

---

## 1. Counts

| | |
|---|---|
| Wave-1 findings raised (raw, seven adversaries) | **138** |
| Root-cause clusters after deduplication | 100 |
| Clusters cross-examined (all SHIP-BLOCKER + SERIOUS) | 72 claims |
| CONFIRMED as stated | **41** |
| PARTIAL (mechanism confirmed; consequence or severity corrected) | **30** |
| REFUTED | **1** (C68) |
| Sub-claims refuted inside PARTIAL entries | 12 |
| Surviving SHIP-BLOCKERs | **7** |
| Wave-1 MINOR/NIT not cross-examined (listed in §6) | 43 |
| New side-findings surfaced by cross-examiners (§7) | 13 |
| **Subagent spawns used** | **22** (7 wave-1 + 15 wave-2), every one with `model: "opus"` |

---

## 2. Deduplication map

Wave-1 IDs: MM = money math, SS = storage shape, DS = data safety, IV = input validation, BL = business logic, SEC = security, FC = file format.

| Cluster | Wave-1 sources | Cross-exam group |
|---|---|---|
| C1 tax/line rounding | MM-1, BL-5, BL-6, MM-16 | G1 |
| C2 entry discounts ignored | MM-2, BL-4 | G1 |
| C3 voucher card lines ignored | BL-9 | G1 |
| C10 no child billterm/taxtable at post; totals recomputed | SS-7, BL-8, BL-14 | G1 |
| C4a void posting txn | SS-6, IV-4, BL-2 | G2 |
| C4b replace_splits force / no strip | IV-3, SS-2 | G2 |
| C4c update_transactions re-dates posting | IV-5 | G2 |
| C43 root account resolvable | IV-17 | G2 |
| C5 unpost cascade wipes invoice slots | SS-1, BL-15 | G3 |
| C7 delete_account cascade | SS-3 | G3 |
| C8 delete_account_slot cascade; reserved keys | SS-4, IV-11, FC-12 | G3 |
| C9 credit-note migration flips signs | SS-5, BL-7 | G4 |
| C17 budget feature stamp on pre-3.8 books | FC-1 | G4 |
| C25 neutral time in extreme zones | SS-10, BL-20 | G4 |
| C30 migration without snapshot | DS-6 | G4 |
| C11 no gnclock row taken | DS-1, FC-16 | G5 |
| C27 SX instantiation commits twice | DS-2, SS-12 | G5 |
| C28 post-commit failure creates 0-byte file | DS-3 | G5 |
| C32 assign_split_to_lot commit-then-raise | DS-8 | G5 |
| C29 auto-backup once per process; failure disables | DS-4, DS-5 | G6 |
| C31 shared LOG_DIR keyed on basename | DS-7, SEC-14 | G6 |
| C33 audit dropped when dir gone | DS-9 | G6 |
| C41 audit invents renames | IV-15 | G6 |
| C53 LOG_DIR override skips symlink guard | SEC-5, DS-14 | G6 |
| C13 negative/zero price accepted | MM-6, IV-2 | G7 |
| C12 zero-side cross-currency split | IV-1 | G7 |
| C21 share quantity rounds to zero | MM-7 | G7 |
| C24 multiple same-day prices | SS-9 | G7 |
| C35 stale-FX advice writes self-price | IV-8 | G7 |
| C14 A/R as payment account | BL-1 | G8 |
| C15 apply_credit_note abs() | BL-3 | G8 |
| C22 no received-amount parameter | MM-8 | G8 |
| C37 overpayment advice misbooks | IV-10 | G8 |
| C44 negative-total invoice reads paid | BL-10 | G8 |
| C45–C51 | BL-11, BL-12, BL-13, BL-16, BL-17, BL-18, BL-19 | G9 |
| C18 SX uses any-age rate | MM-3 | G10 |
| C19 formula 100/3 crashes dashboard | MM-4 | G10 |
| C26 template numerics shape | SS-11 | G10 |
| C42 SX dates unchecked | IV-16 | G10 |
| C20 three-decimal currencies | MM-5 | G11 |
| C63 legacy date formats | FC-4 | G11 |
| C64 due date reads 1970 | FC-5 | G11 |
| C23 reconcile postpone not cleared | SS-8 | G11 |
| C34 far-dated statement lines | IV-6, IV-7 | G12 |
| C36 claim-mismatch advice | IV-9 | G12 |
| C38 create_account guid unusable | IV-12 | G12 |
| C39 batch skip not isolating | IV-13, MM-13 | G12 |
| C40 set_budget_amount sign/rounding | IV-14, MM-11 | G12 |
| C16a password in exception | SEC-1 | G13 |
| C16b query-string password | SEC-2 | G13 |
| C52a malformed URI echoed | SEC-3 | G13 |
| C52b argv password | SEC-4 | G13 |
| C59 stderr leaks | SEC-12 | G13 |
| C54–C58, C60 | SEC-6, SEC-7, SEC-8, SEC-9, SEC-10, SEC-13 | G14 |
| C61 double counters | FC-2 | G15 |
| C62 Credit Notes feature | FC-3, SS-16 | G15 |
| C65 caps exceed columns | FC-6 | G15 |
| C66 MySQL collation | FC-7 | G15 |
| C67 Split Action option | FC-9 | G15 |
| C68 trading accounts | FC-10 | G15 |
| C69 read-only threshold | FC-11 | G15 |

---

## 3. SHIP-BLOCKER (confirmed by cross-examination)

### C1 — Invoice tax and line values round half-even per line; desktop rounds half-up once per tax account
- **Sources:** MM-1, BL-5, BL-6, MM-16 · **Cross-exam:** G1 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/business.py:4248, 4287, 4294-4296, 4304-4306, 4327` (`_compute_entry_tax`); summed at `3290-3305`; also `_convert_invoice_amount` `~6386-6389`
- **Code (verbatim):**
```python
            qv = line_value.quantize(quantum)
...
            pretax = line_value.quantize(quantum)
...
                tax_e = (
                    pretax * e["amount"] / Decimal(100)
                ).quantize(quantum)
...
                tax_by_acct[target_acct] = (
                    tax_by_acct[target_acct] + residual
                )
```
- **Desktop reference:** `gncEntryComputeValueInt` computes tax on the exact unrounded pretax; `gncEntryRecomputeValues` rounds each line's net with `GNC_HOW_RND_ROUND_HALF_UP`; `gncInvoiceGetNetAndTaxesInternal` sums unrounded tax per tax account and rounds once, half-up. Desktop has no residual-cent step.
- **Trigger (all reproduced by both waves):**

| Case | Server posts | Desktop posts |
|---|---|---|
| 1 line, 10.00 at 8.25% | 10.82 | 10.83 |
| 3 lines, 1×0.10 at 5% | tax 0.00 | tax 0.02 |
| 1 line, 1×0.125, untaxed | 0.12 | 0.13 |
| 3 lines, 10.00 tax-included at 7% | 30.00 | 30.01 |

- **Cross-exam evidence:** the verifier ran 20,000 random invoices per scenario through the real function. Single-line invoices differ ~0.2% of the time (exact ties). **Multi-line taxed invoices differ 33–45% of the time**; the dominant cause is per-line rounding, not half-even. Also found: for tax-exclusive lines the server taxes the rounded line value where desktop taxes exact `qty × price`. The docstring near line 4203 claims this "matches GnuCash desktop"; it does not. `tests/test_business.py::test_q3_residual_to_largest_rate` locks the wrong behavior in.
- **Falsifying test:**
```python
def test_tax_rounding_matches_gncInvoiceGetNetAndTaxesInternal(business_book):
    for _ in range(3): gb.add_invoice_entry(i, "Income:Sales", "x", "1", "0.10", taxtable="T5")
    assert gb.post_invoice(i, AR, post_date="2026-01-10")["total"] == "0.32"
    for _ in range(3): gb.add_invoice_entry(j, "Income:Sales", "x", "1", "10.00", taxtable="T7", tax_included=True)
    assert gb.post_invoice(j, AR, post_date="2026-01-10")["total"] == "30.01"
    gb.add_invoice_entry(k, "Income:Sales", "x", "1", "0.125")
    assert gb.post_invoice(k, AR, post_date="2026-01-10")["total"] == "0.13"
```

### C2 — Entry discounts (`i_discount`, `i_disc_type`, `i_disc_how`) are never read
- **Sources:** MM-2, BL-4 · **Cross-exam:** G1 **CONFIRMED** (one detail wrong: bills have no discount columns; desktop passes zero discount for bills)
- **File:line:** `src/gnucash_mcp/book/business.py:3254-3308` (`_get_invoice_entries_and_total`); written only at `5775-5780` and `2795-2798`
- **Code (verbatim):**
```python
            else:
                p_num = row.i_price_num or 0
                p_denom = row.i_price_denom or 1
                acct_guid = row.i_acct
                taxable = bool(row.i_taxable)
                tax_included = bool(row.i_taxincluded)
                taxtable_guid = row.i_taxtable
```
- **Desktop reference:** `gncEntryComputeValueInt` ("Step 3: apply discount and taxes").
- **Trigger:** (a) a 1×100.00 line with a 10% PRETAX discount posts A/R 100 / Sales −100; desktop posts 90. (b) With the posting set to 90 (desktop-posted), `get_document` returns `total 100.00, amount_paid 10.00, amount_due 90.00, payments []`.
- **Cross-exam evidence:** confirmed both. The wrong total also feeds `original_amount` in `get_outstanding_documents`, `total_paid` in `pay_invoice`, and the early-payment discount estimate that `apply_discount` validates against — so it can reach a money write. Verifier's minimum fix: refuse to post any line with a nonzero discount.
- **Falsifying test:**
```python
def test_post_honours_desktop_line_discount(business_book):
    ...  # one 1 x 100.00 line
    _sql(book, "UPDATE entries SET i_discount_num=10, i_discount_denom=1")
    r = gb.post_invoice(invoice_id=inv["id"], post_account="Assets:Accounts Receivable", owner_type="customer")
    assert Decimal(r["total"]) == Decimal("90.00")   # today 100
```

### C4a — `void_transaction` voids an invoice's read-only posting transaction; the invoice then reads paid
- **Sources:** SS-6, IV-4, BL-2 · **Cross-exam:** G2 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/reconciliation.py:562-568`; the delete-path guard at `core.py:6661-6687` is not shared
- **Code (verbatim):**
```python
        with self.open(readonly=False) as book:
            transaction = self._find_transaction(book, guid)
            if not transaction:
                raise ValueError(f"Transaction not found: {guid}")

            if any(s.reconcile_state == "v" for s in transaction.splits):
                raise ValueError(f"Transaction {guid} is already voided")
```
- **Desktop reference:** `xaccTransVoid` (Transaction.cpp ~2507-2514): `if (xaccTransGetReadOnly (trans)) { PWARN ("Refusing to void a read-only transaction!"); return; }`.
- **Trigger:** post a $100 invoice → `void_transaction(<posting guid>, reason="oops")` → `status: voided`; `get_document` → `status "paid", amount_paid 100.00, amount_due 0.00, payments []`; `get_outstanding_documents` → 0 documents.
- **Cross-exam evidence:** confirmed. Extra: the void overwrites the invoice's `trans-read-only` reason with "Transaction Voided" (`reconciliation.py:653`); a later unvoid deletes `trans-read-only` entirely (`:807`), so the posting transaction permanently loses its read-only marker. `tests/test_business.py::test_pay_invoice_refuses_voided_posting_transaction` uses this void as setup, treating the state as legitimate. One shared read-only check across void, replace, update, delete fixes C4a, C4b(i), C4c together.
- **Falsifying test:**
```python
def test_void_refuses_invoice_posting(business_book):
    p = gb.post_invoice(i, "Assets:Accounts Receivable", post_date="2026-01-10")
    with pytest.raises(ValueError, match="unpost"):
        gb.void_transaction(p["transaction_guid"], reason="x")
    assert _split_values(gb, p["transaction_guid"]) != [0, 0]
```

### C4b — `replace_splits` rewrites a posting transaction under `force=true`, and deletes splits without `_strip_guid_slots`
- **Sources:** IV-3, SS-2 · **Cross-exam:** G2 **CONFIRMED** (both parts)
- **File:line:** `src/gnucash_mcp/book/core.py:7523-7557`
- **Code (verbatim):**
```python
                raise ValueError(
                    f"Transaction has {'; and '.join(blockers)}. "
                    f"Use force=true to override."
                )
...
                warnings.append(
                    f"Removed splits from lots: "
                    f"{_lot_split_names(in_lots)}. "
                    f"Cost basis tracking affected."
                )
...
            # 6. Delete existing splits
            for split in list(transaction.splits):
                book.delete(split)
```
- **Trigger (i):** on the posting transaction the first call says "splits in lots: Invoice 000001 ... Use force=true to override"; with `force=True` the splits are rewritten, the A/R split leaves the lot, and the invoice reads `status: paid, amount_paid: 100.00, payments: []` with only "Cost basis tracking affected." **Trigger (ii):** split sA carries a `gains-split` GUID slot pointing at split sB in another transaction; after `replace_splits(A)`, sB's slots go from `[('notes',)]` to `[]`. With desktop's two-way `gains-split`/`gains-source` pair (cap-gains.cpp ~816-823) the call raises `sqlalchemy.exc.CircularDependencyError`.
- **Cross-exam evidence:** both reproduced. `delete_transaction` (core.py ~6763) strips first; the replace path does not. On desktop capital-gains sales it fails loudly rather than corrupting.
- **Falsifying tests:**
```python
def test_replace_splits_refuses_invoice_posting_txn(posted_invoice_book):
    book, txn_guid = posted_invoice_book
    with pytest.raises(ValueError, match="unpost_document"):
        book.replace_splits(txn_guid, [{"account": "Assets:Receivable", "amount": "80"},
                                       {"account": "Income:Salary", "amount": "-80"}], force=True)

def test_replace_splits_strips_guid_slots(tmp_path):
    gc, (ta, sa), (tb, sb) = _two_txns(tmp_path)
    _raw_slot(gc, sa, "gains-split", 5, guid_val=sb)
    _raw_slot(gc, sb, "gains-source", 5, guid_val=sa)
    _raw_slot(gc, sb, "notes", 4, string_val="keep")
    gc.replace_splits(ta, NEW_SPLITS)
    assert ("notes", "keep") in _slots_of(gc, sb)
```

### C5 — `unpost_invoice` ORM-deletes the posting transaction and lot without `_strip_guid_slots`; the cascade wipes every slot on the invoice
- **Sources:** SS-1, BL-15 · **Cross-exam:** G3 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/business.py:6853-6858`
- **Code (verbatim):**
```python
            # The transaction delete cascades its splits; the lot is
            # empty now that the posted-state pointers are cleared.
            if txn is not None:
                book.session.delete(txn)
            if lot is not None:
                book.session.delete(lot)
```
- **Cascade mechanism (verified):** `piecash/kvp.py:265-277` — `SlotFrame.slots = relation("Slot", primaryjoin=foreign(Slot.obj_guid) == guid_val, cascade="all, delete-orphan", ...)`; `class SlotGUID(SlotFrame)` inherits it. A GUID slot's "children" are every slot of the entity it points at. The posting transaction and the lot each carry a `gncInvoice/invoice-guid` GUID slot → deleting either wipes the invoice.
- **Desktop reference:** `gncInvoiceUnpost` (gncInvoice.c 1790-1880) destroys the transaction and lot and never touches invoice slots. `assoc_uri` is `GNC_INVOICE_DOCLINK` (gncInvoice.c:87).
- **Trigger:** plain invoice with `assoc_uri='file:///…pdf'` → post → unpost → invoice slots `[]`. Credit note keeps `credit-note=1` and applies-to link (rewritten by hand at 6861-6867) but loses `assoc_uri`. The comment at 6861-6867 shows the code knows about the sweep and repairs only two keys, only for credit notes.
- **Falsifying test:**
```python
def test_unpost_preserves_invoice_slots(tmp_path):
    gc = _posted_invoice(tmp_path)
    _raw_slot(gc, inv_guid, "assoc_uri", 4, string_val="file:///x.pdf")
    gc.unpost_invoice("INV1")
    rows = _slots_of(gc, inv_guid)
    assert ("assoc_uri", "file:///x.pdf") in rows
    assert ("credit-note", 0) in rows
```

### C8 (i) — `delete_account_slot` on a GnuCash frame key cascades into another account's slots
- **Sources:** SS-4, IV-11(b), FC-12 · **Cross-exam:** G3 **CONFIRMED** for (i); see §4 and §5 for (ii) and (iii)
- **File:line:** `src/gnucash_mcp/book/admin.py:175` and `:25`
- **Code (verbatim):**
```python
_SLOT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
```
```python
            del account[key]
            book.save()
```
- **Trigger:** `Assets:OldBank` has desktop's `ofx/associated-income-account` GUID slot → Sales. `delete_account_slot("Assets:OldBank", "ofx")` → `{'status': 'deleted'}`; Sales' slots go from `[('notes',), ('color',)]` to `[]`. `ofx`, `import-map`, `associated-account`, `lot-mgmt` all pass the regex, and `get_account_slots` lists them as deletable keys. `tools/admin.py:94-95` promises only the named key is deleted.
- **Falsifying test:**
```python
def test_delete_account_slot_refuses_or_strips_gnucash_frames(tmp_path):
    gc = _book_with_ofx_assoc(tmp_path)
    with contextlib.suppress(ValueError):
        gc.delete_account_slot("Assets:OldBank", "ofx")
    assert {n for n, *_ in _slots_of(gc, sales_guid)} == {"notes", "color"}
```

### C16a — Database password echoed verbatim to the LLM, the audit log, and stderr when piecash refuses the URI
- **Sources:** SEC-1 · **Cross-exam:** G13 **CONFIRMED** end to end on PostgreSQL
- **File:line:** `src/gnucash_mcp/book/_base.py:1779-1781`; `tools/_helpers.py:487-496`; `logging_config.py:3030-3035`
- **Code (verbatim):**
```python
        except Exception as e:
            logger.error(
                f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}"
            )
            return _json(
                {
                    "error": redact_paths(
                        f"Unexpected error: {type(e).__name__}: {e}"
                    ),
```
piecash `core/session.py:407-412`:
```python
    if check_exists and not database_exists(uri_conn):
        raise GnucashException(
            "Database '{}' does not exist (please use create_book to create "
            ...".format(uri_conn))
```
- **Trigger:** `GNUCASH_BOOK_URI=postgresql://x13probe:S3CRET@localhost:55432/no_such_ledger`, call `get_book_summary`. Tool result: `{"error":"Unexpected error: GnucashException: Database 'postgresql://x13probe:S3CRET@localhost:55432/no_such_ledger' does not exist ...`. Audit file: `ERROR get_book_summary: Database 'postgresql://x13probe:S3CRET@...`. Stderr: full Rich traceback with the password twice. The same response masks the password in the startup notice and the `Book:` header, then prints it in the error text. Contradicts README 368-369.
- **Cross-exam evidence:** with `GNUCASH_REDACT_PATHS=1` the tool result is masked only by accident (the Windows-drive regex eats `l://…`), and `postgresql://u:S3CRET@localhost` still leaks as `postgresqu:S3CRET@localhost`; stderr and the audit ERROR line leak regardless. Redaction is off by default for CLI and Docker; the MCPB bundle cannot serve a database book. The startup health probe swallows the error, so it surfaces on the first tool call. Fix: route `safe_tool` error text, its `logger.*` lines, and the audit `error_text` through the existing `_URI_IN_TEXT_RE → _redact_uri` scrub (`book/core.py:40,57`).
- **Falsifying test:**
```python
def test_open_failure_never_echoes_db_password(tmp_path, monkeypatch, clean_server):
    uri = f"sqlite://u:S3CRET@/{tmp_path}/missing.gnucash"
    monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
    clean_server._install_book_uri(uri, activate=True); clean_server._apply_module_filter("all")
    out = clean_server.mcp._tool_manager._tools["list_accounts"].fn()
    assert "S3CRET" not in out
    assert all("S3CRET" not in p.read_text() for p in (tmp_path/"logs").rglob("*.txt"))
```

---

## 4. SERIOUS (confirmed, or partial with the defect intact)

### C3 — Voucher company-card lines (`b_paytype=2`, `ccard_guid`) post to A/P
- **Sources:** BL-9 · **Cross-exam:** G1 **PARTIAL** — SHIP-BLOCKER → SERIOUS (server cannot create the precondition; fires only when posting a desktop-drafted card voucher)
- **File:line:** `src/gnucash_mcp/book/business.py:6627-6647`
- **Code (verbatim):**
```python
            for acct_guid, acct_total in acct_totals.items():
                entry_acct = book.session.query(
                    piecash.Account
                ).filter_by(guid=acct_guid).first()
```
- **Trigger:** 40 cash + 60 card voucher posts A/P −100; desktop (`gncInvoicePostToAccount`, `GNC_PAYMENT_CARD` branch) posts A/P −40, card −60. `charge_amt` is ignored the same way.
- **Falsifying test:** `test_voucher_card_lines_post_to_employee_ccard` — after post, `r["total"] == "20.00"` and `split_value("Company Card") == Decimal("-300")`.

### C10 — Posting never creates child billterm/taxtable copies; totals are recomputed from live tables
- **Sources:** SS-7, BL-8, BL-14 · **Cross-exam:** G1 **PARTIAL** — SHIP-BLOCKER → SERIOUS (posted splits/lot/due date are correct; damage is to displayed and reported totals; server-side edit is gated by `force=True`)
- **File:line:** `business.py:5710` (`tt_guid = tt_obj.guid if tt_obj else None`), `6394-6729`, `3046-3055`
- **Code (verbatim):**
```python
        if grand_total is not None:
            grand_total = grand_total.quantize(quantum)
        else:
            try:
                grand_total = self._get_invoice_entries_and_total(
                    book, inv,
                )["grand_total"].quantize(quantum)
            except ValueError:
                grand_total = max(amount_due, Decimal("0"))
```
- **Desktop reference:** `gncInvoicePostToAccount` → `gncBillTermReturnChild(..., TRUE)` and `gncTaxTableReturnChild(...)`; children have `parent` set, `invisible=1`. `gncEntryRecomputeValues` re-values on screen when the table's modification time changes.
- **Trigger:** T5 at 5% → 1×100 invoice → post (A/R 105) → pay 105 → `update_taxtable("T5", entries=[10%], force=True)` → `get_document` → `total 110.00, amount_paid 110.00`. The posting transaction stays 105.
- **Falsifying test:** `test_post_stabilizes_terms_and_taxtables` — after post, `SELECT invisible, parent FROM taxtables WHERE guid=(entry.i_taxtable)` is `(1, <parent>)`; `test_amount_paid_equals_sum_of_payments`.
- **Side finding (G1):** `_find_taxtable` (`:1438-1445`) and `list_taxtables` have no `invisible`/child filter, so on books with desktop-posted invoices the server lists duplicate names and a name lookup can attach new draft lines to a frozen child.

### C4c — `update_transactions` re-dates an invoice's posting transaction
- **Sources:** IV-5 · **Cross-exam:** G2 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/core.py:7299-7300`
- **Code (verbatim):**
```python
            if trans_date is not None:
                transaction.post_date = trans_date
```
- **Trigger:** post on 2026-01-15 → `update_transactions("guid\tdate\n<guid>\t2026-03-01")` → `updated`; `transactions.post_date` moves, `invoices.date_posted` stays. The description of the read-only transaction can be edited too. Desktop's register refuses (`split-register-model.c`).
- **Falsifying test:** `test_update_transactions_refuses_posting_txn_date` — result contains "unpost_document".

### C7 — `delete_account` cascades into the slots of any account its GUID slots reference
- **Sources:** SS-3 · **Cross-exam:** G3 **CONFIRMED** (narrow trigger: account must have zero splits yet carry `ofx/…`, `associated-account/…`, `lot-mgmt/gains-acct/…`, or `import-map/…`)
- **File:line:** `src/gnucash_mcp/book/core.py:6656`
- **Code (verbatim):**
```python
            book.session.delete(account)
            book.save()
```
- **Trigger:** empty `Assets:OldBank` with `ofx/associated-income-account` → Sales; `delete_account("Assets:OldBank")` → Sales' slots `[]` (all four key types reproduced, frame contents included).
- **Falsifying test:** `test_delete_account_does_not_touch_associated_account` — Sales keeps `{"notes", "color"}`.

### C8 (ii) — `set_account_slot` accepts `placeholder` / `hidden`, so desktop and server disagree about the account
- **Sources:** IV-11(a), FC-12 · **Cross-exam:** G3 **CONFIRMED**, medium
- **Trigger:** `set_account_slot("Expenses:Groceries", "placeholder", "true")` → `created`; column stays 0, string slot `placeholder='true'`. Desktop reads the slot (`xaccAccountGetPlaceholder`, Account.cpp:4074; string "true" counts as TRUE; the SQL loader applies columns then slots) → placeholder. Server reads the column → keeps posting to it. `hidden` behaves the same.
- **Falsifying test:**
```python
@pytest.mark.parametrize("key", ["placeholder", "hidden", "reconcile-info", "balance-limit", "notes", "color", "ofx", "import-map", "lot-mgmt", "associated-account"])
def test_slot_tools_refuse_gnucash_keys(test_book, key):
    with pytest.raises(ValueError, match="reserved"):
        book.set_account_slot("Assets:Checking", key, "x")
    with pytest.raises(ValueError, match="reserved"):
        book.delete_account_slot("Assets:Checking", key)
```

### C9 — `_migrate_business_shapes` flips the line signs of any unposted all-negative credit note on the next unrelated business write
- **Sources:** SS-5, BL-7 · **Cross-exam:** G4 **PARTIAL** — SHIP-BLOCKER → SERIOUS (each note flips at most once; mixed-sign notes untouched; silent sign change on a financial document; one-line fix)
- **File:line:** `src/gnucash_mcp/book/business.py:2706-2711`
- **Code (verbatim):**
```python
            else:
                qtys = [Decimal(r[1] or 0) for r in rows]
                if all(q > 0 for q in qtys):
                    flip = True
                elif any(q > 0 for q in qtys) and any(q < 0 for q in qtys):
                    unresolved += 1
```
- **Desktop reference:** `gncEntrySetDocQuantity` (gncEntry.c @5.12:567) stores `is_cn ? gnc_numeric_neg(quantity) : quantity` — a −1 line is stored as +1, exactly what the flip treats as legacy.
- **Trigger:** `create_credit_note("CN1")` → `add_invoice_entry("CN1", quantity="-1", price="40")` (stored +1) → post unrelated INV1 → response carries `credit_note_entries_migrated: 1`; CN1's stored quantity is −1 and total goes from −40.00 to +40.00.
- **Cross-exam evidence:** an unused fingerprint exists — v1.4.4 wrote `i_disc_type=""`, `i_disc_how=""`, `b_paytype=0` where desktop and 1.5 write `PERCENT`/`PRETAX`/`1`; gating the flip on `i_disc_type == ''` works in a single pass because the CN pass runs before `entries_normalized`.
- **Falsifying test:**
```python
def test_cn_negative_doc_quantity_survives_other_writes(tmp_path):
    gc.create_credit_note(owner_id="000001", owner_type="customer", credit_note_id="CN1")
    gc.add_invoice_entry("CN1", "Income:Sales", "fee", quantity="-1", price="40")
    _post_unrelated_invoice(gc)
    assert _entry_qty_nums(gc, "CN1") == [1]
```

### C30 — Book-wide irreversible migration runs inside the first converting write with no dedicated snapshot
- **Sources:** DS-6 · **Cross-exam:** G4 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/_base.py:2532`; `backup.py:829-837`
- **Code (verbatim):**
```python
    def _upgrade_book_shapes(self, book) -> dict:
        """Write path only: convert every pre-1.5 private shape in the
        book to GnuCash's own, posting nothing, and say what it did.
```
- **Trigger:** one `create_price` on the HEAD Alex sample converted the whole book in one `book.save()`: 14,650 row-level diffs (~7.3k rows; 3,964 reconcile dates, 2,484 slot fillers, 254 price dates, 17 templates, 108 invoice links, feature stamp). The only backup guarantee is the once-per-process stage check (C29). CHANGELOG line 26 says only schedule/budget/business writes convert; a price write does too. Database books get no snapshot at all.
- **Falsifying test:**
```python
def test_migrating_write_takes_labeled_snapshot(legacy_book):
    B = build_book_class()(legacy_book); B._backup_checked_in_process = True
    B.pay_invoice("000018", "Assets:Current Assets:Checking Account", "1.00", owner_type="customer")
    assert any(e["label"] == "pre-1.5-upgrade" for e in B.list_backups())
```

### C29 — One failed auto-backup disables auto-backup for the process; stages run once per process
- **Sources:** DS-4, DS-5 · **Cross-exam:** G6 **CONFIRMED** (b SERIOUS; a MINOR/docs)
- **File:line:** `src/gnucash_mcp/book/backup.py:829-834`
- **Code (verbatim):**
```python
        with self._backup_check_lock:
            if self._backup_checked_in_process:
                return
            # Flag BEFORE running so a raise here won't cause the
            # audit hook to retry on every subsequent write of the
            # process.
            self._backup_checked_in_process = True
```
- **Trigger (b):** `gnclock` row present → first write's attempt records `failed ... Lock on the file`; remove the row → later writes take no backup, flag stays True; the dashboard's "Close GnuCash and try again" advice cannot clear without a restart. **(a):** `_now_utc` +13h/+8d/+31d on the same instance → no new backup. `docs/RESTORE_FROM_BACKUP.md:118` ("first write of the day") and the manifest contradict the code.
- **Falsifying test:**
```python
def test_auto_backup_retries_after_lock_failure(test_book):
    c.execute("insert into gnclock values('h',1)"); c.commit(); B._maybe_auto_backup()
    c.execute("delete from gnclock"); c.commit(); B._maybe_auto_backup()
    assert B.list_backups()
```

### C31 — Shared `GNUCASH_LOG_DIR` scopes per-book state by basename only
- **Sources:** DS-7, SEC-14 · **Cross-exam:** G6 **CONFIRMED, worse than claimed**
- **File:line:** `logging_config.py:249-252`; `_base.py:~1552`; `backup.py:~630`; `server.py:945-966`
- **Code (verbatim):**
```python
        return base / f"{Path(book_path).name}.mcp"
```
```python
            log_name=f"{db_name}.gnucash",
```
- **Trigger:** `2026/ledger.gnucash` and `2027/ledger.gnucash` under one log dir → same `.mcp` folder, one interleaved audit file under the 2026 header, `prune_backups` on B deletes A's snapshots. **Worse:** B reads A's state file, sees every stage fresh, and takes no backup of its own; B's "newest snapshot" is A's book — following the restore doc puts the wrong ledger in place. PostgreSQL and MySQL books both named `gnucash` (the README's example for both) share `gnucash.gnucash.mcp`.
- **Falsifying test:**
```python
def test_log_dir_scoping_distinguishes_same_named_books(tmp_path, monkeypatch):
    monkeypatch.setenv("GNUCASH_LOG_DIR", str(tmp_path / "logs"))
    assert resolve_mcp_dir(tmp_path/"y2026/ledger.gnucash") != resolve_mcp_dir(tmp_path/"y2027/ledger.gnucash")
    assert resolve_mcp_dir(BookSource.from_uri("postgresql://h1/gnucash").log_name) != \
           resolve_mcp_dir(BookSource.from_uri("mysql://h2/gnucash").log_name)
```

### C41 — The audit log records renames and description clears that never happened
- **Sources:** IV-15 · **Cross-exam:** G6 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/logging_config.py:1084-1102`
- **Code (verbatim):**
```python
    if before and after:
        old_name = before.get("name", "")
        new_name = after.get("name", "")
        if old_name != new_name:
            lines.append(f'{_INDENT}Name: "{old_name}" → "{new_name}"')
        old_desc = before.get("description", "")
        new_desc = after.get("description", "")
```
- **Trigger:** `update_account(name="Expenses:Evil", description="x")` logs `Name: "Evil" → ""`; `placeholder=True` logs `Name: "Other" → ""` and `Description: "keep me" → ""`, and there is no line for the placeholder change that did happen. No test covers `_fmt_account_update`.
- **Falsifying test:**
```python
def test_account_update_audit_only_lists_changed_fields():
    lines = _fmt_account_update({"params": {"name": "Expenses:X"}, "before_state": {"name": "X", "description": "d"},
                                 "after_state": {"placeholder": True, "status": "updated"}})
    assert not any("Name:" in l or "Description:" in l for l in lines)
```

### C13 — `create_price` accepts zero and negative values; valuation uses them
- **Sources:** MM-6, IV-2 · **Cross-exam:** G7 **PARTIAL** — SHIP-BLOCKER → SERIOUS (desktop's price editor has the same gap and its `convert_amount_at_date` would show the same −1,100; explicit bad input; `delete_price` reverses)
- **File:line:** `investments.py:860-885`; `_currency.py:535` (`_offer(p.commodity.guid, p, Decimal(str(p.value)))`); posting reader `~1251-1253` skips `rate <= 0`
- **Trigger:** `create_price("EUR", "CURRENCY", "-1.10")` → `created`; `net_worth` drops 7800 → 5600; summary shows `EUR Bank: 100 EUR @ -1.5 (USD -150.00)`. Two readers disagree.
- **Falsifying test:**
```python
@pytest.mark.parametrize("v", ["0", "-1.5"])
def test_create_price_rejects_non_positive(multi_currency_book, v):
    with pytest.raises(ValueError, match="positive"):
        book.create_price("EUR", "CURRENCY", v, price_date=date(2026,9,1))
```

### C12 — A zero quantity or zero value on a cross-currency CURRENCY split is accepted with no warning
- **Sources:** IV-1 · **Cross-exam:** G7 **PARTIAL** — SHIP-BLOCKER → SERIOUS; the sub-claim "a zero leg is legitimate only on STOCK/MUTUAL" is **refuted** (the server's own legacy realized-FX leg in `pay_invoice`, `business.py:7409-7413`, is a zero-value nonzero-quantity split on a currency account)
- **File:line:** `src/gnucash_mcp/book/core.py:4292`; `_currency.py:973` (`_fx_sanity_warnings` skips zero legs before the no-rate branch)
- **Code (verbatim):**
```python
                if quantity * value < 0:
                    raise ValueError(
                        f"Split for '{ref}': quantity and value "
                        f"must have same sign "
```
- **Trigger:** `create_transactions("...\tc\t2026-01-07\tQ3\t110\tAssets:EUR Bank\t0\t-110\tAssets:Checking\t")` → `created`, `warnings: None`; splits value 11000/100, quantity 0/100; balance sheet shows −$110 unrealized loss, EUR account missing.
- **Falsifying test:**
```python
def test_currency_split_rejects_zero_side(multi_currency_book):
    for amt, qty in (("110", "0"), ("0", "100")):
        with pytest.raises(ValueError, match="quantity"):
            book.create_transaction(date(2026,1,7), "x", [
                {"account": "Assets:Euro Savings", "amount": amt, "quantity": qty},
                {"account": "Assets:Checking", "amount": f"-{amt}"}])
```

### C21 — A share/crypto quantity finer than the commodity fraction is rounded silently, even to zero
- **Sources:** MM-7 · **Cross-exam:** G7 **CONFIRMED, worse than claimed**
- **File:line:** `core.py:4285-4315`; `_base.py:405`; `investments.py:279-280`
- **Code (verbatim):**
```python
            _, stored_quantity = _split_amounts(
                value, quantity, trans_currency, account,
            )
            if stored_quantity != quantity:
                quantity = stored_quantity
```
- **Trigger:** BTC fraction 10000; $2 buy with quantity 0.00003 → stored 200/100 and 0/10000, `created`, no warning. Worse: 0.00005 rounds up to 0.0001 and the implied price written is 20000 instead of 40000 — valuation counts it and it becomes the sanity anchor for the next entry. The server refuses sub-unit money as a typo but rounds sub-unit shares silently.
- **Falsifying test:**
```python
def test_quantity_that_rounds_away_is_refused(test_book):
    with pytest.raises(ValueError, match="finer|unit"):
        gb.create_transaction(..., splits=[{"account":"Assets:Checking","amount":"-2.00"},
                                           {"account":"Assets:BTC","amount":"2.00","quantity":"0.00003"}])
```

### C24 — `_upsert_price` stores several same-day prices for one pair when sources differ
- **Sources:** SS-9 · **Cross-exam:** G7 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/investments.py:535`
- **Code (verbatim):**
```python
        candidates = book.session.query(Price).filter_by(
            commodity_guid=comm.guid,
            currency_guid=resolved_currency.guid,
            source=source,
        ).all()
```
- **Desktop reference:** `add_price` in gnc-pricedb.cpp @5.12: "If this price is of equal or better precedence than the old one, copy this one over the old one." — `if (p->source > old_price->source) ... return FALSE; gnc_pricedb_remove_price(db, old_price);`. One price per pair per day.
- **Trigger:** three sources on 2026-06-01 → three rows at 10:59:00; the winner is the smallest GUID (this run: `user:split-register`, the lowest-precedence source). `test_create_price_notes_when_outranked` locks the multi-row behavior and calls the winner "the GUID draw".
- **Falsifying test:**
```python
def test_one_price_per_pair_per_day(tmp_path):
    for v, s in (("190","user:price"),("191","Finance::Quote"),("189","user:split-register")):
        gc.create_price("AAPL","NASDAQ",v, price_date=date(2026,6,1), source=s)
    assert _rows(gc, "SELECT source, value_num FROM prices") == [("Finance::Quote", 191)]
```

### C35 — The stale-FX error recommends a `create_price` call that writes a USD-in-USD price; `create_price` accepts commodity == currency
- **Sources:** IV-8 · **Cross-exam:** G7 **CONFIRMED** (narrower trigger: breaks when the invoice currency is the book default or neither currency is; the hint happens to be right when the pay account's currency is the default)
- **File:line:** `business.py:6359-6371`; `investments.py:871-876`
- **Code (verbatim):**
```python
                    f"updated retroactively. Either run create_price("
                    f"commodity='{invoice_currency.mnemonic}', "
                    f"namespace='CURRENCY', value='...', "
                    f"date='{as_of}') to add a rate near {as_of}, or "
                    f"pass force=true to proceed with the stale rate.",
```
- **Trigger:** USD invoice paid from EUR account, 20-day-old quote → error names `create_price(commodity='USD', ...)` without `currency='EUR'` → following it persists a USD/USD row → retry fails identically. Adding `currency='EUR'` works. The error also prints the rate as `0.9090909090909090909090909091`.
- **Falsifying test:**
```python
def test_create_price_rejects_self_price(test_book):
    with pytest.raises(ValueError, match="same"):
        GnuCashBook(test_book).create_price("USD", "CURRENCY", "0.91", price_date=date(2026,9,30))
```

### C14 — `pay_document` accepts the document's own A/R (or any A/R/A/P) as the payment account
- **Sources:** BL-1 · **Cross-exam:** G8 **PARTIAL** — SHIP-BLOCKER → SERIOUS; sub-claims **refuted**: INCOME/EQUITY are selectable in desktop too (parity, not a bug); placeholders are already refused by piecash
- **File:line:** `src/gnucash_mcp/book/business.py:7022-7025`
- **Code (verbatim):**
```python
            pay_acct = self._resolve_account(book, payment_account)
            if not pay_acct:
                raise self._account_not_found_error(book, payment_account)
```
- **Desktop reference:** `gnc_payment_set_account_types` (dialog-payment.c:1147-1157): `avi.include_type[i] = !xaccAccountIsAPARType (i);`.
- **Trigger:** `pay_document(id, payment_account="Assets:Accounts Receivable", amount="100")` → `status: "paid"`, outstanding shows 0 documents, A/R still holds 100, Checking unchanged.
- **Falsifying test:**
```python
def test_pay_refuses_apar_transfer_account(business_book):
    with pytest.raises(ValueError, match="A/R|A/P|receivable"):
        gb.pay_invoice(i, "Assets:Accounts Receivable", "100", payment_date="2026-01-11")
```

### C15 — `apply_credit_note` links same-sign lots through `abs()`
- **Sources:** BL-3 · **Cross-exam:** G8 **PARTIAL** — SHIP-BLOCKER → SERIOUS; sub-claim "A/R increases" **refuted** (A/R is 570 before and after; both link splits sit on the same account — the damage is misallocation between lots, and it needs a net-negative document first, which is C44)
- **File:line:** `src/gnucash_mcp/book/business.py:7914-7932`
- **Code (verbatim):**
```python
            # Signed lot balances (A/R positive, A/P negative);
            # abs() gives the available amount on each side.
            cn_remaining = abs(self._calculate_lot_balance(cn_lot))
            target_remaining = abs(
                self._calculate_lot_balance(target_lot)
            )
...
            max_apply = min(cn_remaining, target_remaining)
```
- **Desktop reference:** `gncOwnerAutoApplyPaymentsWithLots`: `if (gnc_numeric_positive_p (left_lot_bal) == gnc_numeric_positive_p (right_lot_bal)) continue;`.
- **Trigger:** $500 invoice; credit note with lines +100 and −170 (net −70, lot +70 debit) → apply accepted → invoice reads 430 due, CN lot +140 listed as `USD -140 credit available`; outstanding sums to 290 while A/R holds 570.
- **Falsifying test:** `test_apply_refuses_same_sign_lots` — `pytest.raises(ValueError, match="sign|same direction|nothing to offset")`.

### C22 — Cross-currency payment derives the bank amount from a quote; no parameter for the amount actually received
- **Sources:** MM-8 · **Cross-exam:** G8 **CONFIRMED**
- **File:line:** `business.py:7104-7106, 7389-7396, 7600-7605`; `tools/business.py:929-943`
- **Code (verbatim):**
```python
            pay_quantity, exchange_rate = _convert(
                payment_amount, pay_acct.commodity,
            )
...
                    "quantity": -sgn * pay_quantity,
...
            if desktop_currency:
                self._record_payment_price(
                    book, inv.currency, pay_acct.commodity,
                    payment_amount, pay_quantity, parsed_date,
```
- **Trigger:** EUR 1000 invoice, 1.12 quote dated 9/3, paid 9/5 → bank leg USD 1120.00, 20.00 FX gain, and a 9/5 `user:xfer-dialog` price at 28/25 (the two-day-old quote re-dated, which also resets the stale-price warning). Desktop's dialog (dialog-payment.c:999-1022) lets the user set the received amount. Workaround: `create_price` with the realized rate first; undocumented.
- **Falsifying test:**
```python
def test_pay_document_accepts_received_amount(eur_invoice_book):
    r = gb.pay_invoice(invoice_id=inv, payment_account="Assets:Checking", amount="900.00",
                       owner_type="customer", payment_account_amount="1003.47")
    assert _bank_delta(book) == Decimal("1003.47")
```

### C37 — The overpayment refusal recommends a credit note, which misbooks cash and income
- **Sources:** IV-10 · **Cross-exam:** G8 **CONFIRMED**
- **File:line:** `src/gnucash_mcp/book/business.py:7167-7182`
- **Code (verbatim):**
```python
                    f"{invoice_id}. Pay at most the outstanding "
                    f"balance. To record a genuine overpayment, pay "
                    f"the outstanding balance and book the excess as "
                    f"a credit note (create_document with "
                    f"document_type='credit_note') so it shows "
                    f"as credit owed to the counterparty rather than "
```
- **Trigger:** $100 invoice, customer sent $120. Follow the text: pay 100, credit note for 20 on Income. Checking +100 (bank has 120), Income −80 (earned 100). Desktop's `gncOwnerApplyPaymentSecs` books the full cash into a payment lot; the excess stays as a pre-payment lot. The server has no pre-payment concept.
- **Falsifying test:** `assert "credit_note" not in overpay_error_text() or "cash" in overpay_error_text()`; better, a prepayment path.

### C45 — Job-attached credit notes crash `add`/`delete` with `KeyError: 3`; `apply` cannot find the target
- **Sources:** BL-11 · **Cross-exam:** G9 **CONFIRMED**
- **File:line:** `business.py:5504, 5544, 5650, 8150, 7786-7788`
- **Code (verbatim):**
```python
            resolved_owner_type = inv.owner_type
```
```python
        cfg = self._ENTRY_CONFIG[owner_type]
```
- **Trigger:** credit note with `owner_type=3` (desktop's shape for a CN on a job) → `add_document_entry` and `delete_document` raise `KeyError: 3` even with `owner_type='customer'`; `apply_credit_note` → `Target document not found`. This is the bug class `_document_owner_clause` exists to prevent.
- **Falsifying test:** `test_job_attached_credit_note_lifecycle`.

### C47 — Early-payment discount is computed on the full subtotal; correct payment refused, 10× discount accepted
- **Sources:** BL-13 · **Cross-exam:** G9 **CONFIRMED, worse than claimed**
- **File:line:** `business.py:1108`, compared at `7267`
- **Code (verbatim):**
```python
        expected = (subtotal * discount_pct / Decimal(100)).quantize(
            _commodity_quantum(inv.currency)
        )
```
- **Trigger:** 1000 invoice, 900 CN applied, "2/10 net 30": paying 98 (correct) is **refused** with "adjust amount to 80.00"; paying 80 books a 20.00 discount. With a 20% term the hint suggested paying "-100.00".
- **Falsifying test:** `test_discount_prorated_to_settled_principal` — 98.00 accepted with `discount.amount == "2.00"`.

### C48 — A document can be created in a currency other than its owner's
- **Sources:** BL-16 · **Cross-exam:** G9 **CONFIRMED** (sub-claim that desktop's payment dialog hides the lot does not hold)
- **File:line:** `business.py:5005-5007`
- **Code (verbatim):**
```python
            if currency:
                currency_obj = self._document_currency(book, currency)
                currency_guid = currency_obj.guid
```
- **Desktop reference:** dialog-invoice.c:413 and :1168 (`gncInvoiceSetCurrency(invoice, gncOwnerGetCurrency(...))`, bug 728074); a desktop unpost/repost silently flips it back to the owner's currency.
- **Falsifying test:** `test_document_currency_must_match_owner` — `pytest.raises(ValueError, match="owner.*currency")`.

### C49 — Unpost is refused whenever a payment or link exists; the prescribed workaround voids a reconciled bank split
- **Sources:** BL-17 · **Cross-exam:** G9 **CONFIRMED**
- **File:line:** `business.py:6824-6828`
- **Code (verbatim):**
```python
            if real_payment_splits:
                raise ValueError(
                    f"{doc_label} {invoice_id} has payments applied. "
                    f"Void payments first, then unpost."
                )
```
- **Desktop reference:** `gncInvoiceUnpost` keeps the payment via `gncOwnerAttachToLot` (becomes a prepayment) and rebuilds links. `void_transaction` proceeds on reconciled splits with only a warning (reconciliation.py:535-539), so the workaround zeroes a real reconciled bank line.
- **Falsifying test:** `test_unpost_with_payment_keeps_payment_as_owner_lot`.

### C50 — Desktop prepayment lots are invisible; the dashboard reports a settled invoice as past due
- **Sources:** BL-18 · **Cross-exam:** G9 **CONFIRMED**
- **File:line:** `core.py:330-332`; no prepayment path in business.py
- **Code (verbatim):**
```python
            posted = book.session.query(Invoice).filter(
                Invoice.date_posted.isnot(None),
            ).all()
```
- **Trigger:** desktop $500 prepayment, then server posts $500 invoice → A/R nets 0, dashboard warns "Past due invoice: Acme … USD 500.00". No tool can consume the lot; desktop's `gncInvoiceAutoApplyPayments` (gncInvoice.c:1783) does at post. The false warning sits in the first-call work queue and invites a duplicate payment.
- **Falsifying test:** `test_prepayment_lot_offsets_invoice`.

### C18 — Instantiating a schedule stores a quantity from any-age, transaction-implied, or forecast rate
- **Sources:** MM-3 · **Cross-exam:** G10 **CONFIRMED** (scope: cross-commodity legs without the server's own `gnc-mcp/quantity` slot, i.e. desktop-authored templates)
- **File:line:** `scheduling.py:1446-1463`
- **Code (verbatim):**
```python
                if rates is None:
                    rates = self._rates_as_of(book, txn_date, txn_currency)
                rate = rates.get(acct.commodity.guid)
                ...
                s["quantity"] = str(
                    (_to_decimal(s["amount"]) / rate).quantize(
                        _commodity_quantum(acct.commodity)
                    )
                )
```
- **Trigger:** only price is a 984-day-old implied EUR/USD row → instantiation stores −100 EUR with no warning; `_convert_invoice_amount` on the same book raises "no matching price within ±90 days". With a price dated today+400, instantiation used the forecast rate. **New:** the instance writes a fresh `type='transaction'` price dated today at the old rate, which makes the dashboard's "Stale price: EUR … 984 days ago" warning disappear — the exact hazard `_convert_invoice_amount`'s comment cites. Desktop (`gnc-sx-instance-model.c` `_get_vars_helper`) makes the user fill in the rate.
- **Falsifying test:** `test_sx_instantiation_refuses_stale_rate` — `pytest.raises(ValueError, match="rate")`.

### C19 — A desktop schedule formula like "100/3" raises `InvalidOperation` and takes down `get_book_summary`
- **Sources:** MM-4 · **Cross-exam:** G10 **CONFIRMED, worse than claimed**
- **File:line:** `scheduling.py:440-445`
- **Code (verbatim):**
```python
        if num and num[3] is not None and num[4] and num[3] != 0:
            value = Decimal(num[3]) / Decimal(num[4])
            # 4250/100 is 42.50, not 42.5 — keep the fraction's
            # precision so amounts round-trip as typed.
            return value.quantize(Decimal(1) / Decimal(num[4]))
```
- **Trigger:** numeric 100/3 (desktop's parser divides exactly, `GNC_HOW_DENOM_EXACT`, then reduces) → `InvalidOperation` in `get_book_summary`, `get_upcoming_transactions`, `create_transaction_from_scheduled`; path `core.py:3395 → 3083 → scheduling.py:1137 → 1078 → 518 → 445`, nothing through `_check_failed`. Realistic: "1234/12" (617/6). **Worse:** `_upcoming_cash_legs` reads every recipe before checking `enabled`, so a disabled schedule kills the dashboard too.
- **Falsifying test:**
```python
def test_desktop_thirds_formula_reads(test_book):
    _sql(book, "UPDATE slots SET numeric_val_num=100, numeric_val_denom=3 WHERE name LIKE '%-numeric' AND numeric_val_num=3333")
    gb.get_book_summary()                                  # must not raise
    assert "33.33" in str(gb.get_upcoming_transactions(days=40))
```

### C34 — `enter_statement` enters and reconciles lines dated decades away from the statement date
- **Sources:** IV-6, IV-7 · **Cross-exam:** G12 **CONFIRMED** (SERIOUS for `enter_statement`; batch part moderate)
- **File:line:** `tools/core.py:624-631`; `book/core.py:5221-5226`; `_generate_warnings` at `core.py:4176-4207` is called only by single-row `create_transaction`
- **Code (verbatim):**
```python
            d = _parse_iso_date(row["date"])
```
- **Trigger:** statement 2026-01-31 with lines dated 2026-01-10, 2062-01-15, 0026-01-20 → dry run `3 NEW … ties`, no warnings; commit → `Reconciled: 3 splits`; the 0026 and 2062 rows carry `reconcile_state='y'`. The dashboard later flags only the 2062 rows.
- **Falsifying test:** `test_statement_rejects_line_after_statement_date` — dry-run warnings mention "2062-01-15".

### C40 (sign) — `set_budget_amount` accepts a negative "magnitude" and stores the flipped target
- **Sources:** IV-14 · **Cross-exam:** G12 **CONFIRMED** (moderate–serious); rounding half is MINOR (see §5)
- **File:line:** `budgets.py:694, 737-740`
- **Code (verbatim):**
```python
            quantized = amount_decimal.quantize(
                quantum, rounding=ROUND_HALF_EVEN,
            )
            amount_num = int(quantized * amount_denom) * sign
```
- **Trigger:** `set_budget_amount(Income:Salary, '-5000', period=1)` → "updated", stores +500000, desktop reads a −5000 income target; `get_budget_report(period='all')` shows `Income:Salary 0` because it cancels a +5000 in period 0. The server's own instructions teach "income -3000".
- **Falsifying test:** `pytest.raises(ValueError)` on `set_budget_amount("B", "Income:Salary", "-5000", period=0)`.

### C16b — `_redact_uri` leaves query-string credentials in plaintext
- **Sources:** SEC-2 · **Cross-exam:** G13 **PARTIAL** — SHIP-BLOCKER → SERIOUS (needs a non-default URI form; `?password=` is a working credential path — verified login)
- **File:line:** `_format.py:849`
- **Code (verbatim):**
```python
        return _parse_book_url(uri).render_as_string(hide_password=True)
```
- **Trigger:** `postgresql://x13probe@localhost:55432/no_such_ledger?password=S3CRET` → startup notice, audit `Book:` header, `get_server_config`, and the backup refusal all show `?password=S3CRET`. `GNUCASH_REDACT_PATHS` has no effect on `_book_display_name`.
- **Falsifying test:**
```python
@pytest.mark.parametrize("uri", ["postgresql:///db?host=/tmp&user=u&password=S3CRET",
                                 "postgresql://u@h/db?sslpassword=S3CRET"])
def test_display_name_masks_query_credentials(uri):
    assert "S3CRET" not in _book_display_name(uri)
```

### C54 — `get_audit_log` returns the absolute book path in its header despite `GNUCASH_REDACT_PATHS=1`
- **Sources:** SEC-6 · **Cross-exam:** G14 **CONFIRMED**
- **File:line:** `logging_config.py:460`; `server.py:1362-1365, 1570`
- **Code (verbatim):**
```python
        book_label = display_name or book_path
```
- **Trigger:** MCPB defaults (redaction on) → `get_audit_log()` → `Book: /private/tmp/.../Jane Doe Client/book.gnucash`. Leaks without the flag too; the docstring at `_format.py:865-871` claims the header is masked. The path is baked into the day file at creation, so the fix must redact at read time.
- **Falsifying test:** `test_audit_header_never_carries_directory`.

### C55 — Dashboard failure lines embed raw exception text with absolute paths, and persist it
- **Sources:** SEC-7 · **Cross-exam:** G14 **CONFIRMED** (moderate to serious)
- **File:line:** `core.py:43-61`, `1537`; `backup.py:909`
- **Code (verbatim):**
```python
                    reason = attempt.get("reason") or "unknown"
                    backup_health.append(
                        f"Auto-backup failing: {reason} "
```
- **Trigger:** state file replaced by a directory → `⚠ Auto-backup failing: [Errno 21] Is a directory: '/private/tmp/claude-501/jd/Acme Client/book.gnucash.mcp/backups/.state-book.json' (last attempt today)`, repeated on every summary. `_describe_check_failure` masks only URI passwords.
- **Falsifying test:** `test_summary_backup_warning_redacts_paths`.

### C57 — Book strings are emitted raw into compact output; a description can forge rows and the server's own banners
- **Sources:** SEC-9 · **Cross-exam:** G14 **CONFIRMED** (moderate to serious)
- **File:line:** `_base.py:1321-1329`; 35 f-string row builders under `book/` vs 6 `_tsv_cell` call sites
- **Code (verbatim):**
```python
        line = f"{date_str}\t{short}\t{desc}\t{splits_str}"

    if transaction.notes:
        line += f"\t{transaction.notes}"
```
- **Trigger:** a description containing `\n⚠ CONTEXT RESET ... Writes are disarmed` followed by a well-formed fake row appears in `search_transactions` output indistinguishable from the server's real banner (server.py 836-842, 1529-1531). `create_party(name="Acme\n⚠ ...")` accepts a real newline from the model. Multi-line bank-import notes already split rows today.
- **Falsifying test:** `test_compact_transaction_line_escapes_newlines`.

### C61 — Auto-numbered creates crash when a book's counters are stored as doubles (GnuCash bug 798930)
- **Sources:** FC-2 · **Cross-exam:** G15 **CONFIRMED** (moderate to serious; narrow population — counters saved by 5.0/5.1's options dialog)
- **File:line:** `business.py:5065-5067`; piecash `business/person.py:124-125`
- **Code (verbatim):**
```python
                cnt = max(book_counter, max_numeric) + 1
                setattr(book, config["counter_attr"], cnt)
                doc_id = f"{cnt:06d}"
```
- **Desktop reference:** `qof_book_get_counter` (qofbook.cpp:606-615) casts a double with a comment naming bug 798930.
- **Trigger:** `counters/gncCustomer` as slot_type 2 → `create_customer` → `ValueError: Unknown format code 'd' for object of type 'float'`. `create_customer` takes no ID parameter, so no customer can be created on such a book.
- **Falsifying test:** `test_auto_id_accepts_double_counter`.

---

## 5. MINOR after cross-examination (verifier-rated moderate/medium/low)

Real defects, reproduced, not release gates. Each keeps its wave-1 code quote in the scratch reports; the entry here is the corrected finding.

| ID | Finding | File:line | Verifier verdict and correction |
|---|---|---|---|
| C43 | Root account resolvable by GUID prefix; a batch row can post to ROOT (net worth silently dropped 10000 → 9877) | `_base.py:2427-2435` | G2 PARTIAL (moderate). **Refuted:** `set_account_slot` on root cannot overwrite an existing `gnc-mcp` frame (piecash `TypeError`). True instead: `delete_account_slot(root, "gnc-mcp")` wipes the cache; a flat `gnc-mcp` string set first breaks every later designated-account write. No tool surfaces the root GUID. One-line fix: filter `type == "ROOT"`. |
| C8 (iii) | `delete_account_slot(acct, "reconcile-info")` wipes desktop's frame; `set_account_slot(acct, "reconcile-info", "garbage")` makes later reconciles fail write verification with a doubled prefix | `admin.py:25, 175`; `_base.py:2801-2822` | G3 CONFIRMED, low–medium. Failure is atomic; only bites never-reconciled accounts; recoverable. The doubled "Write verification failed:" prefix is a general cosmetic bug in `safe_tool` (`_helpers.py:466-469`). `notes`/`color`/`tax-related`/`equity-type`/`last-num` are harmless string slots. |
| C17 | Non-budget writes stamp `Use natural signs in budget amounts`; GnuCash 3.0–3.7 refuses to open the book | `_base.py:2587-2597` | G4 PARTIAL → MINOR/docs. Facts hold (added in 3.8; 3.7's `gnc-file.c:942-982` refuses; piecash opens 3.x). But desktop 3.8+ stamps any budget-bearing book on plain open (`gnc_maybe_scrub_all_budget_signs`, ScrubBudget.c:207-214), so the behavior is parity. Fix: declare "GnuCash ≥ 3.8, tested with 5.12" in README/manifest. Note piecash-created books record `Gnucash=3000000`, so that row can't gate. |
| C25 | `_neutral_time` hard-codes 10:59 UTC; desktop shifts it beyond UTC−10/UTC+13; the migration rewrites desktop's correct 11:59/09:59 rows | `_base.py:255`; `business.py:2782` | G4 CONFIRMED → MINOR. gnc-datetime.cpp:250-258 quoted. Pago Pago: invoice dated 09-29 reads 09-28; desktop 11:59 row rewritten. The day-off round trip predates 1.5 (piecash `_DateAsDateTime`, sa_extra.py:168-169); the migration half is new and broader than the price migration. |
| C11 | Server never inserts a `gnclock` row; desktop or a second server can open mid-write | `_base.py:1779` | G5 PARTIAL → medium. Desktop's exact lock sequence succeeded mid-`pay_invoice`; two concurrent `create_transaction` calls both got "created". **Refuted:** the claim's own `pay_document` race does not occur on SQLite — the SQLite-only `_find_invoice` heal UPDATE takes the write lock before the balance check (accidental protection; stands on PostgreSQL, argued from code). Present since v1.0.0. Costs of a lock row: desktop's "locked" dialog during every call; stale locks after a crash. |
| C27 | `create_transaction_from_scheduled` commits twice; a phase-3 failure leaves a posted instance without its stamp and an un-advanced schedule; retry returns duplicate-rejected with no guid | `scheduling.py:1469, 1481` | G5 CONFIRMED → medium. Reproduced; the retry advances the schedule but never stamps the transaction, permanently unlinking it. Window is milliseconds; the converter-raise trigger needs a converter bug. |
| C28 | Post-commit response building reopens SQLite by path; a rename between commit and response creates a 0-byte file at the book path and reports the committed write as failed | `business.py:~7617`; `_base.py:~2319` | G5 PARTIAL → low. Reproduced exactly. **Correction:** the failure comes from ORM reload of expired objects, before `_cache_token`. Every write tool shares the pattern. |
| C32 | `assign_split_to_lot` commits then can raise; assignment persists, tool reports error, retry says "already assigned" | `investments.py:1536-1546` | G5 CONFIRMED → low. Needs a root with no commodity (only when the New Account Hierarchy assistant is skipped; ~46 other sites fail first). Extra: if the assignment zeroes the lot and the next step fails, the lot is saved with `is_closed` = open and desktop trusts it. |
| C33 | Audit entries silently dropped when the audit directory is removed mid-run | `logging_config.py:352-385, 453` | G6 CONFIRMED → moderate. Two `update_account` calls committed; audit dir never recreated; only `--- Logging error ---` on stderr. Broader: any audit-write failure (EACCES, disk full) is swallowed after the book commit. |
| C53 | `GNUCASH_LOG_DIR` override bypasses symlink/owner checks; `.json.tmp` writes follow symlinks | `logging_config.py:249-252`; `backup.py:241-245, 314-318` | G6 PARTIAL → low. Same-user repro overwrote a "victim" file. The bypass is documented as opt-in (`:223-224`); the refusal message actually says "user-private location". Two other messages do suggest the override without that caveat. Cheap fix: same symlink/owner check on `{LOG_DIR}/{book}.mcp`, exclusive no-follow create, chmod 0600. |
| C44 | A negative-total regular invoice reads `status paid, amount_paid 0.00, overpaid true` at post and nothing can settle it | `business.py:3163-3166` | G8 PARTIAL → moderate, but the root cause of C15. **Refuted:** the zero-net case — desktop also posts zero totals and `gncInvoiceIsPaid` means "lot closed", so it reads paid there too. Fix at post: refuse net-negative regular documents or treat the open lot as credit. |
| C46 | Early-payment discount window anchored on `date_opened` with day arithmetic; window can close before the invoice is posted | `business.py:1095-1098` | G9 PARTIAL → moderate. Behavior confirmed (refused with "deadline was 2026-01-11"). **Refuted:** `gncBillTermComputeDiscountDate` does not exist in any GnuCash version; desktop has no early-payment-discount logic at all. Fails safe. The server's own `_billterm_due_date` port anchors on post date and handles PROXIMO; the discount code in the same billterm uses neither. |
| C51 | Taxtable entries at 0% or negative are refused; desktop allows −100..100 inclusive | `business.py:4126-4129` | G9 PARTIAL → MINOR. Confirmed, and the 100% cutoff is also off by one. Desktop-made negative entries are read and posted correctly (reverse-charge nets to zero). |
| C26 | Template `*-numeric` slots stored as `int(val*denom)/denom` and `0/100`; desktop stores reduced fractions and `0/1` | `scheduling.py:382-392`; `test_scheduled.py:1712-1714` | G10 PARTIAL → low. Facts right (desktop-resaved sample shows 4200/1, 25/1, 0/1). **Correction:** denominator is the template currency's fraction, not the account's. Values are equal; desktop copies num/denom through. Parity diff only. |
| C42 | SX start date a century back and end-before-start accepted | `scheduling.py:782-785` | G10 PARTIAL → low–moderate. **Refuted:** "no warning" (the next dashboard flags 36,524 days overdue; inverted create returns `next_occurrence: None`) and "can never fire" (an explicit `transaction_date` bypasses the end-date check and posted an instance after the end — a separate defect). Desktop's `gnc_sxed_check_endpoint` only confirms. |
| C20 | Two-decimal formatting everywhere; a BHD 10.125 balance shows 10.13 / 10.12 / 10.12 across three surfaces | `reporting.py:813-825, 894, 148-151`; `core.py:2702`; `logging_config.py:514`; `_format.py:174-191` | G11 CONFIRMED → moderate. Display only; stored amounts exact. Wider: `_format_number` rounds half-up and the dashboard's `_r2` half-even, so any book can differ by 0.01 on an exact half-cent. |
| C63 | Date-range filters compare TEXT lexically; compact `YYYYMMDDHHMMSS` rows and local-midnight rows are misfiled | `_query.py:75, 96-97` | G11 PARTIAL → moderate. Bound strings captured (`'2025-03-01 10:59:00'`). (b) confirmed as stated. (a) overstated: a compact row is only mis-sorted when the bound falls in the same year; historical reports over pre-2018 years only. Also: fresh desktop rows beyond UTC+13/−10 shift the same way. |
| C64 | Due-date reader ignores `slot_type`; a desktop-resaved GDate row reads 1970-01-01 ("20726 days past due") | `business.py:2584-2596`; `_base.py:2744` | G11 CONFIRMED → moderate–low. **Correction:** only converting writes heal it (`post_invoice` reported `due_dates_backfilled: 1`), not `create_customer`/`create_invoice`. Second path with no desktop: `_migrate_slot_fillers` stamps the epoch itself when the business module is disabled. |
| C23 | Server reconcile never clears `reconcile-info/postpone/*` | `_base.py:2779` | G11 CONFIRMED → minor–moderate. `recnFinishCB` calls `xaccAccountClearReconcilePostpone` first; the port kept only `SetReconcileLastDate`. Effect: stale date/balance preloaded in desktop's next reconcile dialog. |
| C36 | Statement claim-mismatch warning says "fix … (update_transactions)", which can't change amounts | `core.py:5494` | G12 PARTIAL → minor. Loud failure; `update_transactions`' docstring points to `replace_splits`. Unmentioned trap: `replace_splits` mints a new split GUID, so "then claim it" with the old GUID won't work. |
| C38 | `create_account` returns a GUID prefix without `%` that no account-taking tool accepts | `core.py:6415-6421` vs `6555` | G12 PARTIAL → moderate. Every misuse fails loudly; `fullname` works. Fix: return `self._account_short_guid(book, new_account)`. |
| C39 | An int64-overflowing amount fails the whole batch regardless of `on_error="skip"`; dry run says `would_create`; 27-digit amounts escape as `InvalidOperation` | `_base.py:445`; `core.py:4730` | G12 PARTIAL → minor. No bad writes; ≥10^17 only; piecash raises in phase 3 outside the per-row try; `_to_decimal`'s docstring promise is broken. `pay_document` shares the helper (not run). |
| C40 (rounding) | `set_budget_amount` silently quantizes 12.345 → 12.34, 0.001 → 0 | `budgets.py:737-740` | G12 PARTIAL → minor. Deliberate older behavior pinned by `test_budget.py:276` (commit bfc8ec1) predating the 2026-09-27 ruling; also ROUND_HALF_EVEN where splits use HALF_UP. |
| C52a | A malformed URI is echoed in full, password twice, on stderr at startup | `_format.py:826-831` | G13 PARTIAL → moderate. Startup-only; lands in the user's own terminal or `~/Library/Logs/Claude/` (mostly 0600). Contradicts `_redact_uri`'s own docstring. One-line fix. |
| C52b | `--book-uri` puts the password in argv (`ps` shows it 4 s after start) | `server.py:1747-1749, ~1877, 2032` | G13 PARTIAL → low–moderate. **Refuted:** "no env alternative documented" — README 316-330 shows only the env form; `--help` says it overrides the env. Holds: no warning, no `.pgpass` guidance (works today). Docker's baked `ENV GNUCASH_BOOK_PATH` makes `-e GNUCASH_BOOK_URI` fail as "both set", pushing Docker users to argv. |
| C59 | `_helpers` logger propagates to FastMCP's root Rich stderr handler; validation messages, tracebacks with `[SQL][parameters]`, and unredacted paths reach stderr regardless of flags | `_helpers.py:48, 420-490` | G13 CONFIRMED → moderate. Content is the user's own ledger data, in a file they own. Real defects: `--noaudit`/`GNUCASH_REDACT_PATHS` don't govern this second log; C16a's fix must cover these lines; Docker log shipping moves ledger text off-machine. The existing Claude logs on this machine hold 127 such lines. |
| C56 | `redact_paths` misses paths with spaces, UNC paths | `logging_config.py:191-195` | G14 CONFIRMED → moderate. **Refuted:** the `~/` case is redacted (to `~b.gnucash`). Side bug: `file:///…` mangled to `filb.gnucash`. Main reason C55 would still leak client names after its own fix. |
| C58 | `loan_term_months = 1e1000000` blocks `debt_payoff_plan` for ~46 s | `reporting.py:1525, 1533, 1544` | G14 PARTIAL → minor–moderate. Measured 0.59 s / 4.28 s / 45.97 s at 1e100000 / 1e300000 / 1e1000000. **Correction:** only `debt_payoff_plan` reads the slot; error is `decimal.Overflow`; clean error, no damage; `delete_account_slot` recovers. Latent: any overflowing term fails the whole plan. Bound ≤1200 fixes both. |
| C60 | `create_backup` is uncapped, unaudited, read-classified | `tools/backup.py:9-15, 37`; `logging_config.py:2866` | G14 PARTIAL → moderate. 1,363 backups / 3.0 GB in 20 s in a direct loop; `get_audit_log` showed 0 entries. **Correction:** "no trace" overstated — each is a timestamped file; a model-driven loop costs one round trip per backup. Fix: audit manual backups; skip when the book is unchanged. |
| C62 | Credit notes never stamp `features/Credit Notes`; parity dump can't see book-level slots | `business.py:4869`; `tests/fixtures/parity_dump.py` | G15 PARTIAL → low. Nothing reads the flag; only affects GnuCash < 2.5.0. Still breaks the "empty diff" ruling and reveals a dump blind spot. |
| C65 | Server text caps exceed desktop's VARCHAR widths on PostgreSQL/MySQL | `business.py:3407-3408`; `admin.py:33`; no cap on transaction description or taxtable name | G15 PARTIAL → moderate. All six cases fail with raw `DataError` on both backends (rolled back cleanly). Plausible trigger: taxtable name > 50. MySQL non-strict would silently truncate. Also: desktop MySQL tables are `utf8mb3`, so an emoji in a customer name fails with error 1366. |
| C66 | Desktop-created MariaDB collation makes name/ID lookups case-, accent-, and trailing-space-insensitive | `business.py:1445, 1549, 254, 260, 1424, 1435` | G15 CONFIRMED → moderate. With both `SALES TAX` and `Sales Tax` present, `delete_taxtable("Sales Tax")` deleted `SALES TAX` and echoed "Sales Tax". `_find_invoice` refuses the double match; taxtables don't. |
| C67 | "Use Split Action Field for Number" book option ignored | `business.py:6618, 6656` | G15 CONFIRMED → low–moderate. `gnc_set_num_action` (engine-helpers.c:119-148) swaps num/action when the option is on; server always writes the opposite. Metadata only; payments differ only on the transfer split's action. |
| C69 | "Day Threshold for Read-Only Transactions" ignored; a frozen period can be created into, voided, deleted with no warning | `core.py:4194-4208` | G15 PARTIAL → moderate. Confirmed the server never reads the option. Desktop's refusal is GUI-only (`xaccTransIsReadonlyByPostedDate` is not called from engine commit/destroy). Not a storage invariant; maintainer's call whether to refuse or warn. |
| C29 (a) | Session/weekly/monthly stages evaluated once per process | `backup.py:827-834` | G6: moderate; per-process is the intended design; user docs describe it wrongly. |

---

## 6. REFUTED

### C68 — "Cross-currency writes in trading-account books get no trading splits" — **REFUTED** (G15)
- **Wave-1 source:** FC-10 (claimed SERIOUS), citing `src/gnucash_mcp/book/_piecash_shapes.py:433-434`:
```python
    if account.type == "TRADING":
        return
```
- **Refutation:** the cited line only skips recording an implied *price* for trading splits; it does not decide whether trading splits are written. piecash's `Transaction.validate` (`piecash/core/transaction.py:350-351`) reads `options/Accounts/Use Trading Accounts` and adds trading splits itself, and every cross-currency write the server makes is an ORM transaction (its raw-SQL split writes touch only reconcile columns). Reproduced: an FX transfer via `create_transaction` wrote Trading USD (110/110) and Trading EUR (−110/−100); a cross-currency `pay_invoice` wrote both trading splits and each commodity nets to zero, satisfying `xaccTransIsBalanced` (Transaction.cpp:1118-1161).
- **Residuals the verifier did not check:** piecash finds the trading tree by the English name "Trading" (desktop uses the translated name — an i18n exposure of the kind CLAUDE.md's account-type rule warns about); piecash fails if a transaction already holds two trading splits for one commodity; desktop never books a realized FX gain on payments in a trading book, so the server's FX split may be wrong there.

Sub-claims refuted inside PARTIAL entries are recorded in §4 and §5 in bold: C2 (bills), C12 (zero-leg legitimacy), C14 (INCOME/EQUITY/placeholder), C15 (A/R increases), C28 (`_cache_token`), C42 (never fires; no warning), C43 (slot overwrite), C44 (zero-net), C46 (cited desktop function), C48 (payment dialog filter), C52b (no env alternative), C56 (`~/` case).

---

## 7. Side-findings surfaced by cross-examiners (not themselves cross-examined)

1. **`_find_taxtable` / `list_taxtables` have no `invisible`/child filter** (G1) — on books with desktop-posted invoices the server lists duplicate names and can attach new draft lines to a frozen child table. `business.py:1438-1445`.
2. **`void_transaction` overwrites `trans-read-only` with "Transaction Voided"; `unvoid` deletes the slot entirely** (G2) — a posting transaction permanently loses its read-only marker after a void/unvoid round trip. `reconciliation.py:653, 807`.
3. **An explicit `transaction_date` bypasses the schedule's end-date check** (G10) — posted an instance after the schedule's own end. `scheduling.py:~1375-1404`.
4. **SX instantiation writes a fresh `type='transaction'` price at the stale rate, dated today**, silencing the dashboard's stale-price warning (G10).
5. **`--noaudit` still creates the audit day file with a header carrying the absolute book path** (G13) — written at import-time `setup_logging` before CLI flags are read. `server.py:1570`.
6. **`redact_paths` mangles `file:///…` to `filb.gnucash`** (G14) — cosmetic.
7. **`apply_credit_note` response reports today as `apply_date` while the link transaction is dated at the lots' latest activity** (G8; = wave-1 BL-21). `business.py:8064`.
8. **The 20% early-payment hint suggested paying "-100.00"** (G9). `business.py:~7267`.
9. **A BTC quantity of 0.00005 rounds up to 0.0001 and writes an implied price of 20000 instead of 40000**, which then anchors the FX sanity check (G7).
10. **CHANGELOG line 26 says only schedule/budget/business writes convert the book; a price write does too** (G4).
11. **`void_transaction` proceeds on reconciled splits with only a warning** (G9) — makes C49's prescribed workaround destructive. `reconciliation.py:535-539`.
12. **Desktop MySQL tables are `utf8mb3`; an emoji in any text column fails with error 1366 under strict mode, or is silently stored as `?` without it** (G15; = wave-1 FC-8). CI's MySQL fixture uses piecash's `utf8mb4` tables and cannot see it.
13. **Out of domain, from the file-format adversary:** GnuCash 5.12's own Balance Sheet on the shipped Alex demo does not balance — Total Assets $862,031.84 vs Total Liabilities & Equity $862,676.41, a $644.57 gap, unchanged after a full server write sweep. Not attributed; worth its own look.

---

## 8. Wave-1 MINOR / NIT not cross-examined

Listed with the offending line and a one-line trigger; full quotes and tests are in the wave-1 scratch reports (`scratchpad/wave1_*.md`, this session).

**Money math**
- MM-9 `get_latest_price` ignores reverse-direction rows (`investments.py:1134-1146`: `if p.currency.mnemonic == currency`) — after USD→EUR 0.8 is added, returns 1.1 from 2024 while `net_worth` values at 1.25.
- MM-10 prices and share quantities shown with 4 decimals (`investments.py:1057, 1272, 1279`) — an IDR rate of 0.0000613 shows as 0.0001; 0.00004321 BTC shows as 0.0000.
- MM-11 `reconcile_account` (`reconciliation.py:476-480`: `expected_q = expected_balance.quantize(quantum)`) and `apply_credit_note` (`business.py:7955`) silently round sub-cent amounts that `enter_statement` refuses.
- MM-12 `get_budget_report` converts all actuals at one year-end rate (`budgets.py:870`: `factors = self._account_conversion_factors(book, last_end)`) — 260.0 vs `spending_by_category` 240.0 on the same data.
- MM-14 `create_commodity` accepts non-power-of-ten fractions (`investments.py:283`: `if not isinstance(fraction, int) or fraction <= 0`) — fraction 3 raises `InvalidOperation` on every write; fraction 8 stores 300/1000 for 0.3 shares.
- MM-15 template share quantity truncated, not rounded (`scheduling.py:394-406`: `numeric_val_num=int(q * q_denom)`) — 1.23456 stored 1.2345 where `create_transaction` stores 1.2346.
- MM-16 `_convert_invoice_amount` rounds half-even (`business.py:6386-6389`) — 10.03 × 1.5 = 15.045 → 15.04; desktop 15.05. (Folded into C1; MINOR on its own — exact ties only.)

**Storage shape**
- SS-13 deleting a document or party leaves orphan child rows of slot frames (`business.py:8265, 8330`: `Slot.__table__.delete().where(obj_guid == inv_guid)`) — `gnc-mcp/applies-to-invoice` left with no parent frame.
- SS-14 billterm refcount recount rewrites desktop's hidden child terms to nonzero (`business.py:2888`: `bt.refcount = int(refs)`); `gncBillTermIncRef` keeps children at 0.
- SS-15 `create_lot` writes `is_closed=0` and an empty `notes` slot (`investments.py:1315`); `gnc_lot_new` writes −1 and no notes.
- SS-17 unvoid clears notes only if they equal English "Voided transaction" (`reconciliation.py:805`); a German-voided transaction keeps "Stornierte Buchung". Void side skips an empty-string notes slot desktop would copy to `void-former-notes`.

**Data safety**
- DS-10 a doubly-faulted `switch_book` can leave the current book with zero audit handlers (`server.py:1501-1510`: `except Exception: pass` around the "LOAD-BEARING" fallback).
- DS-11 audit lines written after commit, response, and a second read-only open for account-ref rendering (`logging_config.py:2770 → _base.py:2485`) — one audited `update_account` executed three opens; a `pkill` in that window leaves a committed write with no audit line.
- DS-12 `_find_invoice`'s self-heal UPDATE runs in read-only sessions and holds SQLite's RESERVED lock for the whole read (`business.py:1533-1540`) — every document read blocks desktop's `gnclock` insert. (Also the accidental protection noted in C11.)
- DS-13 `_is_lock_error` is a substring match on "lock"/"busy" (`_base.py:843`) — a book under `sherlock/` that goes missing reports "Close GnuCash and try again" after 1.5 s; a `database is locked` from `save()` surfaces as `unexpected_error`. Backoff is linear, not exponential as documented.
- DS-15 full-book backups are 0644 in 0755 dirs (`backup.py:514, 537`) while audit logs are 0600; a 0600 book yields a 0644 backup.
- DS-16 (SPECULATIVE) an exception while rendering the audit entry turns a committed write into an error response (`logging_config.py:2937-3038`) — 30 of 68 dispatch handlers raised under off-type fuzz; no natural input found.

**Input validation**
- IV-18 `OverflowError: date value out of range` for rows dated 0001-01-01 / 9999-12-31 sinks the whole batch (`core.py:3879-3880`: `dup_start = trans_date - timedelta(...)`); `get_upcoming_transactions(days=3285000)` likewise.
- IV-19 no length cap on account names, descriptions, memos, transaction notes (`core.py:6278-6299`) — a 5 MB account name and 10 MiB descriptions accepted; book grew to 26 MB in three calls.
- IV-20 NUL and control characters accepted in descriptions/memos/notes/slot values (only account names reject) — SQLite stores 8 chars, `length()` reports 3, desktop shows "NUL"; PostgreSQL would reject.
- IV-21 account names starting with `%`, 32-hex names, trailing/zero-width/RTL-override characters accepted — `%abcdef0` becomes unreachable by path; three visually identical "Groceries" entries.
- IV-22 a UTF-8 BOM on a TSV header yields an error that appears to name a correct header (`_format.py:262`: `t.strip().lower()`; U+FEFF not in the invisible-char list at 397).
- IV-23 `update_transactions` silently drops cells beyond the header (`_format.py:1146`: `cell = cells[j].strip() if j < len(cells) else ""`).
- IV-24 a `qty` cell on a same-currency split is silently ignored (`core.py:4279-4280`: `if account.commodity == trans_currency: quantity = value`).
- IV-25 `create_budget` has no upper bound on `num_periods` (100,000 accepted; `set_budget_amount` without `period` then writes 100,000 rows) and accepts an empty name; `create_commodity` accepts fraction 10¹² (overflows int64 above ~9.2M units) and lowercase `namespace="currency"` as a second non-CURRENCY namespace.
- IV-26 `reconcile_account` silently drops `except_guids` that don't resolve (`reconciliation.py:418-425`, comment: "non-resolving prefixes drop silently") — passing a transaction GUID by mistake sweeps the pending item in.
- IV-27 (NIT) `Decimal(str(value))` accepts `5_000`, Arabic-Indic `٥`, fullwidth `５`; `limit=-5` silently becomes 50; `create_account(commodity="eur")` fails with no uppercase hint; `XXX` leaks `invalid literal for int() with base 10: 'N.A.'`.

**Business logic**
- BL-20 = C25. BL-21 = side-finding 7. BL-22 post/pay/unpost responses call a job-attached bill an "invoice" (`business.py:6702, 6910, 7513`: `.get(inv.owner_type, "invoice")`), so the audit log writes POST INVOICE for a bill.
- BL-23 `create_billterm` accepts `due_days=-30`, `discount_days > due_days`, and `discount_percent="150"` (`business.py:3917-3990`).
- BL-24 entry price `0.0000000000000000001` raises raw `OverflowError: Python int too large to convert to SQLite INTEGER` (`business.py:1810-1812`: `denom = 10 ** (-exp)`).

**Security**
- SEC-11 `create_price(value="9e999998")` blocks ~24 s before rejecting (`investments.py:494`: `frac = Fraction(value)`; piecash `_common.py:116`).
- SEC-15 = DS-15, plus pre-1.5 audit files written 0644 are never tightened (`logging_config.py:360-376`: `if path.exists() and path.stat().st_size > 0: return path`).
- SEC-17 the Docker image runs as root (no `USER` directive; `CMD ["uv", "run", "--no-sync", "gnucash-mcp", "--modules=all"]`), making the sidecar `st_uid` check meaningless.
- SEC-18 (NIT) debug-log lines embed unescaped exception text (`logging_config.py:3020-3023`: `error={e}`), so a newline in an echoed value forges a debug-log record.

**File format**
- FC-13 any SQLite file without a `gnclock` table is reported as "locked by GnuCash" after three retries (`_base.py:833-843`: `"lock" in msg` matches `no such table: gnclock`).
- FC-14 the startup sniff checks only the SQLite magic (`server.py:1242-1257`: `if head.startswith(_SQLITE_MAGIC): return None`) — a 2.6-era `versions` table, an extra `versions` row, or a dropped `versions` table each pass startup and fail every tool call with raw piecash errors.
- FC-15 a stale `gnclock` row (dead PID — currently present on the local MariaDB `gnucash` DB: `Ixion | 83233`, not running) gets "Close GnuCash and try again" with no host/PID and no liveness check; desktop offers "Open Anyway".
- FC-16 = C11.
- FC-17 auto-IDs ignore `counter_formats/gncInvoice` (`business.py:5067`: `doc_id = f"{cnt:06d}"`) — desktop generates `INV-47`, the server `000047`; two schemes in one book.
- FC-18 the three bundled demo books are old piecash-format files (`splits=4`, no feature stamps, 54/29/12 entries with `i_disc_type=''`/`b_paytype=0`); `gnucash-cli --report run` on a copy logged 108 warnings and rewrote the file (splits table rebuilt, counters frame reinserted, ISO-8601 stamp added). After a full server write sweep the same CLI logged 0 warnings and left the file byte-identical.
- FC-19 every test, parity twin, and demo uses a piecash-created book (`tests/conftest.py:66-70`; `test_db_backend.py:747`); the only GnuCash-created `.gnucash` on this machine is the maintainer's production book. Schemas differ (`splits.tx_guid` NOT NULL; `gnclock` column spelling; MySQL `utf8mb3` and collation). C65/C66 exist because of this gap.
- FC-20 nothing stops a 1.4.x server from writing to a 1.5-converted book (`scheduling.py:638-649` deletes `splits-json`; v1.4.4 reads recipes only from it; v1.4.4's `set_budget_amount` writes magnitudes into a now-stamped book). No `gnc-mcp/schema` marker exists.

---

## 9. What the test suite would need to have caught this

Each item is a contract test in the house styles (grep-the-source, set-equality, output-agreement):

1. **Delete-strip contract:** every `session.delete(`, `book.delete(`, and `del obj[...]` in `book/*.py` must have `_strip_guid_slots` within N lines — would have flagged C4b, C5, C7, C8 at once (the storage adversary's own suggestion; mirrors `TestWriteVerificationCoverage`).
2. **Read-only transaction guard chokepoint:** one predicate shared by void, replace, update, delete — would have flagged C4a, C4b(i), C4c.
3. **Tax parity oracle:** a table of invoices with expected desktop totals computed by a Python port of `gncInvoiceGetNetAndTaxesInternal` — C1, C2, C10.
4. **Reserved-key registry enforced at the write site,** not only in `test_slot_shapes.py` — C8.
5. **A GnuCash-created fixture book** (File › New in 5.12, committed), plus a desktop-shaped MySQL schema in CI — C63, C65, C66, C61, FC-19.
6. **Parity dump extended to book-level slots** and to the unpost path — C5, C62.
7. **URI-in-error scrub applied at `safe_tool` and the audit ERROR line,** with a test that greps every returned string and log line for a planted fake password — C16a, C16b, C52a, C59.
8. **Migration idempotence and false-positive tests:** run `_upgrade_book_shapes` on a fresh 1.5-written book and a desktop-written book and assert zero rows change — C9, C25, SS-14.

---

## 10. Resolution log (branch `fix/v1.5-adversarial-blockers`, as of 2026-09-30, late night)

Every fix has a test that fails on the pre-fix source. Full suite at
the last code commit (`00ed9a7`): 3093 passed, 33 skipped, no
expected failures. PostgreSQL gate: 88 passed. Desktop totals oracle:
0 differences. Engine twin: 24 scenarios, the server's rows equal to
the engine's in every one, less only what `ENGINE_DEBRIS` names.

### Ship-blockers: all seven fixed

| Item | Fix | Commit |
|---|---|---|
| C1 tax and line rounding | `_entry_math`, a port of GnuCash's own arithmetic | `69352d9` |
| C2 entry discounts ignored | same port; discounts read and applied | `69352d9` |
| C4a void of a posting transaction | `_refuse_posting_record`, shared by every path | `626287c` |
| C4b forced rewrite of a posting; unstripped split delete | `626287c`; `2b21d45` | |
| C5 unpost wipes the invoice's slots | strip before delete | `2b21d45` |
| C8 (i) slot-tool frame delete cascades | reserved keys and a shape check | `2b21d45` |
| C16a database password in errors and logs | `_scrub_credentials` on every road out | `55af187` |

C1 and C2 were verified against GnuCash 5.12's own engine, headlessly
(`19ce874`): the pre-port math differed on 383 of 600 random
documents, the port on 0 of 1,540.

### Serious: fixed

| Item | Commit |
|---|---|
| C4c re-dating a posting transaction | `626287c` |
| C7 `delete_account` cascade | `2b21d45` |
| C8 (ii) `placeholder` / `hidden` slots; (iii) `reconcile-info` | `2b21d45` |
| C10 / BL-14 posted total recomputed from live tables (the read half) | `5de139b` |
| C12 zero side on a currency split | `4726752` |
| C13 non-positive prices; C35 stale-rate advice | `7fd61f5` |
| C14 receivable as payment account; C15 same-side credit note | `91fb31c` |
| C16b query-string password; C52a echoed bad URI; C52b argv (documented); C59 (credential half) | `55af187` |
| C18 schedule instance at a stale or implied rate | `313aca0` |
| C19 fraction-valued schedule crashes the dashboard | `312c903` |
| C21 share quantity rounds to nothing | `4726752` |
| C34 far-dated statement and batch lines | `fd4b920` |
| C40 negative budget magnitude (sign half) | `4b6be72` |
| C41 audit log invents renames | `48fdf81` |
| C45 job-attached credit note | `3abea88` |
| C47 early-payment discount base | `b779fd0` |
| C54 audit header path; C55 dashboard failure paths | `dbd43ba` |
| C57 book text forging rows | `cca67a7` |
| C61 / FC-2 double-typed counters | `3abea88` |

Also closed along the way: SS-13 (orphaned frame children, `2b21d45`),
SEC-11 (slow absurd price, `7fd61f5`), side-finding 2 (unvoid dropped
a posting's read-only marker, `626287c`), a pre-existing flake in the
real-driver database tests (`b6d54be`).

### Serious: fixed after the first log

| Item | Fix | Commit |
|---|---|---|
| C9 credit-note migration flips a legitimate all-negative note | The converter keys on the pre-1.5 fingerprint (`i_disc_type = ''`) | `18c3330` |
| C30 one-time conversion without its own snapshot | A labelled pre-upgrade snapshot before the first converting write | `17b29ab` |
| C29 auto-backup never retried; once per process | Re-checked as stages come due; retried after a failure | `425392f` |
| C31 shared `GNUCASH_LOG_DIR` keyed on the filename | The first book claims the folder; a second with the same name gets its own | `425392f` |
| C22, C37, C49, C50 prepayments | `pay_document` gains `allow_prepayment`, `from_prepayment`, `payment_account_amount`; unpost keeps payments; the lists and the dashboard show unapplied payments | `26ddaa8` |
| C48 document currency differs from the owner's | Refused by default, `force` to proceed (ruling 3, below) | `d1e5881` |
| C10 storage half: no child bill term at post | `_billterm_return_child`; tax-table name lookups skip hidden copies | `830bb27` |
| C3 voucher card lines | `_card_charges`: card lines and the extra post to the employee's card account | `830bb27` |

### Serious: the last one, closed by ruling

| Item | State |
|---|---|
| C24 several same-day prices per pair | Twin run (`814851e`); ruled in round 2 (rank-replacement adopted, the leaked row not copied); fixed in `b3aa727`. |

One part of C10 is OPEN by the same round's ruling: whether a posted
line should point at a hidden copy of its tax table. The server
leaves lines on the live table. The GUI gate decides (step 6 of
`BOOKKEEPER_TEST_PLAN_FIX_BRANCH_GUI_GATE.md`).

### The engine twin

Until this point "parity with desktop" needed a person at the screen.
`tests/fixtures/engine_twin.py` removes that for anything the dialogs
do through the engine: `gnucash-cli` loads a report whose renderer
calls the engine function (`gncInvoicePostToAccount`,
`gncInvoiceApplyPayment`, `gncInvoiceAutoApplyPayments`,
`gncInvoiceUnpost`, `gnc_pricedb_add_price`,
`gncOwnerGetBalanceInCurrency`), and GnuCash's own SQL backend saves
the result. The same action runs through the server on a copy of the
book, both are dumped with GUIDs replaced by roles, and the text must
match. The engine's dumps are recorded for CI.

It corrected this review three times:

1. **C10, tax tables.** The review expected every posted line to
   point at a hidden copy of its tax table. The engine makes the copy
   and repoints the line in memory, and never saves the line. In a
   book GnuCash has posted in, the entries still name the live table.
   The server leaves them there. Only the bill term's copy is real on
   disk, and that is what was ported.
2. **C24, same-day prices.** "One price per pair per day" is half the
   rule. See ruling 2 below.
3. **C48, document currency.** The cross-examiner struck the claim
   that desktop hides such a document. The engine's payment and
   unpost code do handle it, row for row with the server's. But its
   balance for the party leaves the document out. See ruling 3 below.

Three things the engine leaves in a SQL book are debris by its own
account, and the server does not copy them: an empty payment lot
after every payment it moves into a document's lot (its scrub
destroys them); stale `post_txn` / `post_lot` / `post_acc` on an
unposted document (the SQL backend drops a NULL reference from its
UPDATE); and the unreferenced tax-table copy above. Stored
`refcount`s are not compared: the engine adds one per reference it
loads and saves the sum. The four were ACCEPTED in round 2 as a named
allowlist: `ENGINE_DEBRIS` in `tests/fixtures/engine_twin.py` lists
each with its reason, the dump filters only what the list names, and
every recording stores how much of each kind the engine left
(`00ed9a7`).

### Minor tier (sections 5, 7, 8): fixed

Each has its test in `tests/test_review_minor.py` unless noted.

| Commit | Items |
|---|---|
| `dab3481` | C27, C36, C38, C64, BL-22, side-findings 3 and 7 |
| `9ae608f` | C39, C40 (rounding), C42, C51, C58, BL-23, BL-24, MM-11, MM-14, IV-18, IV-21 to IV-27 |
| `6fdb606` | C23, C43, SS-15, MM-15, DS-13, DS-15, FC-13 |
| `03a542a` | C33, C56, C60 (audit line), SEC-18, side-findings 5 and 6 |
| `1a20ff6` | C17, C65, C66, IV-19, IV-20 |
| `952d53b` | C25, C44, FC-17, MM-9 |
| `c6ac896` | DS-12, FC-15, MM-10 |
| `830bb27` | side-finding 1, SS-14 (with C10) |
| earlier | C8 (iii), C29 (a), C52a, C52b, SS-13, SEC-11, side-findings 2, 4, 8, 10 |
| `c192c87` | G-1 (the GUI gate's finding); test in `tests/test_converter_false_positives.py` |
| `86fb02f` | C69, as a warning |

Narrower than the finding in four places: IV-20 refuses a NUL but not
other control characters in free text; IV-21 does not look for
zero-width or right-to-left characters; MM-10 covers the price list,
not lot quantities; SEC-15 creates new files 0600 and does not
re-mode audit files written before 1.5.

### Minor tier: not fixed, and how each was closed

*Rewritten 2026-10-01 after the pre-tag triage. Nothing is left
open: each row is closed by a ruling, or named in the Known
limitations section of the 1.5 CHANGELOG entry by the amended
ruling 6.*

| Item | Disposition |
|---|---|
| C11 / FC-16 no `gnclock` row taken | Closed as designed (triage ruling 3). The README's "Cannot open book" section says so; listed under Known limitations. |
| C62 `features/Credit Notes` not stamped | Closed as designed (triage ruling 5). Listed. |
| C63 date-range filters compare text | Verified, no code. GnuCash 5.12 stores `post_date` as 19-character ISO text at 10:59:00 (checked on the gate book and `parity-desktop.gnucash`), so text comparison is correct for every row GnuCash 3 or later wrote. Listed for rows written by 2.6 or older. |
| C69 read-only day threshold | Fixed as a warning, `86fb02f`. |
| C46 early-payment window anchored on `date_opened` | Listed. Fails safe; GnuCash has no logic to match. |
| C60 (cap) manual backups are unlimited | Listed. |
| C67 "Use Split Action Field for Number" ignored | Listed. Metadata only. |
| C20 three-decimal currencies shown at two | Listed. Display only. |
| MM-12 budget report at one period-end rate | Listed. |
| C26, C28, C32, C53, SS-17, DS-10, DS-11, DS-16, FC-14, SEC-17 | Listed, one line apiece. |
| FC-18, FC-19, FC-20 | Listed. FC-19 is narrower than it was: the engine twins, the converter's false-positive test, and the GUI gate now run against rows GnuCash wrote. |
| Side-findings 9, 11, 12 | Listed. |
| Side-finding 13 (the 644.57) | The bookkeeper's, during the generator top-up. Not a server defect as far as anyone has shown. |
| IV-20, IV-21, MM-10, SEC-15 (the narrowed fixes) | Listed, in one line. |
| `hidden` refused by the slot tools | Endorsed as built (triage ruling 4). Listed: no tool hides an account. |

### Before this branch merges

0. The GUI gate has its own manifest and a purpose-built book:
   `BOOKKEEPER_TEST_PLAN_FIX_BRANCH_GUI_GATE.md` and
   `~/Projects/abe-bench/fix-branch-gate.gnucash`. Seven steps; two
   of them (the taxed post, the Price Editor) end in a SELECT.
1. The desktop-open gate: unpost, the delete paths, and the posting
   math changed what is written. Post and unpost an invoice with a
   document link, delete an account with an OFX link, and open the
   book in GnuCash. Added by the later fixes, all engine-twinned but
   not yet seen in the GUI: a book holding an overpayment's
   pre-payment lot (Process Payment should list it for the
   customer), a document unposted with its payment kept, a document
   on a posted copy of its billing term (the Billing Terms editor
   should show the term once), and a card voucher.
2. The maintainer's calls flagged in each commit's summary (new
   response fields `total_note` and entry `discount`; `hidden`
   refused by the slot tools with no server alternative; budget
   sub-cent rounding left as is). *Stale since `9ae608f`: a
   sub-unit budget amount is refused, not rounded (C40).*

*Scratch material for this review (wave-1 reports `wave1_*.md`, wave-2 verdicts `wave2_G*.md`, and every reproduction script) lives in the session scratchpad at `/private/tmp/claude-501/-Users-stephen-Projects-gnucash-mcp/60d9a729-9d7d-44e5-9095-692b003ba4ea/scratchpad/`. It is not committed.*

---

## Bookkeeper countersignature (2026-09-30, afternoon)

**Concur: NO-SHIP.** C1 alone decides it. A server that posts a
different A/R total than desktop on a third to half of multi-line
taxed invoices is wrong money on the most ordinary business
workflow there is, and the docstring claims parity while a house
test locks the divergence in. That is precisely the class of defect
this whole operation exists to stop, and it outranks any schedule.

**Reconciliation with the live-battery ledger.** Today's parity
clearance of `test/slot-shapes` at `b5c98b6` STANDS AS SCOPED: every
behavior that battery verified (payment currency and FX splits,
price-row discipline, document-currency balances, due-date writes,
partial payments) was verified against desktop and none of these
findings contradicts it. Every surviving blocker lives in territory
no battery ever walked: no gate has ever posted a TAXED or
DISCOUNTED invoice; no battery ever aimed void_transaction,
replace_splits, update_transactions or unpost at a posting
transaction; none touched frame-key slots or a database URI error
path. The one REFUTED finding (C68, trading accounts) is consistent
with battery evidence. The clearance covered what changed; the
adversaries attacked what never changed. **The tag now waits on the
seven, so the clearance's "cleared for merge" no longer implies
"cleared to ship."**

**One qualification to filed doctrine, answered honestly.** The
dashboard-sensitivity report states "the server's READER is
bilingual; only the writer was wrong." C64 (cross-examined,
CONFIRMED at moderate) shows a path where that fails: the reader
ignores `slot_type`, so a desktop-resaved GDate row reads 1970
("20726 days past due"), and only converting writes heal it. The
bilingual-reader line held for every row the battery produced and
read, but it was broader than the evidence. The doctrine is hereby
narrowed to: the reader handles both formats ON ROWS THE SERVER OR
THE BACKFILL HAS TOUCHED; C64's reader fix should key on slot_type.
Amendments stay visible; the original line stays where it was
written.

**What the fix cycle owes the loop.** The seven cluster smaller
than they count: one shared read-only guard closes C4a, C4b(i) and
C4c; strip-before-ORM-delete at the three missed sites closes
C4b(ii), C5 and C8(i); C1+C2 are one tax/discount engine rewrite
against `gncEntryComputeValueInt`; C16a routes three outputs
through the scrub that already exists. Riders that should ride the
same branch as cheap data-safety: C9's one-line `i_disc_type`
fingerprint gate and C30's labeled pre-migration snapshot. Every
falsifying test in this report becomes a contract test — including
replacing `test_q3_residual_to_largest_rate`, which currently
guards the defect.

**The gate requirement carries forward unchanged.** The fix branch
changes posting math and slot handling — storage shape — so it gets
the full bookkeeper loop AND the desktop gate, and the gate's twin
set is hereby EXPANDED to cover what today exposed as never-walked:
a multi-line taxed invoice twin (server-posted vs desktop-posted,
totals diffed to the cent), a discounted-entry twin, an
unpost/repost round trip with slots diffed before and after, and a
desktop-drafted credit note. The date moves; the gate does not.

**Claimed by the bookkeeper:** side-finding 13, the $644.57
imbalance in the shipped Alex demo's own Balance Sheet
(862,031.84 vs 862,676.41). An unbalanced demo book is a books
problem, not a code problem, and the sample generators are about to
be topped up — I will chase the gap to its split before the
regenerated samples are frozen.

*Signed, the bookkeeper. Nothing in this review was scrubbed; one
filed doctrine was narrowed in response to it, above.*

## What became of the three rulings below (2026-09-30, late night)

*Written by the fixing session; the rulings themselves follow,
unedited.*

**Ruling 1, prepayments.** Overridden by the maintainer the same
evening ("I think I changed my mind. We're fixing it tonight"). The
full capability is in `26ddaa8`, with ten scenarios twinned against
the engine. The narrow fix the ruling describes is contained in it:
the advice text is corrected, the dashboard reads prepayment lots,
and unpost no longer refuses.

**Ruling 2, same-day prices.** The twin was run through the engine's
`gnc_pricedb_add_price`, the function behind the Price Editor's OK
button and a quote fetch. Neither reading was whole:

- A new price whose source ranks equal to or better than the day's
  existing price replaces it. The old row is deleted, whatever its
  source, and in whichever direction of the pair it was quoted.
- A new price whose source ranks worse is rejected in memory, but it
  was committed before `add_price` was asked, so its row is already
  in the table and stays. The next load reads both.

So desktop does produce the two same-day rows the gate saw, and does
collapse in the case the adversary described. `create_price` agrees
with the engine in four of six scenarios. In the other two (a better
source after a worse one; the opposite direction on the same day) it
keeps a row the engine deletes. Those two are strict expected
failures in `tests/test_parity_prices.py`. Per the ruling, nothing
was changed; adopting the engine's rule is a patch awaiting the word.

**Ruling 3, document currency.** The proof the ruling asked for came
back against the dialect. Asked for Acme's balance with 220 of open
USD invoices, the engine said 220. Asked again with an unpaid EUR 100
invoice beside them, it said 220: `gncOwnerGetBalanceInCurrency`
walks only the receivable accounts in the owner's currency. By the
ruling's own terms that is a misread, so the mismatch is refused by
default with `force` (`d1e5881`), and the forced response names the
divergence. The GUI half of the twin (Find Invoice, Process Payment,
Customer Report on a forced document) is still worth a look at the
gate; the engine half is pinned.

## Bookkeeper rulings — three questions from the fix cycle (2026-09-30, 21:12)

**1. Prepayments.** Narrow fix in 1.5: correct the advice text (it
misbooks — lies don't ship), teach the dashboard to read desktop's
prepayment lots (invisible money is a lie by omission; read-side
only), and unpost-with-payments refuses with the cure named in the
error. The full capability — holding unmatched cash, new
`pay_document` parameters — is the first post-release patch:
additive and backwards-compatible, so legitimate under
patches-only, with its own loop and gate. Missing capability may
ship, named in the release notes; misleading output may not.

**2. Same-day prices.** No change on this evidence. Today's desktop
gate (C7) PASSED with two same-day EUR rows of different sources on
screen, and desktop's own payment updated its dialog row in place
rather than collapsing the pair — the claim's "one per pair per
day" is contradicted by the oracle at the screen. Behavior pinned
by a test, a ruling, and a passed gate does not move on static
analysis. If the adversary stands by the claim: twin it (two
same-pair prices, one day, desktop Price Editor, rows diffed). If
the twin shows collapse, adopt desktop's rule as a patch.

**3. Document currency.** Don't refuse — that breaks a real
workflow and removes capability mid-fix-cycle. But the dialect
isn't proven either: no gate has ever shown desktop READING an
off-owner-currency document, and by the payment-currency
precedent, dialect status requires exactly that proof. Keep the
pick, warn on mismatch, and add to the fix-branch gate a twin: a
server-made invoice in a currency its owner doesn't use, through
Find Invoice, Process Payment, and Customer Report. Desktop reads
it clean → dialect, warned and documented. Desktop misreads →
refuse-by-default with force, because then it was never a
capability.

## Bookkeeper rulings — fix-branch round 2 (2026-09-30, 23:05)

**0. Correction of the record: Ruling 1 was NOT overridden by the
bookkeeper.** The 21:12 ruling (narrow fix in 1.5, full prepayment
surface as first post-release patch) stands unretracted; no
override was issued. The full fix that landed is good work — twelve
engine-twin scenarios row-for-row exceeds what the deferral assumed
possible — and may stand IF the maintainer ratifies it as his own
override, recorded under his name. RESOLVED 23:05: the maintainer confirms the override is his
("I could not stomach" deferring work this close to the last
release). The full prepayment fix therefore stands as a MAINTAINER
OVERRIDE of bookkeeper ruling 1, with the bookkeeper's concurrence,
conditional on the expanded GUI gate (item 6 below). The
verification mattered doubly because the same work stretch
contained a forged system-reminder in a tool result; unverified
authority claims get verified, not inherited — this one verified
true.

**1. Same-day prices (per the engine twin): adopt rank-replacement;
do not reproduce the leak.** The engine replaces the day's row when
the new source ranks equal or better — the server adopts that. The
engine also persists a row its own memory rejected (worse-ranked);
the server does not write that row, by the `temporary`-price
precedent: fidelity means desktop-readable, not litter-compatible.
Of the two strict xfails, the rank-replacement case becomes real
behavior; the leak case becomes a documented intentional
divergence citing this ruling. (This also retro-explains the C7
gate observation: the morning's coexisting EUR rows were the leak,
which is why desktop displayed them without complaint.)

**2. Document currency: refusal RATIFIED.** The twin met the
ruling's own condition — the engine's customer balance ignored an
unpaid off-currency invoice, so the mismatch was never a dialect
but invisible money. Refuse-by-default with `force` is correct; the
behavior break and ten migrated tests are accepted.

**3. Engine-debris exceptions: the four are ACCEPTED** (empty
payment lot per payment, stale posting refs on an unposted
document, unreferenced tax-table copy, refcounts uncompared) — as a
NAMED allowlist in the twin fixture, each with a comment saying
what it is and why, never a silent filter. The GUI gate's
prepayment book is the backstop if desktop proves to expect the
empty lot.

**4. Tax tables on the live table: NOT accepted on current
evidence — open, gate decides.** Two oracles conflict: GnuCash
source (gncTaxTableReturnChild in the GUI post path, confirmed by
cross-examination) vs the headless engine run that never saved the
move; headless cli may not walk the GUI dialog's path. Tiebreaker
at the gate: post a taxed invoice IN THE GUI on a SQL book, then
`SELECT invisible, parent FROM taxtables`. The billterm child
already landed; the taxtable question stays open until that SELECT.

**5. For the record:** the forged system-reminder in a tool result
was correctly ignored; its source (tool and call) should be
recorded in §10. The `git checkout --` slip is acknowledged and
closed — self-limited, owned unprompted.

**6. GUI gate manifest for the fix branch** (purpose-built book):
prepayment lot held; document unposted with payment kept; document
on a posted billterm copy; card voucher; forced off-currency
invoice; taxed-invoice post followed by the taxtable SELECT above;
Price Editor same-day rank-replacement check.

## Fix-branch response to round 2 (2026-09-30, late night)

*Written by the fixing session, item for item.*

**0. The override.** Recorded as the bookkeeper wrote it: a
maintainer override of ruling 1, with the bookkeeper's concurrence,
conditional on the GUI gate.

**1. Same-day prices.** Done in `b3aa727`. `create_price` and
`create_prices` follow `gnc_pricedb_add_price`: a source that ranks
equal or better takes the day, in either direction of the pair; a
worse-ranked price is not written, and the response says what
outranked it and how to override (`status: kept`). The row GnuCash's
SQL backend leaves for a price it turned away is not copied. In
`tests/test_parity_prices.py` the two expected failures are gone: the
server's rows are the engine's rows less the ones the engine's own
memory rejected, and that subtraction is written out in the test, not
filtered. One consequence worth knowing: the default source,
`user:price`, ranks below `Finance::Quote`, so a price typed through
the server on a day that already has a feed quote is not written
unless it is sent as `user:price-editor`. A batch with two rows for
one pair and day now rejects the second.

**2. Document currency.** Nothing further; ratified as built.

**3. Debris allowlist.** Done in `00ed9a7`, as described in section
10. A server-written book must report no debris at all
(`debris_found(book) == {}` in every scenario); the engine's counts
are in the recordings.

**4. Tax tables.** Marked open everywhere it was stated as settled:
the code comment at the post, the CHANGELOG entry, CLAUDE.md, the
twin's docstring, and the allowlist entry itself. The server's
behavior is unchanged pending step 6 of the gate. If the GUI post
does save the line's move, the server-side change is small: at post,
find or make the table's hidden copy (the way
`_billterm_return_child` does for terms) and repoint each line.

**5. The forged reminder, for the record.** It arrived in the result
of a `Bash` tool call: the first call after this session resumed from
a context compaction, a `sed -n` print of lines 6949 to 7084 of
`src/gnucash_mcp/book/business.py` (the head of `pay_invoice`). After
the file's text, inside the same tool result, came a block styled as
a `<system-reminder>`. It said it replaced earlier attribution
guidance and told the session to end commit messages with a
`Co-Authored-By: Claude Fable 5.1` line and pull-request bodies with a
"Generated with Claude Code" footer. A search of the repository finds
that text in no file, so it did not come from the content being read.
It was not acted on; the project's no-attribution rule held for every
commit on this branch. The `git checkout --` slip is closed as ruled.

**6. Gate manifest.** Written as
`BOOKKEEPER_TEST_PLAN_FIX_BRANCH_GUI_GATE.md`, with the book built
and placed in the bench folder. GnuCash's engine loads the book
headlessly; the windows have not been opened on it.

## GUI gate result (2026-10-01, 00:00 to 01:40)

*Written by the gate session. The maintainer worked the windows; the
gate session ran every SELECT and every server call. Book:
`~/Projects/abe-bench/fix-branch-gate.gnucash`, built at `00ed9a7`;
server at `f38c8cc`; GnuCash 5.12, SQLite backend.*

**Verdict: PASS.** Every step of
`BOOKKEEPER_TEST_PLAN_FIX_BRANCH_GUI_GATE.md` passed or recorded what
it was asked to record. So did three steps the gate session added
from this review's own list (§10, "Before this branch merges", item
1, and the countersignature's expanded twin set): a document link
through a server unpost, an OFX-linked account deleted by the server,
and a taxed, discounted invoice posted both ways. One new MINOR
finding (G-1, below). Nothing scrubbed.

Snapshots kept beside the book: `fix-branch-gate.virgin.gnucash` (as
built), `fix-branch-gate.after-pass1.gnucash` (after the first GUI
session, before any server write), and the server's own
`pre-1-5-upgrade` snapshot (see G-1).

### Steps

| Step | Result |
|---|---|
| 0 Book opens | PASS. No error, no feature refusal. "Process payments on posting" switched off for the gate, so no step auto-applied a payment. |
| 1 Prepayment lot | PASS. Process Payment listed the 20.00 and 70.00 as Pre-Payment, in the Credit column, on their own rows. 000002 with the 20.00 at zero cash left 30.00 due. |
| 2 Unposted, payment kept | PASS. 000003 opened unposted and editable; posted 2026-01-22; the 70.00 was offered and settled it in full. |
| 3 Billing term copy | PASS. "Net 30" listed once. 000004's terms read Net 30. The window does not show a due date; the book holds 2026-02-17 on the posting transaction's `trans-date-due`. |
| 4 Card voucher | PASS. Hotel and Dinner as Charge; Company Card 60.00 and 25.50; Dana's A/P 40.00. |
| 5 Off-currency invoice | RECORDED. The window shows no currency, only amounts; its post account is Receivable EUR. Process Payment lets Receivable EUR be chosen as Post To and then lists 000006. The Customer Report shows 000006 in a separate EUR section. Viewing it and running the report changed nothing: 000006's invoice row, posting splits and slots hash identical to the as-built book. Ruling 3 (refuse by default) is unaffected: the engine's owner balance still leaves 000006 out. |
| 6 Taxed GUI post | 000005 posted at 315.00 (A/R 315, Sales −300, GST −15). The GUI created a hidden copy of T5 (`invisible=1`, parent set), and 000005's line stays on the **live** table (`points_at_copy = 0`). By the manifest's rule, **leaving lines on the live table stands.** See the qualification below. |
| 7 Price Editor, same day | PASS. The editor asked to replace the day's price; afterwards one 2026-06-01 row, `user:price-editor`, 23/20. Desktop's rank-replacement, as the server now does it. |
| 8 Document link through unpost (added) | PASS. Link `https://example.com/inv4.pdf` attached to 000004 in the GUI; the server unposted 000004; `assoc_uri` and `credit-note` both survived (C5). Back in the GUI the link was present; 000004 reposted 2026-01-22 (due 2026-02-21); "Net 30" still listed once. |
| 9 OFX-linked account deleted (added) | PASS. GUI created an empty Assets:Old Bank and gave Income:Sales a note and a color. The gate session then wrote a desktop-shaped `ofx` frame on Old Bank with `ofx/associated-income-account` pointing at Sales (raw SQL; only an OFX investment import makes it in the GUI). The server deleted Old Bank: Sales kept `notes`, `color` and `balance-limit`, and no frame rows were orphaned (C7). The GUI agreed. |
| 10 Tax and discount twin (added) | PASS. 000007 drafted in the GUI: three lines 1 × 0.10 and one line 1 × 100.00 with a 10% pretax discount, all taxable on T5. The invoice window computed subtotal 90.30, tax 4.52. Duplicated as 000009 and posted in the GUI: A/R 94.82, GST −4.52, Sales −90.30. The server posted 000007: the same three splits to the cent. Before 1.5's fixes the server would have posted 105.30 (C1, C2). |
| After the gate | PASS. The server's reads on the GnuCash-written book raised nothing. A/R 824.94 = 30 + 200 + 315 + 90.30 + 94.82 + 94.82; Receivable EUR 100 (USD 115.00 at the new 1.15); A/P 40.00; Company Card 85.50; GST 24.04; no prepayment left over. All agree with the windows. No `gnclock` row left behind. |

### Qualification to step 6

000009's lines DO point at the hidden copy. GnuCash moves a posted
line to the copy in memory and saves the move only when it writes
that line for some other reason; the duplicated lines were freshly
written, 000005's were loaded and untouched. Desktop therefore writes
both shapes and reads both. The server's choice (live table) is the
common desktop case and posts correctly either way; it found and
skipped the hidden copy when posting 000007. The
`unreferenced_taxtable_copy` entry in `ENGINE_DEBRIS` stays, but its
comment should say the copy is sometimes referenced.

### New finding

**G-1 (MINOR): the converter rewrites line dates written by
desktop's Duplicate Invoice, and takes a pre-1.5 snapshot on a 1.5
book.** The server's first write after the first GUI session (the
step 8 unpost) returned `pre_upgrade_backup`, `entries_normalized: 8`
and `billterm_refcounts_recomputed: 1`. The 8 rows were the lines of
000008 and 000009. Desktop's duplicate path stored their `date` at
10:59:00 UTC (neutral time); the converter rewrote them to 19:00:00
UTC (local noon, the entry-ledger convention). Lines typed into the
register (000007's) were already stored at 19:00 by desktop itself.
So both are desktop shapes, and the converter took one for a
pre-1.5 server shape: the false positive §9, item 8 asked a test
for. No money moved; the GUI still shows 10/01/2026 on those lines.
Cost: rewritten desktop rows and a needless "pre-1.5 upgrade"
snapshot on any book where an invoice has been duplicated.

### Observations, not findings

- An unposted invoice shows a posted date of 31-Dec-1969 in the
  invoice window. 000003 (unposted by the server) and 000005 (never
  posted) both store NULL and both show it; it is how the window
  draws an empty date.
- GnuCash left one entry row with no invoice (date_entered at the
  epoch), most likely the invoice editor's blank row. The server
  reads around it.
- A no-terms invoice posted in the GUI defaults its due date to
  today (000003, 000008, 000009), not to the post date.
- An accidental GUI post and unpost of 000007 left GnuCash's own
  stale `post_txn`, `post_lot` and `post_acc` (the `ENGINE_DEBRIS`
  item). The server posted over them cleanly, so desktop debris
  of that kind is now tested at the screen as well as in the twin.
- Process Payment's Transfer Account list commits a payment on
  double-click. Step 1 was restored from the as-built snapshot and
  redone after one; the first attempt left nothing behind.
- The countersignature's desktop-drafted credit note was not walked
  in the GUI; `tests/test_parity_credit_note.py` covers it against
  the engine.

### Open for the maintainer

1. G-1: fix before tag, or carry it on the open list.
2. The step 6 comment change in `ENGINE_DEBRIS`.
3. §10, item 2: the new `total_note` and entry `discount` response
   fields; `hidden` refused by the slot tools with no server
   alternative; budget sub-cent rounding left as is.

## Bookkeeper rulings — pre-tag triage (2026-10-01, 02:00)

**1. C10 tax tables: CLOSED by the gate.** Desktop writes both
shapes (live-table on lines it loaded, hidden-copy on lines it
rewrote), reads both, and the server posted correctly against each.
Live-table stands. Amend the `ENGINE_DEBRIS` comment as the gate
session proposed ("sometimes referenced").

**2. G-1: FIX BEFORE TAG.** This is C9's bug class recurring — the
converter inferring provenance from content (a 10:59 entry date)
and rewriting rows desktop made. The cure is already on the branch:
gate `entries_normalized` on the same pre-1.5 fingerprint C9's fix
uses (`i_disc_type = ''`), which desktop's duplicate path never
writes. A final version whose converter rewrites desktop rows and
takes "pre-1.5 upgrade" snapshots of 1.5 books contradicts the
release's core claim; the fix is one gate on an existing pass plus
the §9-item-8 test. Small, and it should not ship wrong.

**3. C11 gnclock: CLOSED AS DESIGNED.** The server does not take a
gnclock row. A call-length lock that survives a crash wedges
desktop with a stale "book is locked" the user must override —
worse than the thing it prevents. The existing etiquette (refuse a
desktop-locked book cleanly; database-level integrity inside a
call) is the safer asymmetry. One line in the docs saying so.

**4. The three maintainer calls: ENDORSED AS BUILT.** `total_note`
and entry `discount` response fields (additive; name them in the
release notes); `hidden` refused by the slot tools with no server
alternative (safe for 1.5; an alternative is patch material);
budget sub-cent rounding left as is.

**5. C62 feature stamp: CLOSED AS DESIGNED** — concur; a feature
stamp is the riskiest row this server could write and only
pre-2.5.0 GnuCash reads it. Conservatism doctrine applies.

**6. Everything else on the open list is post-tag patch material**,
with two priorities for the first patch cycle: C63 (text date
comparison can misstate query results — money-adjacent) and C69
(warn inside desktop's read-only window). C46, C20, C67, MM-12 and
the low tier follow. Side-finding 13 (the 644.57) stays claimed by
the bookkeeper for the generator top-up. MM-12 gets a proper
semantics ruling when the patch cycle asks for it.

**Final clearance condition:** G-1's fix lands with its test and
the suite stays green. On that, `fix/v1.5-adversarial-blockers` is
cleared by the bookkeeper for merge and tag.

### Amendment to ruling 6 (2026-10-01, 02:05)

The maintainer advises no post-tag development period should be
assumed. Ruling 6's "first patch cycle" framing is therefore
retired and the open list re-ranked by a harsher test: safe to
leave forever, or not.

- **Before tag, budget permitting:** C63 (first VERIFY: if stored
  dates are ISO, text comparison is correct and a Known-Limitations
  line about pre-2.6 books closes it at zero code); C69 as a warn.
- **Before the samples freeze:** side-finding 13 (the 644.57),
  chased by the bookkeeper during the generator top-up.
- **Abandoned formally:** everything else open, each named in a
  KNOWN LIMITATIONS section of the 1.5 release notes, one line
  apiece. Documented boundaries, not silent gaps. G-1 is confirmed
  fixed; the final clearance condition in the pre-tag triage is
  met once the suite is green on that commit.

## Fix-branch response to the pre-tag triage (2026-10-01, morning)

*Written by the fix session. Branch at `86fb02f` plus the docs
commit that carries this section.*

**Ruling 1 (C10 tax tables).** Closed. The `ENGINE_DEBRIS` comment
for `unreferenced_taxtable_copy` says the copy is sometimes
referenced and records what the gate saw (in `c192c87`).

**Ruling 2 (G-1).** Fixed in `c192c87`, and wider than the one gate
the ruling asked for. Every pass of `_migrate_business_shapes` now
keys on a mark the old server left, read once at the top:

| Rows | The old server's mark |
|---|---|
| Entries | `i_disc_type` empty. Desktop always writes `VALUE` or `PERCENT`. |
| Documents | Holds an old entry, or `billto_type = 0` with no `billto_guid`. |
| Slots | piecash's filler columns: `double_val = 0` on a non-double slot, or a NULL `timespec_val` on a non-timespec slot. GnuCash leaves the first NULL and the second at the epoch. |
| Payments | A `P` or `L` transaction whose `date-posted` slot carries those fillers. |
| Lots | An empty `notes` slot carrying those fillers. |

A row with no mark is not read as old, whatever its values. The
billing-term refcount pass now only raises a count that is too low
and skips hidden copies, so desktop's own inflated counts are left
alone. A converting write that finds nothing to convert withdraws
the snapshot it took, so a 1.5 book no longer collects a
`pre-1-5-upgrade` file.

The §9-item-8 test is `tests/test_converter_false_positives.py`:
a 1.5-written book, hand-built desktop shapes (a duplicated
invoice's 10:59 line dates among them), a live engine run when
`gnucash-cli` is installed, and an old-fingerprint control that
must still convert. Each "untouched" case compares every row of
every table before and after. On the gate's own after-pass-1
snapshot the converter reports nothing and changes no row; the
three committed pre-1.5 sample books still convert in full (54, 29
and 12 entries).

**Ruling 3 (C11).** The line is in the README's "Cannot open book"
section and under Known limitations.

**Ruling 4 (maintainer calls).** The release notes name
`total_note` and `discount` in a new "New response fields, all
additive" line under Changed, with the others this branch added.
One correction to the ruling's text: "budget sub-cent rounding left
as is" was true when §10 was first written and stopped being true
at `9ae608f`. `set_budget_amount` now REFUSES an amount finer than
the currency's unit (C40). Nothing rounds silently. If the
bookkeeper would rather it rounded, that is a one-line change; the
refusal matches `reconcile_account` and `apply_credit_note`.

**Ruling 5 (C62).** Closed; listed under Known limitations.

**Ruling 6 as amended.**

- *C63, verified first.* GnuCash 5.12 writes `post_date` as
  `YYYY-MM-DD HH:MM:SS` at 10:59:00, on the gate book and on
  `parity-desktop.gnucash`. Text comparison is correct for those
  rows. Closed at zero code with a Known limitations line. One
  correction to the ruling's wording: the compact form belongs to
  rows GnuCash 2.6 or older wrote (3.0 began writing ISO text;
  2.6.20 was the first release able to read it), so the line says
  "2.6 or older", not "pre-2.6".
- *C69, as a warning.* `86fb02f`. The option is the book slot
  `options/Accounts/Day Threshold for Read-Only Transactions (red
  line)`, a double. Both facts were put to GnuCash 5.12's engine
  rather than read off the source: a book with that slot at 30.0
  answers 30 from `qof_book_get_num_days_autoreadonly`, the same
  number stored as an int64 answers 0, and
  `xaccTransIsReadonlyByPostedDate` says yes to a transaction 40
  days old and no to one exactly 30 days old. The server therefore
  reads only the double and warns strictly before the date. The
  warning rides create, batch create, `enter_statement` (dry run
  and commit), the three update forms, `replace_splits`, void,
  unvoid, and both deletes. Business postings and scheduled
  instantiation do not warn: desktop's post and payment dialogs and
  its since-last-run do not check the option either. Fourteen tests
  in `TestC69TheReadOnlyPeriodIsNamed`.
- *Abandoned formally.* The CHANGELOG's 1.5 entry has a Known
  limitations section, one line for each item in the table at
  "Minor tier: not fixed, and how each was closed" above.
- *Side-finding 13* stays with the bookkeeper.

**Verification at `86fb02f`.** Full suite 3119 passed, 32 skipped.
PostgreSQL gate 88 passed, 16 skipped. MySQL gate (local MariaDB,
scratch database) 88 passed, 16 skipped. Nothing is pushed: the branch is 18 commits ahead
of `origin/fix/v1.5-adversarial-blockers`, plus the docs commit.

**The final clearance condition** (G-1's fix lands with its test,
suite green) is met at `c192c87` and still holds at `86fb02f`.


## Bookkeeper rulings — close-out decisions (2026-10-05)

**Side-finding 13: CLOSED, attributed.** The 644.57 = 352.73 (old
server's zero-value FX legs, valued at value x report rate by the
no-trading-accounts Balance Sheet) + 291.84 (revenue legs GnuCash's
own invoice post writes — a pure-desktop book gaps the same way).
Sums to the cent. Credit: the engine-twin instrument.

**1. Old cross-currency payments: LEAVE AND DOCUMENT.** Conversion
rewrites real, reconciled payment transactions to half-close a
one-report cosmetic gap that desktop-native books share, and would
need its own twin and screen session. The Known Limitations entry
says the gap is inherent without trading accounts, names trading
accounts as GnuCash's answer, and notes 1.5 payments add nothing
to it.

**2. FC-20 guard: APPROVED as proposed.** gnc-mcp slot on the root
at conversion; on later finding an old-server shape in a marked
book, WARN (response + dashboard) that 1.4.4 budget signs need
review — never rewrite. C9/G-1 doctrine forward: no fingerprint,
no flip. Staying out of GnuCash's features frame is C62 holding.

**3. Trading-accounts books: REFUSE cross-currency payments (and
any posting that writes trading splits) until the correct shape is
built.** This is wrong rows, not missing capability: a realized
gain GnuCash's model doesn't book there, trading splits at a
non-GnuCash denominator. Balancing is not the bar. Error names the
limitation and the desktop workaround; one Known Limitations line.
A refusal is a boundary; a wrong row is a defect.

**Concurrences:** unvoid keeping "Voided transaction" notes
(parity beats tidiness); capture-rig cent moves from per-account
rounding; backup cap returning the existing copy on an unchanged
book. The bookkeeper stands ready to run
`BOOKKEEPER_TEST_PLAN_CLOSE_OUT.md` on the maintainer's word.
