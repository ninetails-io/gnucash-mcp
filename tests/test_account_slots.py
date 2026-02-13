"""Tests for account slot (KVP metadata) operations."""

from pathlib import Path

import pytest

from gnucash_mcp.book import GnuCashBook


class TestGetAccountSlots:
    """Tests for get_account_slots method."""

    def test_get_all_slots_empty(self, test_book: Path):
        """Should return empty slots for an account with no custom slots."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_account_slots("Assets:Checking")

        assert result["account"] == "Assets:Checking"
        assert result["slots"] == {}

    def test_get_all_slots_after_setting(self, test_book: Path):
        """Should return all slots after setting them."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        gc_book.set_account_slot("Assets:Checking", "credit_limit", "15000")

        result = gc_book.get_account_slots("Assets:Checking")
        assert result["account"] == "Assets:Checking"
        assert result["slots"]["apr"] == "24.99"
        assert result["slots"]["credit_limit"] == "15000"

    def test_get_specific_slot(self, test_book: Path):
        """Should return only the requested slot when key is specified."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        gc_book.set_account_slot("Assets:Checking", "credit_limit", "15000")

        result = gc_book.get_account_slots("Assets:Checking", key="apr")
        assert result["slots"] == {"apr": "24.99"}

    def test_get_specific_slot_not_found(self, test_book: Path):
        """Should return empty slots when requested key doesn't exist."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.get_account_slots("Assets:Checking", key="nonexistent")

        assert result["account"] == "Assets:Checking"
        assert result["slots"] == {}

    def test_get_slots_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Account not found"):
            gc_book.get_account_slots("Nonexistent:Account")


class TestSetAccountSlot:
    """Tests for set_account_slot method."""

    def test_set_new_slot(self, test_book: Path):
        """Should create a new slot and return status 'created'."""
        gc_book = GnuCashBook(str(test_book))
        result = gc_book.set_account_slot("Assets:Checking", "apr", "24.99")

        assert result["account"] == "Assets:Checking"
        assert result["key"] == "apr"
        assert result["value"] == "24.99"
        assert result["status"] == "created"

    def test_set_existing_slot(self, test_book: Path):
        """Should update an existing slot and return status 'updated'."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        result = gc_book.set_account_slot("Assets:Checking", "apr", "19.99")

        assert result["status"] == "updated"
        assert result["value"] == "19.99"

    def test_set_slot_persists(self, test_book: Path):
        """Should persist the slot value across separate reads."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "reward_rate", "1.5% cash back")

        # Read back in a new session
        result = gc_book.get_account_slots("Assets:Checking", key="reward_rate")
        assert result["slots"]["reward_rate"] == "1.5% cash back"

    def test_set_slot_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Account not found"):
            gc_book.set_account_slot("Nonexistent:Account", "key", "value")

    def test_set_multiple_slots_different_accounts(self, test_book: Path):
        """Should handle slots on different accounts independently."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "note", "primary account")
        gc_book.set_account_slot("Expenses:Groceries", "budget", "500")

        result1 = gc_book.get_account_slots("Assets:Checking")
        result2 = gc_book.get_account_slots("Expenses:Groceries")

        assert result1["slots"]["note"] == "primary account"
        assert result2["slots"]["budget"] == "500"
        assert "budget" not in result1["slots"]
        assert "note" not in result2["slots"]


class TestDeleteAccountSlot:
    """Tests for delete_account_slot method."""

    def test_delete_existing_slot(self, test_book: Path):
        """Should delete a slot and return status 'deleted'."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        result = gc_book.delete_account_slot("Assets:Checking", "apr")

        assert result["account"] == "Assets:Checking"
        assert result["key"] == "apr"
        assert result["status"] == "deleted"

    def test_delete_slot_is_gone(self, test_book: Path):
        """Should no longer return the slot after deletion."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        gc_book.delete_account_slot("Assets:Checking", "apr")

        result = gc_book.get_account_slots("Assets:Checking", key="apr")
        assert result["slots"] == {}

    def test_delete_nonexistent_slot(self, test_book: Path):
        """Should raise ValueError when key doesn't exist."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Slot key not found"):
            gc_book.delete_account_slot("Assets:Checking", "nonexistent")

    def test_delete_slot_account_not_found(self, test_book: Path):
        """Should raise ValueError for non-existent account."""
        gc_book = GnuCashBook(str(test_book))
        with pytest.raises(ValueError, match="Account not found"):
            gc_book.delete_account_slot("Nonexistent:Account", "key")

    def test_delete_one_slot_leaves_others(self, test_book: Path):
        """Should only delete the specified slot, leaving others intact."""
        gc_book = GnuCashBook(str(test_book))

        gc_book.set_account_slot("Assets:Checking", "apr", "24.99")
        gc_book.set_account_slot("Assets:Checking", "credit_limit", "15000")

        gc_book.delete_account_slot("Assets:Checking", "apr")

        result = gc_book.get_account_slots("Assets:Checking")
        assert "apr" not in result["slots"]
        assert result["slots"]["credit_limit"] == "15000"
