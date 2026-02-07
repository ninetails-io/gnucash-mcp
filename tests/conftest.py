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
