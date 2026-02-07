"""Pytest fixtures for gnucash-mcp tests."""

import pytest
import piecash
from piecash import factories
from pathlib import Path
from datetime import date
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
