# Piecash Complete Reference

> **Version:** 1.2.0 (latest: 1.2.1, July 2024)
> **Source:** [github.com/sdementen/piecash](https://github.com/sdementen/piecash)
> **Docs:** [piecash.readthedocs.io](https://piecash.readthedocs.io/en/master/)
> **License:** MIT
> **Python:** 3.6+
> **Backends:** SQLite3, PostgreSQL, MySQL

Piecash provides a Pythonic interface to GnuCash files stored in SQL databases. Built on SQLAlchemy, it does not require GnuCash itself to be installed.

---

## Table of Contents

1. [Public API Exports](#public-api-exports)
2. [Book Management](#book-management)
3. [Account](#account)
4. [Commodity](#commodity)
5. [Price](#price)
6. [Transaction](#transaction)
7. [Split](#split)
8. [ScheduledTransaction](#scheduledtransaction)
9. [Recurrence](#recurrence)
10. [Lot](#lot)
11. [Budget & BudgetAmount](#budget--budgetamount)
12. [Business Objects](#business-objects)
13. [Slots / KVP System](#slots--kvp-system)
14. [Ledger Export](#ledger-export)
15. [Factories](#factories)
16. [Exceptions](#exceptions)
17. [Object Model Constraints](#object-model-constraints)
18. [Gotchas & Tips](#gotchas--tips)

---

## Public API Exports

Everything importable from `piecash` directly:

```python
# Core
from piecash import (
    Book, Account, ACCOUNT_TYPES, AccountType,
    Transaction, Split, ScheduledTransaction, Lot,
    Commodity, Price,
    create_book, open_book, factories,
    Budget, BudgetAmount,
    Recurrence,
)

# Business
from piecash import (
    Vendor, Customer, Employee, Address,
    Invoice, Job, Taxtable, TaxtableEntry,
)

# Utilities
from piecash import slot, ledger

# Exceptions
from piecash import (
    GnucashException, GncNoActiveSession,
    GncValidationError, GncImbalanceError,
)
```

---

## Book Management

### `create_book()`

Creates a new empty GnuCash book.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sqlite_file` | str | None | SQLite3 file path |
| `uri_conn` | str | None | SQLAlchemy connection string |
| `currency` | str | `'EUR'` | ISO currency symbol for default currency |
| `overwrite` | bool | False | Overwrite existing file |
| `keep_foreign_keys` | bool | False | Preserve foreign key constraints |
| `db_type` | str | None | `"postgres"` or `"mysql"` |
| `db_user` | str | None | Database username |
| `db_password` | str | None | Database password |
| `db_name` | str | None | Database name |
| `db_host` | str | None | Database host |
| `db_port` | int | None | Database port |
| `check_same_thread` | bool | True | SQLite threading flag |
| `pg_template` | str | None | PostgreSQL template (`"template0"` or `"template1"`) |

**Returns:** `Book` (GncSession)

```python
# In-memory
book = piecash.create_book()

# SQLite file
book = piecash.create_book("my_book.gnucash", currency="USD")

# PostgreSQL
book = piecash.create_book(db_type="postgres", db_user="user",
                           db_password="pass", db_name="gnucash")
```

### `open_book()`

Opens an existing GnuCash book.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sqlite_file` | str | None | SQLite3 file path |
| `uri_conn` | str | None | SQLAlchemy connection string |
| `readonly` | bool | True | Read-only mode |
| `open_if_lock` | bool | False | Override file locks |
| `do_backup` | bool | True | Create timestamped backup on write |
| `check_same_thread` | bool | True | SQLite threading flag |
| `check_exists` | bool | True | Validate database existence |

**Returns:** `Book` (GncSession)

```python
# Read-only (default)
book = piecash.open_book("my_book.gnucash")

# Read-write
book = piecash.open_book("my_book.gnucash", readonly=False, do_backup=False)

# Override locks
book = piecash.open_book("my_book.gnucash", open_if_lock=True)
```

### `Book` Class

| Property | Type | Description |
|----------|------|-------------|
| `root_account` | Account | Root of account hierarchy |
| `root_template` | Account | Template account root (for scheduled txns) |
| `default_currency` | Commodity | Book's default currency |
| `uri` | str | Connection string |
| `session` | Session | SQLAlchemy session |
| `is_saved` | bool | True if no unsaved changes |
| `use_trading_accounts` | bool | Enable trading accounts |
| `use_split_action_field` | bool | Use action field for txn numbers |
| `RO_threshold_day` | int | Read-only threshold in days |

**Collection Properties** (all return `CallableList`):

| Property | Returns |
|----------|---------|
| `accounts` | All Account objects |
| `transactions` | All Transaction objects |
| `splits` | All Split objects |
| `commodities` | All Commodity objects |
| `currencies` | Commodities where namespace='CURRENCY' |
| `prices` | All Price objects |
| `invoices` | All Invoice objects |
| `customers` | All Customer objects |
| `vendors` | All Vendor objects |
| `employees` | All Employee objects |
| `taxtables` | All Taxtable objects |

**Business Counters** (all `int`):
`counter_customer`, `counter_vendor`, `counter_employee`, `counter_invoice`, `counter_job`, `counter_bill`, `counter_exp_voucher`, `counter_order`

**Business Company Info** (all `str`):
`business_company_name`, `business_company_ID`, `business_company_address`, `business_company_phone`, `business_company_email`, `business_company_contact`, `business_company_website`

**Methods:**

| Method | Description |
|--------|-------------|
| `save()` | Commit changes to file/database |
| `flush()` | Flush pending changes |
| `cancel()` | Rollback unsaved changes |
| `close()` | Close session, release locks |
| `add(obj)` | Add unlinked object to book |
| `delete(obj)` | Permanently remove object |
| `validate()` | Validate book integrity |
| `get(cls, **kwargs)` | Retrieve object by class + attributes |
| `trading_account(cdty)` | Get/create trading account for commodity |
| `splits_df(additional_fields=None)` | Pandas DataFrame of splits |
| `prices_df()` | Pandas DataFrame of prices |
| `preload()` | Pre-fetch accounts and splits |

**CallableList filtering:**

```python
# By attribute
book.accounts(name="Checking")
book.accounts(type="EXPENSE")
book.accounts(fullname="Expenses:Taxes:Social Security")

# Multi-criteria
book.accounts(commodity=eur, name="Gas")

# By index
book.accounts[10]

# Generic get
book.get(Account, name="Assets", parent=book.root_account)
book.get(Commodity, namespace="CURRENCY", mnemonic="EUR")
book.get(Budget, name="2026 Budget")
```

**Slot/KVP access (DictWrapper):**

```python
book["options/Accounts/Use trading accounts"] = "t"
val = book["options/Accounts/Use trading accounts"]
```

---

## Account

```python
Account(name, type, commodity, parent=None, description="",
        commodity_scu=None, hidden=0, placeholder=0, code="",
        book=None, children=None)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | str | Yes | Account name |
| `type` | str | Yes | Account type (see types below) |
| `commodity` | Commodity | Yes | Currency/commodity for account |
| `parent` | Account | No | Parent account |
| `description` | str | No | Account description |
| `commodity_scu` | int | No | Smallest currency unit |
| `hidden` | int | No | 1 = hidden |
| `placeholder` | int | No | 1 = no transactions allowed |
| `code` | str | No | Account code |
| `book` | Book | No | Associated book |
| `children` | list | No | Child accounts |

**Account Types:** `ASSET`, `BANK`, `CASH`, `CREDIT`, `EQUITY`, `EXPENSE`, `INCOME`, `LIABILITY`, `MUTUAL`, `STOCK`, `TRADING`, `RECEIVABLE`, `PAYABLE`, `ROOT`

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `name` | str | Account name |
| `fullname` | str | Full path (`"Assets:Bank:Checking"`) |
| `type` | str | Account type |
| `sign` | int | 1 for positive-balance types, -1 for negative |
| `code` | str | Account code |
| `description` | str | Description |
| `commodity` | Commodity | Account commodity |
| `commodity_scu` | int | Smallest currency unit |
| `non_std_scu` | int | 1 if scu differs from commodity default |
| `placeholder` | int | 1 = container only |
| `hidden` | int | 1 = hidden |
| `is_template` | bool | True if commodity is template/template |
| `parent` | Account | Parent account |
| `children` | list[Account] | Child accounts |
| `splits` | list[Split] | Splits in this account |
| `lots` | list[Lot] | Lots linked to account |
| `book` | Book | Book (if root account) |
| `budget_amounts` | list[BudgetAmount] | Budget amounts |
| `scheduled_transaction` | ScheduledTransaction | Linked scheduled txn |

**Methods:**

```python
# Balance (with optional recursion, currency conversion, date filter)
acc.get_balance(recurse=True, commodity=None, natural_sign=True, at_date=None)

# Navigate children
acc.children(name="Checking")
acc.children[0]
acc.children(type="CASH")
```

---

## Commodity

```python
Commodity(namespace, mnemonic, fullname, fraction=100, cusip="",
          quote_flag=0, quote_source=None, quote_tz="", book=None)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `namespace` | str | Yes | `"CURRENCY"` for currencies, else stock exchange/custom |
| `mnemonic` | str | Yes | ISO code or ticker symbol |
| `fullname` | str | Yes | Full name |
| `fraction` | int | No | Smallest unit (100 = cents) |
| `cusip` | str | No | CUSIP/ISIN identifier |
| `quote_flag` | int | No | Enable online quotes |
| `quote_source` | str | No | Quote provider |
| `quote_tz` | str | No | Quote timezone |
| `book` | Book | No | Associated book |

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `namespace` | str | Grouping namespace |
| `mnemonic` | str | Symbol/code |
| `fullname` | str | Full name |
| `fraction` | int | Smallest unit divisor |
| `cusip` | str | CUSIP/ISIN |
| `quote_flag` | int | 0 or 1 |
| `quote_source` | str | Quote source name |
| `quote_tz` | str | Timezone |
| `base_currency` | Commodity | Default/quoted currency |
| `accounts` | list[Account] | Accounts using this commodity |
| `transactions` | list[Transaction] | Transactions using this currency |
| `prices` | iterator[Price] | Price history (SQLAlchemy query) |

**Methods:**

```python
# Currency conversion factor
cdty.currency_conversion(target_currency)
# Raises GncConversionError if no conversion path exists

# Update prices from online sources (Yahoo/Quandl)
cdty.update_prices(start_date=None)  # default: 7-day lookback
```

**Creating currencies:**

```python
# From ISO code
usd = factories.create_currency_from_ISO("USD")
book.add(usd)

# Custom commodity
miles = Commodity(namespace="LOYALTY", mnemonic="Miles",
                  fullname="Reward miles", fraction=1000000)
book.add(miles)
```

> **Constraint:** Creating non-ISO currencies in the `"CURRENCY"` namespace is forbidden.

---

## Price

```python
Price(commodity, currency, date, value, type="unknown", source="user:price")
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `commodity` | Commodity | Yes | — | Valued asset |
| `currency` | Commodity | Yes | — | Valuation currency |
| `date` | date | Yes | — | Price date |
| `value` | Decimal | Yes | — | Price value |
| `type` | str | No | `"unknown"` | `last`, `ask`, `bid`, `unknown`, `nav` |
| `source` | str | No | `"user:price"` | Origin identifier |

**Properties:** `commodity`, `currency`, `date`, `value`, `type`, `source`

---

## Transaction

```python
Transaction(currency, description="", notes=None, splits=None,
            enter_date=None, post_date=None, num="")
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `currency` | Commodity | Yes | Transaction currency (**immutable** after set) |
| `description` | str | No | Description |
| `notes` | str | No | Notes (stored as slot) |
| `splits` | list[Split] | No | List of splits |
| `enter_date` | datetime | No | Entry timestamp |
| `post_date` | date | No | Posting date |
| `num` | str | No | Transaction number |

**Properties:** `currency`, `description`, `enter_date`, `post_date`, `num`, `splits`, `scheduled_transaction`, `notes`

**Methods:**

```python
tr.calculate_imbalances()  # Returns value/quantity imbalances
```

**Example:**

```python
tr = Transaction(
    currency=eur,
    description="Grocery shopping",
    post_date=date(2026, 1, 15),
    splits=[
        Split(account=checking, value=-50),
        Split(account=groceries, value=50),
    ]
)
book.flush()
```

> **Constraint:** Splits must balance to zero or `GncImbalanceError` is raised.

---

## Split

```python
Split(account, value, quantity=None, transaction=None, memo="",
      action="", reconcile_date=None, reconcile_state="n", lot=None)
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `account` | Account | Yes | — | Split account |
| `value` | Decimal | Yes | — | Amount in **transaction currency** |
| `quantity` | Decimal | No | = value | Amount in **account commodity** |
| `transaction` | Transaction | No | — | Parent transaction |
| `memo` | str | No | `""` | Split memo |
| `action` | str | No | `""` | Action type |
| `reconcile_date` | datetime | No | — | Reconciliation timestamp |
| `reconcile_state` | str | No | `"n"` | `'n'` (new), `'c'` (cleared), `'y'` (reconciled) |
| `lot` | Lot | No | — | Associated lot |

**Properties:** `transaction`, `account`, `lot`, `memo`, `value`, `quantity`, `reconcile_state`, `reconcile_date`, `action`

**Additional properties:**
- `is_credit` — True if this is a credit split
- `is_debit` — True if this is a debit split

> **Key distinction:** `value` = amount in transaction currency. `quantity` = amount in account commodity. For same-currency, value == quantity. For multi-currency, they differ.

---

## ScheduledTransaction

```python
# No public constructor documented; created via ORM attributes
ScheduledTransaction()
```

**Database table:** `schedxactions`

| Attribute | Type | Description |
|-----------|------|-------------|
| `guid` | str | 32-char hex GUID |
| `name` | str | Schedule name |
| `enabled` | int | 1 = active, 0 = disabled |
| `start_date` | date | Schedule start |
| `end_date` | date | Schedule end (optional) |
| `last_occur` | date | Most recent occurrence |
| `num_occur` | int | Total planned occurrences |
| `rem_occur` | int | Remaining occurrences |
| `auto_create` | int | Auto-create flag |
| `auto_notify` | int | Auto-notify flag |
| `adv_creation` | int | Days advance creation |
| `adv_notify` | int | Days advance notification |
| `instance_count` | int | Total instances created |
| `template_account` | Account | Template source account |
| `recurrence` | Recurrence | Recurrence pattern (one-to-one) |

---

## Recurrence

| Attribute | Type | Description |
|-----------|------|-------------|
| `obj_guid` | str | Parent object GUID (ScheduledTransaction or Budget) |
| `recurrence_mult` | int | Multiplier (e.g., 1 = every period, 2 = every other) |
| `recurrence_period_type` | str | Period type (e.g., `"month"`, `"week"`, `"day"`) |
| `recurrence_period_start` | date | Start date |
| `recurrence_weekend_adjust` | str | Weekend adjustment logic |

---

## Lot

```python
Lot(title, account, notes="", splits=None, is_closed=0)
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `title` | str | Yes | — | Lot identifier |
| `account` | Account | Yes | — | Parent account |
| `notes` | str | No | `""` | Notes |
| `splits` | list | No | None | Associated splits |
| `is_closed` | int | No | 0 | 1 = closed |

> **Note:** `title` and `notes` are `pure_slot_property` — stored in slots table, accessed transparently via ORM.

**Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `is_closed` | int | 1 if closed |
| `account` | Account | Parent account |
| `splits` | CallableList[Split] | Associated splits |
| `title` | str | Lot title (slot) |
| `notes` | str | Lot notes (slot) |

**Validation:**
- All splits must be in the same account as the lot
- Cannot modify splits or account when lot is closed

---

## Budget & BudgetAmount

### Budget

```python
# No public constructor; use ORM attributes
Budget()
```

**Database table:** `budgets`

| Attribute | Type | Description |
|-----------|------|-------------|
| `guid` | str | 32-char hex GUID |
| `name` | str | Budget name |
| `description` | str | Description |
| `num_periods` | int | Number of budget periods |
| `recurrence` | Recurrence | Recurrence pattern (one-to-one) |
| `amounts` | CallableList[BudgetAmount] | Budget amounts per account/period |

### BudgetAmount

**Database table:** `budget_amounts`

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | int | Auto-increment primary key |
| `budget_guid` | str | FK to budgets |
| `account_guid` | str | FK to accounts |
| `period_num` | int | Period index (0-based) |
| `amount` | Decimal | Budgeted amount (stored as num/denom) |
| `account` | Account | Related account |
| `budget` | Budget | Related budget |

---

## Business Objects

### Address

```python
Address(name="", addr1="", addr2="", addr3="", addr4="",
        email="", fax="", phone="")
```

All parameters are `str`, default `""`.

### Customer

```python
Customer(name, currency, id=None, notes="", active=1,
         tax_override=0, credit=0, discount=0, taxtable=None,
         address=None, shipping_address=None, tax_included="USEGLOBAL",
         book=None)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | str | Yes | Customer name |
| `currency` | Commodity | Yes | Customer currency |
| `id` | str | No | Auto-generated 5-digit ID |
| `notes` | str | No | Notes |
| `active` | int | No | 1 = active |
| `tax_override` | int | No | Tax override flag |
| `credit` | Decimal | No | Credit limit |
| `discount` | Decimal | No | Discount percentage |
| `taxtable` | Taxtable | No | Tax table |
| `address` | Address | No | Billing address |
| `shipping_address` | Address | No | Shipping address |
| `tax_included` | str | No | `"USEGLOBAL"`, `"YES"`, `"NO"` |
| `term` | Billterm | No | Billing terms |
| `book` | Book | No | Auto-add to book |

### Vendor

Same parameters as Customer except no `shipping_address`.

### Employee

```python
Employee(name, currency, creditcard_account=None, id=None,
         active=1, acl="", language="", workday=0, rate=0,
         address=None, book=None)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Employee name |
| `currency` | Commodity | Employee currency |
| `creditcard_account` | Account | Credit card account |
| `workday` | Decimal | Standard workday length |
| `rate` | Decimal | Billing rate |
| `language` | str | Language |
| `acl` | str | Access control |

**Implementation notes for gnucash-mcp**

- **Employee does NOT accept `notes`.** Customer and Vendor take a
  `notes=""` kwarg; Employee's schema has no `notes` column and its
  constructor rejects the kwarg. The shared `_create_business_person`
  helper in `book/business.py` passes class-specific fields via a
  `**extra_kwargs` passthrough: Customer/Vendor callers pass
  `notes=notes`, Employee passes none.
- **Auto-ID works the same way:** `book.counter_employee` exists and
  increments by `+1` for each new Employee, matching the
  `counter_customer` / `counter_vendor` pattern.
- **owner_type for Employee documents is 5** (per GnuCash's
  `gncOwner.h`: `GNC_OWNER_CUSTOMER=2`, `GNC_OWNER_JOB=3`,
  `GNC_OWNER_VENDOR=4`, `GNC_OWNER_EMPLOYEE=5`). Employees own
  **expense vouchers** (`book.counter_exp_voucher`), not bills or
  invoices. Expense vouchers are out of scope for the 1.3.0 release
  — Employee's CRUD surface covers create/list/get/delete only, and
  `delete_employee` passes no dependency check because Employees in
  this release have nothing to depend on.
- **Address shape is identical** to Customer and Vendor: the same
  8-field `Address` record (`name, addr1, addr2, addr3, addr4,
  phone, fax, email`).

### Invoice

**Database table:** `invoices`

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | str | Invoice number |
| `date_opened` | datetime | Open date |
| `date_posted` | datetime | Post date |
| `notes` | str | Notes |
| `active` | int | Active flag |
| `currency` | Commodity | Invoice currency |
| `owner_type` | int | Owner type (customer/vendor) |
| `owner_guid` | str | Owner GUID |
| `term` | Billterm | Billing terms |
| `billing_id` | str | Billing ID |
| `post_txn` | Transaction | Posted transaction |
| `post_lot` | Lot | Posted lot |
| `post_account` | Account | Posted account |
| `charge_amt` | Decimal | Charge amount |
| `entries` | CallableList[Entry] | Invoice entries |

### Entry

**Database table:** `entries`

| Attribute | Type | Description |
|-----------|------|-------------|
| `date` | datetime | Entry date |
| `date_entered` | datetime | Date entered |
| `description` | str | Description |
| `action` | str | Action type |
| `notes` | str | Notes |
| `quantity` | Decimal | Quantity |
| `i_price` | Decimal | Invoice price |
| `i_discount` | Decimal | Invoice discount |
| `i_taxable` | int | Taxable flag |
| `b_price` | Decimal | Bill price |
| `b_taxable` | int | Bill taxable flag |
| `invoice` | Invoice | Parent invoice |
| `order` | Order | Parent order |

### Job

```python
Job(name, owner, reference="", active=1)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | str | Job name |
| `owner` | Customer/Vendor | Job owner |
| `reference` | str | Reference |
| `active` | int | Active flag |

Auto-generates 6-digit ID from book counter on `add`.

### Order

**Database table:** `orders`

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | str | Order ID |
| `notes` | str | Notes |
| `reference` | str | Reference |
| `active` | int | Active flag |
| `date_opened` | datetime | Open date |
| `date_closed` | datetime | Close date |
| `entries` | CallableList[Entry] | Order entries |

### Billterm

**Database table:** `billterms`

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | str | Term name |
| `description` | str | Description |
| `type` | str | Term type |
| `duedays` | int | Days until due |
| `discountdays` | int | Discount period days |
| `discount` | Decimal | Discount amount |
| `cutoff` | int | Cutoff day |
| `children` | list | Child terms |
| `parent` | Billterm | Parent term |

### Taxtable

```python
Taxtable(name, entries=None)
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | str | Tax table name |
| `refcount` | int | Reference count |
| `invisible` | int | Invisible flag |
| `entries` | CallableList[TaxtableEntry] | Tax entries |
| `children` | list | Child tables |
| `parent` | Taxtable | Parent table |

### TaxtableEntry

```python
TaxtableEntry(type, amount, account, taxtable=None)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | str | `"value"` or `"percentage"` |
| `amount` | Decimal | Tax amount |
| `account` | Account | Tax account |
| `taxtable` | Taxtable | Parent table |

---

## Slots / KVP System

GnuCash stores arbitrary key-value pairs (slots) on most objects. Piecash provides a `DictWrapper` mixin for dictionary-like access.

### KVP Types

| Enum | Value | Python Type |
|------|-------|-------------|
| `KVP_TYPE_GINT64` | 1 | int |
| `KVP_TYPE_DOUBLE` | 2 | float |
| `KVP_TYPE_NUMERIC` | 3 | Decimal |
| `KVP_TYPE_STRING` | 4 | str |
| `KVP_TYPE_GUID` | 5 | (object reference) |
| `KVP_TYPE_TIMESPEC` | 6 | datetime |
| `KVP_TYPE_BINARY` | 7 | bytes |
| `KVP_TYPE_GLIST` | 8 | list |
| `KVP_TYPE_FRAME` | 9 | dict |
| `KVP_TYPE_GDATE` | 10 | date |

### Slot Classes

- `Slot` — Base polymorphic class (single-table inheritance)
- `SlotInt` — BIGINT storage
- `SlotString` — VARCHAR(4096)
- `SlotDouble` — REAL
- `SlotTime` — DateTime
- `SlotDate` — Date
- `SlotNumeric` — Rational (num/denom)
- `SlotFrame` — Nested dict container
- `SlotList` — Ordered list container
- `SlotGUID` — Object reference

### Usage

```python
# Set slots
book["myintkey"] = 3
book["mystrkey"] = "hello"
book["myboolkey"] = True
book["mydatekey"] = datetime.date.today()
book["mynumerickey"] = decimal.Decimal("12.34567")
book["account"] = book.root_account

# Nested access
book["options/Accounts/Use trading accounts"] = "t"
s = book["options"]["Accounts"]["Use trading accounts"]

# Iterate
for k, v in book.iteritems():
    print(k, v.value, type(v.value))

# Delete
del book["myintkey"]
del book[:]  # delete all
```

### `slot()` Factory Function

```python
from piecash import slot
s = slot(parent_obj, "name", value)
```

Routes to appropriate Slot subclass based on value type.

### `pure_slot_property`

Used by `Lot` for `title` and `notes` — transparently stores/retrieves from slots table.

---

## Ledger Export

```python
from piecash import ledger

# Export entire book
print(ledger(book))

# Export single transaction
print(ledger(transaction))

# Export account
print(ledger(account))
```

Uses `@singledispatch` to handle different object types. Supports multi-currency with quantity/amount syntax. Requires Babel for locale-specific formatting.

---

## Factories

```python
from piecash import factories

# Create currency from ISO code
usd = factories.create_currency_from_ISO("USD")

# Create stock from Yahoo symbol (requires yahoo-finance)
aapl = factories.create_stock_from_symbol("AAPL", book=book)

# Create standard stock accounts
stock_acc, income_accs = factories.create_stock_accounts(
    cdty=aapl,
    broker_account=broker,
    income_account=income,
    income_account_types="D/CL/CS/I"
    # D=Dividend, CL=Cap Gain Long, CS=Cap Gain Short, I=Interest
)
```

---

## Exceptions

| Exception | Parent | Raised When |
|-----------|--------|-------------|
| `GnucashException` | Exception | Base for all piecash errors |
| `GncNoActiveSession` | GnucashException | No active GnuCash session |
| `GncValidationError` | GnucashException | Validation fails |
| `GncImbalanceError` | GncValidationError | Transaction splits don't balance |
| `GncConversionError` | GnucashException | Currency conversion impossible |
| `GncCommodityError` | GnucashException | Commodity-related error |
| `GncPriceError` | GnucashException | Price-related error |

---

## Object Model Constraints

### Book
- One and only one Book per GnuCash document
- Exactly one `root_account` and one `root_template`

### Commodity
- Currency commodities must have `namespace == "CURRENCY"`
- Cannot create non-ISO currencies in CURRENCY namespace
- Stock commodities have `namespace != "CURRENCY"`

### Account
- Only two ROOT accounts allowed (root_account + root_template)
- Placeholder accounts cannot have transaction splits
- Account type is constrained by parent type
- Trading accounts are conditional on `use_trading_accounts` setting
- `fullname` is hierarchical: `"Assets:Bank:Checking"`

### Transaction & Split
- Split values must sum to zero (else `GncImbalanceError`)
- `value` uses numerator/denominator internally
- With trading accounts: quantities per commodity must also balance
- Voided transactions set all splits to `'v'` reconcile state
- Voided transactions require 4 metadata slots; voided splits require 2

### Reconcile States
- `'n'` — New (unreconciled)
- `'c'` — Cleared
- `'y'` — Reconciled
- `'f'` — Frozen
- `'v'` — Voided

### Price
- Values stored as numerator/denominator
- Date is UTC

---

## Gotchas & Tips

1. **Books must be closed after use.** Use context managers or call `book.close()`.

2. **Default is read-only.** Pass `readonly=False` to `open_book()` for write access.

3. **Backups are automatic.** `open_book(readonly=False)` creates timestamped backups. Suppress with `do_backup=False`.

4. **value vs quantity.** `Split.value` = transaction currency amount. `Split.quantity` = account commodity amount. Same for single-currency; different for multi-currency.

5. **Slot ORM issues.** Direct ORM queries on the Slot table may fail due to polymorphic relationship conflicts. Use raw SQL via `sqlalchemy.text()` for slot reads and deletes when needed.

6. **KVP_Type enum.** `SlotType` TypeDecorator expects `KVP_Type` enum values (e.g., `KVP_Type.KVP_TYPE_STRING`), not raw integers.

7. **Detached instances.** Must capture ORM object attributes before closing the session — accessing them after close raises `DetachedInstanceError`.

8. **Lot constructor is open.** `Lot(title=..., account=..., notes=..., is_closed=0)` works directly.

9. **`pure_slot_property`** — used by Lot for `title`/`notes`. Stored in slots table, accessed transparently.

10. **`create_book()` defaults to EUR.** Pass `currency="USD"` to change.

11. **`fraction` parameter:** 100 for standard currencies (cents), 10000 for 4 decimal places (shares), 1000000 for crypto.

12. **File locks.** SQLite books may have stale locks. Use `open_if_lock=True` to override.

13. **Pandas integration.** Install with `pip install piecash[pandas]` for `splits_df()` and `prices_df()`.

14. **Online quotes.** `Commodity.update_prices()` uses Yahoo Finance for stocks and Quandl for currencies.

15. **Always backup.** "Always do a backup of your gnucash file/DB before using piecash."

---

## CLI Commands

Piecash includes a command-line interface:

```bash
# Export customers to CSV
piecash export customers book.gnucash

# Export vendors to CSV
piecash export vendors book.gnucash

# Export prices to CSV
piecash export prices book.gnucash

# Convert to ledger-cli format
piecash ledger book.gnucash

# Create empty book
piecash create book.gnucash --currency USD

# Dump SQL schema
piecash schema
```

---

## Package Structure

```
piecash/
├── __init__.py          # Public exports
├── _common.py           # Exceptions, Recurrence, CallableList, utilities
├── _declbase.py         # Declarative base classes
├── budget.py            # Budget, BudgetAmount
├── kvp.py               # Slot/KVP system
├── ledger.py            # Ledger-cli export
├── metadata.py          # Version metadata
├── sa_extra.py          # SQLAlchemy extensions
├── yahoo_client.py      # Yahoo Finance client
├── core/
│   ├── account.py       # Account, AccountType
│   ├── book.py          # Book class
│   ├── commodity.py     # Commodity, Price
│   ├── currency_ISO.py  # ISO currency data (ISO_type namedtuple)
│   ├── factories.py     # create_currency_from_ISO, create_stock_from_symbol, create_stock_accounts
│   ├── session.py       # create_book, open_book, build_uri, Version
│   └── transaction.py   # Transaction, Split, ScheduledTransaction, Lot
├── business/
│   ├── invoice.py       # Invoice, Entry, Job, Order, Billterm
│   ├── person.py        # Customer, Vendor, Employee, Address
│   └── tax.py           # Taxtable, TaxtableEntry
└── scripts/             # CLI scripts
```
