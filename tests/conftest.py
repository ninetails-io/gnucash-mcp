"""Pytest fixtures for gnucash-mcp tests."""

import pytest
import piecash
from pathlib import Path


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

    # Get the root account
    root = book.root_account

    # Create standard account hierarchy
    assets = piecash.Account(
        name="Assets",
        type="ASSET",
        parent=root,
        commodity=book.default_currency,
        placeholder=True,
    )
    checking = piecash.Account(
        name="Checking",
        type="BANK",
        parent=assets,
        commodity=book.default_currency,
    )

    liabilities = piecash.Account(
        name="Liabilities",
        type="LIABILITY",
        parent=root,
        commodity=book.default_currency,
        placeholder=True,
    )

    income = piecash.Account(
        name="Income",
        type="INCOME",
        parent=root,
        commodity=book.default_currency,
        placeholder=True,
    )
    salary = piecash.Account(
        name="Salary",
        type="INCOME",
        parent=income,
        commodity=book.default_currency,
    )

    expenses = piecash.Account(
        name="Expenses",
        type="EXPENSE",
        parent=root,
        commodity=book.default_currency,
        placeholder=True,
    )
    groceries = piecash.Account(
        name="Groceries",
        type="EXPENSE",
        parent=expenses,
        commodity=book.default_currency,
    )

    equity = piecash.Account(
        name="Equity",
        type="EQUITY",
        parent=root,
        commodity=book.default_currency,
        placeholder=True,
    )
    opening_balance = piecash.Account(
        name="Opening Balance",
        type="EQUITY",
        parent=equity,
        commodity=book.default_currency,
    )

    # Save accounts
    book.flush()

    # Create sample transactions
    from datetime import date
    from decimal import Decimal

    # Opening balance: $1000 in checking
    piecash.Transaction(
        currency=book.default_currency,
        description="Opening Balance",
        post_date=date(2024, 1, 1),
        splits=[
            piecash.Split(account=checking, value=Decimal("1000.00")),
            piecash.Split(account=opening_balance, value=Decimal("-1000.00")),
        ],
    )

    # Salary deposit: $2000
    piecash.Transaction(
        currency=book.default_currency,
        description="Salary Deposit",
        post_date=date(2024, 1, 15),
        splits=[
            piecash.Split(account=checking, value=Decimal("2000.00")),
            piecash.Split(account=salary, value=Decimal("-2000.00")),
        ],
    )

    # Grocery expense: $150
    piecash.Transaction(
        currency=book.default_currency,
        description="Weekly Groceries",
        post_date=date(2024, 1, 20),
        splits=[
            piecash.Split(account=groceries, value=Decimal("150.00")),
            piecash.Split(account=checking, value=Decimal("-150.00")),
        ],
    )

    book.flush()
    book.close()

    return book_path
