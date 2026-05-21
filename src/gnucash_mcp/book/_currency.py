"""Cross-commodity currency-conversion mixin.

Composed into :class:`BaseGnuCashBook` unconditionally so every
module — ``reporting``, ``budgets``, ``business``, ``core`` — gets
the same conversion path regardless of which ``--modules`` the user
enabled. Currency conversion is cross-cutting infrastructure, not a
feature flag.

Single source of truth for:

- "What's the latest market price for commodity X in the book's
  default currency?" — :meth:`CurrencyMixin._rates_as_of`.
- "What factor converts each account's quantity to the default
  currency?" — :meth:`CurrencyMixin._account_conversion_factors`.
- "What's one split's value in the default currency?" —
  :meth:`CurrencyMixin._split_in_default_currency`.
- "What's a whole account's quantity worth, with a display annotation
  and a cost-basis fallback when no rate is on file?" —
  :meth:`CurrencyMixin._market_value`.
- "What's the exchange rate between two non-default currencies near
  some date?" — :meth:`CurrencyMixin._find_exchange_rate`.

All helpers skip piecash's auto-created ``type='transaction'`` price
placeholders via :func:`gnucash_mcp.book._base._is_market_price` —
those rows capture the effective rate of one specific cross-currency
transaction and would shadow real user-supplied market quotes.

The indexed query primitive :meth:`CurrencyMixin._find_prices` is
provided here for future call-site migration; the rate-collecting
helpers above still walk ``book.prices`` directly today (the v1.3
performance sweep replaces those walks).
"""

from datetime import date, datetime
from decimal import Decimal

import piecash


def _to_date(dt: date | datetime) -> date:
    """Convert a datetime or date to a date object.

    piecash may return either datetime or date for price dates depending
    on how the price was created. This normalizes to date.
    """
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def _is_market_price(price) -> bool:
    """True iff ``price`` is a real market quote, not a piecash auto-
    placeholder.

    On any cross-currency transaction, piecash auto-creates a Price
    row with ``type='transaction'`` capturing the effective rate of
    that one transaction. These are bookkeeping artifacts, NOT
    user-supplied market quotes — every helper that walks
    ``book.prices`` to value holdings or pick exchange rates must
    skip them, or they shadow real quotes the user has on file.

    Centralized here so every call site
    (core's ``_collect_warnings``, this module's ``_rates_as_of`` and
    ``_find_exchange_rate``, investments' price-delete safety check)
    all answer the question the same way. Adding
    ``"transaction-currency"`` or any future placeholder type only
    needs one change.
    """
    return getattr(price, "type", None) != "transaction"


