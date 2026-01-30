"""Pytest fixtures for gnucash-mcp tests."""

import pytest
import piecash
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
