"""Pytest fixtures for gnucash-mcp tests."""

import typing

import pytest
import piecash
from piecash import factories
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal


@pytest.fixture
def test_book(tmp_path: Path) -> Path:
    """Create a temporary GnuCash book with sample data.

    Creates a book with:
    - Standard account hierarchy (Assets, Liabilities, Income, Expenses, Equity)
    - A checking account under Assets
    - Sample transactions

    Returns:
        Path to the temporary GnuCash SQLite file.
    """
    book_path = tmp_path / "test.gnucash"

    # Create a new book with USD as default currency
    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    # Get the root account and currency
    root = book.root_account
    usd = book.default_currency

    # Create standard account hierarchy - must add to session
    assets = piecash.Account(
        name="Assets",
        type="ASSET",
        parent=root,
        commodity=usd,
        placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking",
        type="BANK",
        parent=assets,
        commodity=usd,
    )
    book.session.add(checking)

    liabilities = piecash.Account(
        name="Liabilities",
        type="LIABILITY",
        parent=root,
        commodity=usd,
        placeholder=True,
    )
    book.session.add(liabilities)

    income = piecash.Account(
        name="Income",
        type="INCOME",
        parent=root,
        commodity=usd,
        placeholder=True,
    )
    book.session.add(income)

    salary = piecash.Account(
        name="Salary",
        type="INCOME",
        parent=income,
        commodity=usd,
    )
    book.session.add(salary)

    expenses = piecash.Account(
        name="Expenses",
        type="EXPENSE",
        parent=root,
        commodity=usd,
        placeholder=True,
    )
    book.session.add(expenses)

    groceries = piecash.Account(
        name="Groceries",
        type="EXPENSE",
        parent=expenses,
        commodity=usd,
    )
    book.session.add(groceries)

    equity = piecash.Account(
        name="Equity",
        type="EQUITY",
        parent=root,
        commodity=usd,
        placeholder=True,
    )
    book.session.add(equity)

    opening_balance = piecash.Account(
        name="Opening Balance",
        type="EQUITY",
        parent=equity,
        commodity=usd,
    )
    book.session.add(opening_balance)

    # Save accounts first
    book.save()

    # Create sample transactions
    # Opening balance: $1000 in checking
    t1 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2024, 1, 1),
        splits=[
            piecash.Split(account=checking, value=Decimal("1000.00")),
            piecash.Split(account=opening_balance, value=Decimal("-1000.00")),
        ],
    )
    book.session.add(t1)

    # Salary deposit: $2000
    t2 = piecash.Transaction(
        currency=usd,
        description="Salary Deposit",
        post_date=date(2024, 1, 15),
        splits=[
            piecash.Split(account=checking, value=Decimal("2000.00")),
            piecash.Split(account=salary, value=Decimal("-2000.00")),
        ],
    )
    book.session.add(t2)

    # Grocery expense: $150
    t3 = piecash.Transaction(
        currency=usd,
        description="Weekly Groceries",
        post_date=date(2024, 1, 20),
        splits=[
            piecash.Split(account=groceries, value=Decimal("150.00")),
            piecash.Split(account=checking, value=Decimal("-150.00")),
        ],
    )
    book.session.add(t3)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def multi_currency_book(tmp_path: Path) -> Path:
    """Create a GnuCash book with multi-currency transactions.

    Creates a USD-default book with:
    - USD checking account
    - EUR savings account (commodity=EUR)
    - A cross-currency transfer: $1100 USD -> 1000 EUR
    - A USD salary deposit: $3000
    - A USD grocery expense: $200

    The cross-currency transaction has value != quantity:
    - EUR savings split: value=1100 (USD), quantity=1000 (EUR)
    - USD checking split: value=-1100 (USD), quantity=-1100 (USD)

    Returns:
        Path to the temporary GnuCash SQLite file.
    """
    book_path = tmp_path / "multi_currency.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency
    eur = factories.create_currency_from_ISO("EUR")
    book.session.add(eur)

    # Accounts
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    savings_eur = piecash.Account(
        name="Euro Savings", type="BANK", parent=assets, commodity=eur,
    )
    book.session.add(savings_eur)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    salary = piecash.Account(
        name="Salary", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(salary)

    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(expenses)

    groceries = piecash.Account(
        name="Groceries", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(groceries)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity, commodity=usd,
    )
    book.session.add(opening)

    book.save()

    # Transaction 1: Opening balance $5000 in checking
    t1 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2024, 1, 1),
        splits=[
            piecash.Split(account=checking, value=Decimal("5000")),
            piecash.Split(account=opening, value=Decimal("-5000")),
        ],
    )
    book.session.add(t1)

    # Transaction 2: Salary $3000
    t2 = piecash.Transaction(
        currency=usd,
        description="Salary",
        post_date=date(2024, 1, 15),
        splits=[
            piecash.Split(account=checking, value=Decimal("3000")),
            piecash.Split(account=salary, value=Decimal("-3000")),
        ],
    )
    book.session.add(t2)

    # Transaction 3: Cross-currency transfer USD -> EUR
    # $1100 buys 1000 EUR (rate ~1.10 USD/EUR)
    # Transaction currency is USD
    t3 = piecash.Transaction(
        currency=usd,
        description="Transfer to EUR savings",
        post_date=date(2024, 1, 20),
        splits=[
            piecash.Split(
                account=checking,
                value=Decimal("-1100"),
                quantity=Decimal("-1100"),
            ),
            piecash.Split(
                account=savings_eur,
                value=Decimal("1100"),
                quantity=Decimal("1000"),  # 1000 EUR received
            ),
        ],
    )
    book.session.add(t3)

    # Transaction 4: Grocery expense $200
    t4 = piecash.Transaction(
        currency=usd,
        description="Groceries",
        post_date=date(2024, 1, 25),
        splits=[
            piecash.Split(account=groceries, value=Decimal("200")),
            piecash.Split(account=checking, value=Decimal("-200")),
        ],
    )
    book.session.add(t4)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def budget_book(tmp_path: Path) -> Path:
    """Create a GnuCash book with data suitable for budget testing.

    Creates a USD-default book with:
    - Accounts: Assets:Checking, Income:Salary, Expenses:Groceries,
      Expenses:Dining, Expenses:Entertainment, Equity:Opening Balance
    - Jan 2026: Salary $5000, Groceries $120+$180+$200=$500,
      Dining $85+$65=$150
    - Feb 2026: Salary $5000, Groceries $150, Dining $95

    Returns:
        Path to the temporary GnuCash SQLite file.
    """
    book_path = tmp_path / "budget_test.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency

    # Accounts
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    salary = piecash.Account(
        name="Salary", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(salary)

    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(expenses)

    groceries = piecash.Account(
        name="Groceries", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(groceries)

    dining = piecash.Account(
        name="Dining", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(dining)

    entertainment = piecash.Account(
        name="Entertainment", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(entertainment)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity, commodity=usd,
    )
    book.session.add(opening)

    book.save()

    # Opening balance: $20000 in checking
    t0 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2025, 12, 31),
        splits=[
            piecash.Split(account=checking, value=Decimal("20000")),
            piecash.Split(account=opening, value=Decimal("-20000")),
        ],
    )
    book.session.add(t0)

    # === January 2026 ===

    # Salary $5000
    t1 = piecash.Transaction(
        currency=usd,
        description="January Salary",
        post_date=date(2026, 1, 15),
        splits=[
            piecash.Split(account=checking, value=Decimal("5000")),
            piecash.Split(account=salary, value=Decimal("-5000")),
        ],
    )
    book.session.add(t1)

    # Groceries: $120
    t2 = piecash.Transaction(
        currency=usd,
        description="Grocery Store A",
        post_date=date(2026, 1, 5),
        splits=[
            piecash.Split(account=groceries, value=Decimal("120")),
            piecash.Split(account=checking, value=Decimal("-120")),
        ],
    )
    book.session.add(t2)

    # Groceries: $180
    t3 = piecash.Transaction(
        currency=usd,
        description="Grocery Store B",
        post_date=date(2026, 1, 12),
        splits=[
            piecash.Split(account=groceries, value=Decimal("180")),
            piecash.Split(account=checking, value=Decimal("-180")),
        ],
    )
    book.session.add(t3)

    # Groceries: $200
    t4b = piecash.Transaction(
        currency=usd,
        description="Grocery Store C",
        post_date=date(2026, 1, 20),
        splits=[
            piecash.Split(account=groceries, value=Decimal("200")),
            piecash.Split(account=checking, value=Decimal("-200")),
        ],
    )
    book.session.add(t4b)

    # Dining: $85
    t5 = piecash.Transaction(
        currency=usd,
        description="Restaurant A",
        post_date=date(2026, 1, 8),
        splits=[
            piecash.Split(account=dining, value=Decimal("85")),
            piecash.Split(account=checking, value=Decimal("-85")),
        ],
    )
    book.session.add(t5)

    # Dining: $65
    t6 = piecash.Transaction(
        currency=usd,
        description="Restaurant B",
        post_date=date(2026, 1, 22),
        splits=[
            piecash.Split(account=dining, value=Decimal("65")),
            piecash.Split(account=checking, value=Decimal("-65")),
        ],
    )
    book.session.add(t6)

    # === February 2026 ===

    # Salary $5000
    t7 = piecash.Transaction(
        currency=usd,
        description="February Salary",
        post_date=date(2026, 2, 15),
        splits=[
            piecash.Split(account=checking, value=Decimal("5000")),
            piecash.Split(account=salary, value=Decimal("-5000")),
        ],
    )
    book.session.add(t7)

    # Groceries: $150
    t8 = piecash.Transaction(
        currency=usd,
        description="Grocery Store D",
        post_date=date(2026, 2, 10),
        splits=[
            piecash.Split(account=groceries, value=Decimal("150")),
            piecash.Split(account=checking, value=Decimal("-150")),
        ],
    )
    book.session.add(t8)

    # Dining: $95
    t9 = piecash.Transaction(
        currency=usd,
        description="Restaurant C",
        post_date=date(2026, 2, 18),
        splits=[
            piecash.Split(account=dining, value=Decimal("95")),
            piecash.Split(account=checking, value=Decimal("-95")),
        ],
    )
    book.session.add(t9)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def scheduled_book(tmp_path: Path) -> Path:
    """Create a GnuCash book for scheduled transaction testing.

    Creates a USD-default book with:
    - Accounts: Assets:Checking, Expenses:Rent, Expenses:Utilities,
      Income:Salary, Equity:Opening Balance
    - Opening balance: $10000 in checking

    No pre-existing scheduled transactions — tests create their own.

    Returns:
        Path to the temporary GnuCash SQLite file.
    """
    book_path = tmp_path / "scheduled_test.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency

    # Accounts
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    salary = piecash.Account(
        name="Salary", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(salary)

    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(expenses)

    rent = piecash.Account(
        name="Rent", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(rent)

    utilities = piecash.Account(
        name="Utilities", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(utilities)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity, commodity=usd,
    )
    book.session.add(opening)

    book.save()

    # Opening balance: $10000 in checking
    t0 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2025, 12, 31),
        splits=[
            piecash.Split(account=checking, value=Decimal("10000")),
            piecash.Split(account=opening, value=Decimal("-10000")),
        ],
    )
    book.session.add(t0)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def investment_book(tmp_path: Path) -> Path:
    """Create a GnuCash book with investment accounts for lot testing.

    Creates a USD-default book with:
    - Assets:Checking (BANK, USD)
    - Assets:Investments (ASSET, USD, placeholder)
    - Assets:Investments:VTSAX (MUTUAL, commodity=VTSAX)
    - Income:Capital Gains (INCOME, USD)
    - Equity:Opening Balance (EQUITY, USD)
    - VTSAX commodity (FUND namespace, fraction=10000)
    - VTSAX price: $125.00 on 2026-01-15
    - Opening balance: $10000 in checking

    No pre-existing lots or investment transactions.

    Returns:
        Path to the temporary GnuCash SQLite file.
    """
    book_path = tmp_path / "investment_test.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency

    # Create VTSAX commodity
    vtsax = piecash.Commodity(
        namespace="FUND",
        mnemonic="VTSAX",
        fullname="Vanguard Total Stock Market",
        fraction=10000,
    )
    book.session.add(vtsax)

    # Accounts
    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    investments = piecash.Account(
        name="Investments", type="ASSET", parent=assets,
        commodity=usd, placeholder=True,
    )
    book.session.add(investments)

    vtsax_acct = piecash.Account(
        name="VTSAX", type="MUTUAL", parent=investments,
        commodity=vtsax,
    )
    book.session.add(vtsax_acct)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    cap_gains = piecash.Account(
        name="Capital Gains", type="INCOME", parent=income,
        commodity=usd,
    )
    book.session.add(cap_gains)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity,
        commodity=usd,
    )
    book.session.add(opening)

    book.save()

    # Record VTSAX price
    p = piecash.Price(
        commodity=vtsax,
        currency=usd,
        date=date(2026, 1, 15),
        value=Decimal("125"),
        type="nav",
        source="user:price",
    )
    book.session.add(p)

    # Opening balance: $10000 in checking
    t0 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2025, 12, 31),
        splits=[
            piecash.Split(account=checking, value=Decimal("10000")),
            piecash.Split(account=opening, value=Decimal("-10000")),
        ],
    )
    book.session.add(t0)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def business_book(tmp_path: Path) -> Path:
    """Create a GnuCash book with business-ready accounts.

    Creates a USD-default book with:
    - Assets:Checking (BANK, USD)
    - Assets:Accounts Receivable (RECEIVABLE, USD)
    - Liabilities:Accounts Payable (PAYABLE, USD)
    - Income:Sales, Income:Consulting (INCOME, USD)
    - Expenses:Office Supplies, Expenses:Services (EXPENSE, USD)
    - Equity:Opening Balance (EQUITY, USD)
    - Opening balance: $10000 in checking

    No pre-existing customers/vendors — tests create their own.
    """
    book_path = tmp_path / "business_test.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency

    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    ar = piecash.Account(
        name="Accounts Receivable", type="RECEIVABLE", parent=assets,
        commodity=usd,
    )
    book.session.add(ar)

    liabilities = piecash.Account(
        name="Liabilities", type="LIABILITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(liabilities)

    ap = piecash.Account(
        name="Accounts Payable", type="PAYABLE", parent=liabilities,
        commodity=usd,
    )
    book.session.add(ap)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    sales = piecash.Account(
        name="Sales", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(sales)

    consulting = piecash.Account(
        name="Consulting", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(consulting)

    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(expenses)

    office_supplies = piecash.Account(
        name="Office Supplies", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(office_supplies)

    services = piecash.Account(
        name="Services", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(services)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity, commodity=usd,
    )
    book.session.add(opening)

    book.save()

    t0 = piecash.Transaction(
        currency=usd,
        description="Opening Balance",
        post_date=date(2025, 12, 31),
        splits=[
            piecash.Split(account=checking, value=Decimal("10000")),
            piecash.Split(account=opening, value=Decimal("-10000")),
        ],
    )
    book.session.add(t0)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def debt_book(tmp_path: Path) -> Path:
    """Create a GnuCash book with credit card and loan accounts for debt payoff testing.

    Creates a USD-default book with:
    - Assets:Checking (BANK, $10000)
    - Liabilities:Visa (CREDIT, $4800 balance after $200 payment, APR 23.49%)
    - Liabilities:Mastercard (CREDIT, $3000 balance, APR 18.99%)
    - Liabilities:Car Loan (LIABILITY, $15000 balance, APR 6.5%)
    - Expenses:Groceries (EXPENSE)
    - Income:Salary (INCOME)
    - Equity:Opening Balance (EQUITY)
    - A payment transaction on Visa ($200 payment on 2026-02-15)

    APR slots are set on debt accounts. minimum_payment slot set on Car Loan only.
    credit_limit slot set on Visa only.
    """
    book_path = tmp_path / "debt_test.gnucash"

    book = piecash.create_book(
        str(book_path),
        currency="USD",
        overwrite=True,
    )

    root = book.root_account
    usd = book.default_currency

    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(assets)

    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    book.session.add(checking)

    liabilities = piecash.Account(
        name="Liabilities", type="LIABILITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(liabilities)

    visa = piecash.Account(
        name="Visa", type="CREDIT", parent=liabilities, commodity=usd,
    )
    book.session.add(visa)

    mastercard = piecash.Account(
        name="Mastercard", type="CREDIT", parent=liabilities, commodity=usd,
    )
    book.session.add(mastercard)

    car_loan = piecash.Account(
        name="Car Loan", type="LIABILITY", parent=liabilities, commodity=usd,
    )
    book.session.add(car_loan)

    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(income)

    salary = piecash.Account(
        name="Salary", type="INCOME", parent=income, commodity=usd,
    )
    book.session.add(salary)

    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(expenses)

    groceries = piecash.Account(
        name="Groceries", type="EXPENSE", parent=expenses, commodity=usd,
    )
    book.session.add(groceries)

    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    book.session.add(equity)

    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity, commodity=usd,
    )
    book.session.add(opening)

    book.save()

    # Set APR slots on debt accounts
    visa["apr"] = "23.49"
    visa["credit_limit"] = "10000"
    mastercard["apr"] = "18.99"
    car_loan["apr"] = "6.50"
    car_loan["minimum_payment"] = "350"

    book.save()

    # Visa: $5000 in charges
    t1 = piecash.Transaction(
        currency=usd,
        description="Visa Charges",
        post_date=date(2026, 1, 1),
        splits=[
            piecash.Split(account=visa, value=Decimal("-5000")),
            piecash.Split(account=groceries, value=Decimal("5000")),
        ],
    )
    book.session.add(t1)

    # Mastercard: $3000 in charges
    t2 = piecash.Transaction(
        currency=usd,
        description="Mastercard Charges",
        post_date=date(2026, 1, 1),
        splits=[
            piecash.Split(account=mastercard, value=Decimal("-3000")),
            piecash.Split(account=groceries, value=Decimal("3000")),
        ],
    )
    book.session.add(t2)

    # Car Loan: $15000 balance
    t3 = piecash.Transaction(
        currency=usd,
        description="Car Loan",
        post_date=date(2026, 1, 1),
        splits=[
            piecash.Split(account=car_loan, value=Decimal("-15000")),
            piecash.Split(account=opening, value=Decimal("15000")),
        ],
    )
    book.session.add(t3)

    # Payment on Visa: $200 (reduces debt)
    t4 = piecash.Transaction(
        currency=usd,
        description="Visa Payment",
        post_date=date(2026, 2, 15),
        splits=[
            piecash.Split(account=visa, value=Decimal("200")),
            piecash.Split(account=checking, value=Decimal("-200")),
        ],
    )
    book.session.add(t4)

    book.save()
    book.close()

    return book_path


@pytest.fixture
def localized_book(tmp_path: Path) -> Path:
    """A book with a German (`de_DE`-style) account hierarchy.

    EUR default. Account *names* are German; account *types* are the
    locale-invariant enum, exactly as a real localized GnuCash book is
    laid out. Crucially there is **no account literally named "Income"
    or "Expenses"** — so any code that resolves a helper-account parent
    by English name throws here, while type-based resolution succeeds.

    Chart:
    - Aktiva (ASSET) → Girokonto (BANK), Forderungen (RECEIVABLE)
    - Fremdkapital (LIABILITY) → Verbindlichkeiten (PAYABLE)
    - Erträge (INCOME) → Umsatzerlöse (INCOME)
    - Aufwand (EXPENSE) → Bürobedarf (EXPENSE)
    - Eigenkapital (EQUITY) → Anfangsbestand (EQUITY)
    - Opening balance: €10,000 in Girokonto.
    """
    book_path = tmp_path / "localized.gnucash"

    book = piecash.create_book(
        str(book_path), currency="EUR", overwrite=True,
    )
    root = book.root_account
    eur = book.default_currency

    aktiva = piecash.Account(
        name="Aktiva", type="ASSET", parent=root,
        commodity=eur, placeholder=True,
    )
    girokonto = piecash.Account(
        name="Girokonto", type="BANK", parent=aktiva, commodity=eur,
    )
    piecash.Account(
        name="Forderungen", type="RECEIVABLE", parent=aktiva, commodity=eur,
    )

    fremdkapital = piecash.Account(
        name="Fremdkapital", type="LIABILITY", parent=root,
        commodity=eur, placeholder=True,
    )
    piecash.Account(
        name="Verbindlichkeiten", type="PAYABLE", parent=fremdkapital,
        commodity=eur,
    )

    ertraege = piecash.Account(
        name="Erträge", type="INCOME", parent=root,
        commodity=eur, placeholder=True,
    )
    piecash.Account(
        name="Umsatzerlöse", type="INCOME", parent=ertraege, commodity=eur,
    )

    aufwand = piecash.Account(
        name="Aufwand", type="EXPENSE", parent=root,
        commodity=eur, placeholder=True,
    )
    piecash.Account(
        name="Bürobedarf", type="EXPENSE", parent=aufwand, commodity=eur,
    )

    eigenkapital = piecash.Account(
        name="Eigenkapital", type="EQUITY", parent=root,
        commodity=eur, placeholder=True,
    )
    anfangsbestand = piecash.Account(
        name="Anfangsbestand", type="EQUITY", parent=eigenkapital,
        commodity=eur,
    )

    book.save()

    book.session.add(piecash.Transaction(
        currency=eur,
        description="Anfangsbestand",
        post_date=date(2025, 12, 31),
        splits=[
            piecash.Split(account=girokonto, value=Decimal("10000")),
            piecash.Split(account=anfangsbestand, value=Decimal("-10000")),
        ],
    ))

    book.save()
    book.close()

    return book_path


class PathologicalBook(typing.NamedTuple):
    """Handle returned by the ``pathological_book`` fixture.

    Bundles the book path with the GUIDs tests need to assert that
    specific pathological rows never resurface in report output.
    """

    path: Path
    voided_txn_guid: str
    template_txn_guid: str
    usd_invoice_id: str
    eur_invoice_id: str


@pytest.fixture
def pathological_book(tmp_path: Path) -> PathologicalBook:
    """A book of legal-but-unexercised shapes the synthetic sample
    books structurally cannot produce.

    The final adversarial review of v1.3.1 observed that every
    confirmed defect lived in a shape neither Alex's nor Lin Wei's
    book contains — the bookkeeper loop and the sample corpus can't
    reach them. This fixture packs all of those shapes into one
    small USD book so the report surfaces can be regression-tested
    against the combination:

    1. **Parent with direct splits** — Assets:Checking holds real
       money AND has a child (``Sub``). Leaf-only iteration drops
       its balance (review C1).
    2. **Placeholder with direct splits** — Assets:Savings is
       ``placeholder=1`` with a direct 500 split (raw piecash; the
       wrapper refuses placeholder targets by design). Placeholder
       skips delete real money (C1).
    3. **Properly voided transaction** — a 150.00 grocery run,
       voided via the API. Zombie splits must stay out of every
       aggregate.
    4. **Overpaid invoice lot** — 500.00 invoice, 400 paid via
       ``pay_invoice``, then a manual −300 payment split assigned
       to the lot (the guard added for C2 blocks overpaying through
       the API, but legacy/desktop data can carry negative lots).
    5. **Foreign-denominated A/R** — EUR receivable account in a
       USD book, EUR 1,000 invoice posted at 1.10 (C10 territory).
    6. **Desktop-created SX template** — a real Transaction whose
       splits post to an account under ``book.root_template``,
       exactly as the GnuCash desktop UI persists scheduled-
       transaction recipes. Our MCP uses a splits-json slot
       instead, so server-built books never contain these rows
       (C9).

    Whole-dollar amounts keep cross-surface expectations exact:

    - Checking 10,000 − 500 + 400 + 300 = 10,200.00
    - Savings (placeholder) 500.00
    - A/R USD 500 − 400 − 300 = −200.00 (overpaid)
    - A/R EUR 1,000 × 1.10 = 1,100.00
    - **Assets total 11,600.00**, liabilities 0, net worth
      11,600.00, retained earnings 1,600.00.
    """
    from gnucash_mcp.book import GnuCashBook

    book_path = tmp_path / "pathological.gnucash"
    book = piecash.create_book(
        str(book_path), currency="USD", overwrite=True,
    )
    usd = book.default_currency
    root = book.root_account

    assets = piecash.Account(
        name="Assets", type="ASSET", parent=root,
        commodity=usd, placeholder=True,
    )
    checking = piecash.Account(
        name="Checking", type="BANK", parent=assets, commodity=usd,
    )
    # Shape 1: Checking is a PARENT — it has both direct splits
    # (below) and this child.
    piecash.Account(
        name="Sub", type="BANK", parent=checking, commodity=usd,
    )
    # Shape 2: will carry a direct split, then get flipped to
    # placeholder below — piecash refuses new splits against an
    # account that is ALREADY a placeholder, so the shape can only
    # arise in this order (which is also how it arises in the wild:
    # desktop GnuCash lets you mark any account placeholder later).
    savings = piecash.Account(
        name="Savings", type="BANK", parent=assets, commodity=usd,
    )
    piecash.Account(
        name="Accounts Receivable", type="RECEIVABLE",
        parent=assets, commodity=usd,
    )
    eur = factories.create_currency_from_ISO("EUR")
    book.session.add(eur)
    # Shape 5: receivable denominated in a foreign currency.
    piecash.Account(
        name="Accounts Receivable EUR", type="RECEIVABLE",
        parent=assets, commodity=eur,
    )
    income = piecash.Account(
        name="Income", type="INCOME", parent=root,
        commodity=usd, placeholder=True,
    )
    piecash.Account(
        name="Sales", type="INCOME", parent=income, commodity=usd,
    )
    expenses = piecash.Account(
        name="Expenses", type="EXPENSE", parent=root,
        commodity=usd, placeholder=True,
    )
    piecash.Account(
        name="Groceries", type="EXPENSE", parent=expenses,
        commodity=usd,
    )
    equity = piecash.Account(
        name="Equity", type="EQUITY", parent=root,
        commodity=usd, placeholder=True,
    )
    opening = piecash.Account(
        name="Opening Balance", type="EQUITY", parent=equity,
        commodity=usd,
    )
    book.session.add(piecash.Price(
        commodity=eur, currency=usd, date=date(2026, 3, 10),
        value="1.10", source="user:test", type="nav",
    ))
    book.save()

    book.session.add(piecash.Transaction(
        currency=usd, description="Opening balance",
        post_date=date(2026, 1, 5),
        splits=[
            piecash.Split(account=checking, value=Decimal("10000")),
            piecash.Split(account=opening, value=Decimal("-10000")),
        ],
    ))
    # Direct split on the placeholder (wrapper validation refuses
    # placeholder targets, so this only arises from desktop GnuCash
    # or legacy data — built raw here on purpose).
    book.session.add(piecash.Transaction(
        currency=usd, description="Transfer to savings",
        post_date=date(2026, 1, 15),
        splits=[
            piecash.Split(account=savings, value=Decimal("500")),
            piecash.Split(account=checking, value=Decimal("-500")),
        ],
    ))
    # Shape 6: desktop-style SX template — real Transaction rows
    # against an account under root_template (see
    # ``_seed_template_transaction`` in test_book.py for the
    # original recipe). Dated 2020 so an unfiltered surface that
    # counts it visibly stretches the book's first-activity date.
    template_acct = piecash.Account(
        name="Mortgage Template", type="BANK",
        parent=book.root_template, commodity=usd,
    )
    book.session.add(template_acct)
    # Desktop GnuCash also creates a ``template`` pseudo-commodity
    # and denominates template accounts in it. A second template
    # account holds it here so the commodity surfaces
    # (list_commodities, the dashboard Commodities line, stale-price
    # warnings) can be probed for the leak.
    template_commodity = piecash.Commodity(
        namespace="template", mnemonic="template",
        fullname="template", fraction=1,
    )
    book.session.add(template_commodity)
    piecash.Account(
        name="Recipe Holder", type="BANK",
        parent=book.root_template, commodity=template_commodity,
    )
    book.flush()
    template_txn = piecash.Transaction(
        currency=usd, description="Mortgage Payment",
        post_date=date(2020, 1, 1),
        splits=[
            piecash.Split(account=template_acct, value=Decimal("2485")),
            piecash.Split(account=template_acct, value=Decimal("-2485")),
        ],
    )
    book.session.add(template_txn)
    book.save()
    # Now that Savings carries a real split, freeze it (shape 2).
    savings.placeholder = 1
    book.save()
    template_txn_guid = template_txn.guid
    book.close()

    gb = GnuCashBook(str(book_path))

    # Shape 3: voided transaction. Created and voided through the
    # API so the slot bookkeeping matches what the server produces.
    created = gb.create_transaction(
        description="Groceries run",
        splits=[
            {"account": "Expenses:Groceries", "amount": "150.00"},
            {"account": "Assets:Checking", "amount": "-150.00"},
        ],
        trans_date=date(2026, 2, 1),
        check_duplicates=False,
    )
    voided_txn_guid = created["guid"]
    gb.void_transaction(guid=voided_txn_guid, reason="entered twice")

    # Shape 4: overpaid invoice lot.
    gb.create_customer(name="Acme Corp")
    usd_invoice = gb.create_invoice(
        customer_id="000001", date_opened="2026-03-01",
    )
    usd_invoice_id = usd_invoice["id"]
    gb.add_invoice_entry(
        invoice_id=usd_invoice_id,
        account="Income:Sales",
        description="Consulting",
        quantity="1", price="500.00",
    )
    gb.post_invoice(
        invoice_id=usd_invoice_id,
        post_account="Assets:Accounts Receivable",
        post_date="2026-03-01",
    )
    gb.pay_invoice(
        invoice_id=usd_invoice_id,
        payment_account="Assets:Checking",
        amount="400", payment_date="2026-04-01",
    )
    # Drive the lot negative the only way still possible — a manual
    # payment transaction assigned to the lot from outside
    # ``pay_invoice`` (its overpayment guard can't see this one).
    with gb.open(readonly=False) as b:
        ar = next(
            a for a in b.accounts
            if a.fullname == "Assets:Accounts Receivable"
        )
        chk = next(
            a for a in b.accounts if a.fullname == "Assets:Checking"
        )
        lot_obj = ar.lots[0]
        ar_split = piecash.Split(account=ar, value=Decimal("-300"))
        piecash.Transaction(
            currency=b.default_currency,
            description="Manual overpayment",
            post_date=date(2026, 5, 1),
            splits=[
                ar_split,
                piecash.Split(account=chk, value=Decimal("300")),
            ],
        )
        ar_split.lot = lot_obj
        b.save()

    # Shape 5 (continued): EUR invoice posted to the EUR receivable.
    gb.create_customer(name="Berlin Digital", currency="EUR")
    eur_invoice = gb.create_invoice(
        customer_id="000002", currency="EUR",
        date_opened="2026-03-10",
    )
    eur_invoice_id = eur_invoice["id"]
    gb.add_invoice_entry(
        invoice_id=eur_invoice_id,
        account="Income:Sales",
        description="EUR consulting",
        quantity="1", price="1000.00",
    )
    gb.post_invoice(
        invoice_id=eur_invoice_id,
        post_account="Assets:Accounts Receivable EUR",
        post_date="2026-03-10",
    )

    # Shape 7 (C5): a future-dated transaction — entered ahead of
    # time, a workflow the server supports. Every "now" surface
    # (balance_sheet/net_worth/dashboard/get_balance/runway/low-cash/
    # debt payoff) must exclude it, so the cross-surface totals above
    # hold with this row present; the register surfaces still show it.
    gb.create_transaction(
        description="Scheduled rent (future)",
        splits=[
            {"account": "Expenses:Groceries", "amount": "1000.00"},
            {"account": "Assets:Checking", "amount": "-1000.00"},
        ],
        trans_date=date.today() + timedelta(days=10),
        check_duplicates=False,
    )

    return PathologicalBook(
        path=book_path,
        voided_txn_guid=voided_txn_guid,
        template_txn_guid=template_txn_guid,
        usd_invoice_id=usd_invoice_id,
        eur_invoice_id=eur_invoice_id,
    )


# Pristine inline-tool snapshot, captured at conftest import — before
# any test on this xdist worker has run _apply_module_filter. Inline
# tools (switch_book, get_server_config) register at server import
# ONLY; a single-book "all" filter run by an earlier test on the same
# worker pops switch_book permanently, breaking later multi-book
# tests by scheduling accident. Tests that need the pristine inline
# set restore from here.
import gnucash_mcp.server as _srv_for_snapshot
PRISTINE_INLINE_TOOLS = {
    name: tool
    for name, tool in _srv_for_snapshot.mcp._tool_manager._tools.items()
}