class CurrencyMixin:
    """Cross-commodity valuation helpers.

    Composed into :class:`BaseGnuCashBook` so the methods are
    available on every constructed ``GnuCashBook`` class — including
    the ones built by ``build_book_class({"core", "budgets"})`` where
    the reporting mixin is absent.
    """

    @staticmethod
    def _find_prices(
        book: piecash.Book,
        *,
        commodity_guid: str | None = None,
        currency_guid: str | None = None,
        market_only: bool = True,
    ) -> list:
        """Indexed lookup over ``book.prices``.

        Replaces the ``for p in book.prices: if p.commodity == ...``
        linear walks scattered across the codebase. The query hits
        the SQLAlchemy session directly, ordered most-recent-first.

        Args:
            book: Open piecash book.
            commodity_guid: When set, restrict to prices of this
                commodity (the held instrument).
            currency_guid: When set, restrict to prices denominated
                in this currency (the quote side).
            market_only: When True (default), skip piecash's
                ``type='transaction'`` auto-placeholders via
                :func:`_is_market_price`.

        Returns:
            List of :class:`piecash.core.commodity.Price` rows,
            newest first.
        """
        from piecash.core.commodity import Price

        q = book.session.query(Price)
        if commodity_guid is not None:
            q = q.filter(Price.commodity_guid == commodity_guid)
        if currency_guid is not None:
            q = q.filter(Price.currency_guid == currency_guid)
        q = q.order_by(Price.date.desc())
        prices = list(q)
        if market_only:
            prices = [p for p in prices if _is_market_price(p)]
        return prices

    def _rates_as_of(
        self,
        book: piecash.Book,
        as_of: date | None = None,
        default_currency: piecash.Commodity | None = None,
    ) -> dict[str, Decimal]:
        """Latest user-supplied rate per non-default-currency commodity.

        Returns ``{commodity_guid: Decimal rate}``. Each rate is the
        most recent market price (skipping ``type='transaction'``
        auto-placeholders) of the commodity quoted in the book's
        default currency.

        Args:
            book: Open piecash book.
            as_of: When set, restrict to prices on or before this
                date. When ``None`` (default), no upper bound — returns
                the absolute latest rate per commodity, including
                future-dated forecasts the bookkeeper has deliberately
                written.
            default_currency: The book's default currency. Computed
                via :meth:`_require_default_currency` when ``None``.

        Returns:
            ``{commodity_guid: Decimal rate}``. Commodities without any
            qualifying price don't appear; callers fall back to
            ``split.value`` (cost basis) or skip the account.
        """
        if default_currency is None:
            default_currency = self._require_default_currency(book)
        latest: dict[str, tuple[date, Decimal]] = {}
        for p in book.prices:
            if p.currency != default_currency:
                continue
            if not _is_market_price(p):
                continue
            p_date = _to_date(p.date)
            if as_of is not None and p_date > as_of:
                continue
            key = p.commodity.guid
            existing = latest.get(key)
            if existing is None or p_date > existing[0]:
                latest[key] = (p_date, Decimal(str(p.value)))
        return {guid: rate for guid, (_d, rate) in latest.items()}

    def _account_conversion_factors(
        self,
        book: piecash.Book,
    ) -> dict[str, Decimal | None]:
        """Map ``{account_guid: factor}`` for default-currency conversion.

        ``factor * split.quantity = amount in default currency``.

        - ``Decimal("1")`` — the account is already in default currency.
        - A non-unit ``Decimal`` — the most recent market rate for the
          account's commodity in default currency.
        - ``None`` — no rate on file. Callers should fall back to
          ``split.value`` (transaction-currency amount), which equals
          cost basis for default-currency-denominated investment buys
          and degrades gracefully for foreign-currency holdings.

        Template accounts (under ``book.root_template``) are excluded
        from the map — they're scheduled-transaction scaffolding, not
        user-facing accounts.
        """
        default_currency = self._require_default_currency(book)
        rates = self._rates_as_of(book, default_currency=default_currency)
        template_guids = self._template_account_guids(book)
        factors: dict[str, Decimal | None] = {}
        for acct in book.accounts:
            if acct.guid in template_guids:
                continue
            if acct.commodity == default_currency:
                factors[acct.guid] = Decimal("1")
            else:
                factors[acct.guid] = rates.get(acct.commodity.guid)
        return factors

    @staticmethod
    def _split_in_default_currency(
        split,
        account,
        factor: Decimal | None,
    ) -> Decimal:
        """Value a single split in the book's default currency.

        Uses ``factor * quantity`` when a factor is available. Falls
        back to ``split.value`` otherwise — correct for STOCK/MUTUAL
        splits whose transaction currency is the book default, and a
        reasonable cost-basis approximation for other cases.
        """
        if factor is not None:
            return Decimal(str(split.quantity)) * factor
        return Decimal(str(split.value))

    def _market_value(
        self,
        account,
        quantity: Decimal,
        *,
        rates: dict[str, Decimal],
        default_currency: piecash.Commodity,
        today: date | None = None,
        with_cost_fallback: bool = True,
    ) -> tuple[Decimal, str | None]:
        """Value an account-level quantity with a display annotation.

        Used by ``get_book_summary``'s per-account display where the
        annotation tells the LLM whether the number came from a market
        rate or a cost-basis fallback. Sibling to
        :meth:`_split_in_default_currency` — same conversion model,
        different aggregation level.

        Args:
            account: piecash Account whose commodity is being valued.
            quantity: Quantity in ``account.commodity``, already summed
                by the caller.
            rates: ``{commodity_guid: Decimal}`` map from
                :meth:`_rates_as_of`. Passed in (not re-fetched) so
                callers control the price-map build once per report.
            default_currency: The book's default currency commodity.
            today: Cost-basis fallback ignores splits dated after this
                so trajectory snapshots don't pick up future-dated
                buys. When ``None``, no date filter is applied.
            with_cost_fallback: When True (default), missing rate falls
                back to summing ``split.value`` across the account
                (cost basis). When False, missing rate returns
                ``Decimal("0")`` with a ``"no price data"`` note —
                useful for callers that want a clear empty signal
                instead of a silent cost-basis substitute.

        Returns:
            ``(value_in_default_currency, display_note)``.
            ``display_note`` is ``None`` for default-currency accounts.
        """
        if account.commodity == default_currency:
            return quantity, None
        sym = account.commodity.mnemonic
        rate = rates.get(account.commodity.guid)
        if rate is not None:
            return quantity * rate, f"{quantity} {sym} @ {rate}"
        if not with_cost_fallback:
            return Decimal("0"), f"{quantity} {sym} — no price data"
        cost_basis = Decimal("0")
        for s in account.splits:
            if today is None or s.transaction.post_date <= today:
                cost_basis += Decimal(str(s.value))
        return cost_basis, f"{quantity} {sym} — no price data"

    @staticmethod
    def _find_exchange_rate(
        book: piecash.Book,
        from_commodity: piecash.Commodity,
        to_commodity: piecash.Commodity,
        as_of: date,
    ) -> Decimal | None:
        """Cross-currency rate near ``as_of``.

        ``1 unit of from_commodity == rate units of to_commodity``.

        Preference order:

        1. Direct price (``commodity=from, currency=to``) dated on or
           before ``as_of``, closest to ``as_of``.
        2. Inverse price (``commodity=to, currency=from``) dated on or
           before, closest. Returned as ``1 / p.value``.
        3. Direct after ``as_of``, closest.
        4. Inverse after ``as_of``, closest.

        Skips piecash's auto-created ``type='transaction'`` rows —
        those are 1.0 placeholders generated on cross-currency invoice
        post and would mask the absence of a real market rate.

        Skips zero-or-negative prices on either branch (the inverse
        branch needs the guard to avoid div-by-zero; the direct branch
        gets the same guard for consistent failure signaling).

        Returns:
            Decimal rate, or ``None`` when no usable price exists in
            either direction.
        """
        if from_commodity == to_commodity:
            return Decimal("1")

        best_before_direct: tuple[int, Decimal] | None = None
        best_after_direct: tuple[int, Decimal] | None = None
        best_before_inverse: tuple[int, Decimal] | None = None
        best_after_inverse: tuple[int, Decimal] | None = None

        for p in book.prices:
            if not _is_market_price(p):
                continue
            p_date = _to_date(p.date)
            if p.commodity == from_commodity and p.currency == to_commodity:
                days = (as_of - p_date).days
                rate = Decimal(str(p.value))
                if rate <= 0:
                    continue
                if days >= 0:
                    if best_before_direct is None or days < best_before_direct[0]:
                        best_before_direct = (days, rate)
                else:
                    if best_after_direct is None or -days < best_after_direct[0]:
                        best_after_direct = (-days, rate)
            elif p.commodity == to_commodity and p.currency == from_commodity:
                days = (as_of - p_date).days
                if Decimal(str(p.value)) <= 0:
                    continue
                rate = Decimal("1") / Decimal(str(p.value))
                if days >= 0:
                    if best_before_inverse is None or days < best_before_inverse[0]:
                        best_before_inverse = (days, rate)
                else:
                    if best_after_inverse is None or -days < best_after_inverse[0]:
                        best_after_inverse = (-days, rate)

        for candidate in (
            best_before_direct,
            best_before_inverse,
            best_after_direct,
            best_after_inverse,
        ):
            if candidate is not None:
                return candidate[1]
        return None
