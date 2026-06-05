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

import os
from datetime import date, datetime
from decimal import Decimal

import piecash


# ── FX staleness cap (Plumb Bob validation, 2026-06-04) ───────────
#
# Pre-fix ``_find_exchange_rate`` would happily use the temporally-
# closest price regardless of distance from ``as_of`` — a 2027
# invoice could silently use a 2026 rate, a 2020 invoice could
# silently use a 2025 rate. The error message promised a price "on
# or near DATE" but the function had no proximity bound, so the
# error was effectively unreachable for any currency with at least
# one price on file.
#
# The cap below filters candidates to ``|days_offset| <=
# _FX_STALENESS_DAYS``. When no price within the window exists,
# the function returns ``None`` and the caller's existing
# "Add a price with create_price, then retry" error fires correctly
# (now with a real chance to fire).
#
# Default 90 days matches a typical bookkeeping cadence (monthly
# statement close + a grace period). The
# ``GNUCASH_FX_STALENESS_DAYS`` env var overrides it; ``0`` or
# negative disables the cap entirely (pre-fix behavior).


def _fx_staleness_days() -> int:
    """Read the FX staleness cap from the environment.

    Resolved per-call rather than cached because the env var can
    change between server starts (and tests need to monkey-patch
    it). The lookup is O(1) so the cost is negligible.
    """
    raw = os.environ.get("GNUCASH_FX_STALENESS_DAYS")
    if raw is None:
        return 90
    try:
        return int(raw)
    except ValueError:
        # Malformed value falls back to the default. We don't
        # want a startup typo to silently disable the cap (which
        # would happen if we returned 0).
        return 90


# ── FX freshness guard (stale-rate guard on post/pay) ─────────────
#
# Distinct from the staleness *cap* above. The cap (90 days) is the
# outer hard floor enforced inside ``_find_exchange_rate``: beyond
# it, no rate is returned at all and posting hard-errors. The guard
# below is the inner *freshness* check applied when a foreign-
# currency document is posted or paid: when the chosen rate is more
# than ``GNUCASH_FX_GUARD_DAYS`` (default 7) from the document's own
# date, the operation refuses unless ``force=True`` — because the
# rate gets etched in stone at post/pay time and ``create_price``
# does not retroactively update an already-posted document.
#
# Both measure the SAME axis: ``|price_date - as_of|`` (the
# document date), so they compose into three bands —
#   <= 7 days   : proceed
#   7..90 days  : refuse, forceable
#   > 90 days   : cap hard-errors, not forceable
#
# Resolved per-call (not cached) so tests can monkey-patch the
# threshold freely; the lookup is O(1).


def _fx_guard_days() -> int:
    """Read the FX freshness-guard threshold from the environment.

    ``GNUCASH_FX_GUARD_DAYS`` overrides the 7-day default. A
    malformed value falls back to 7 rather than silently disabling
    the guard. Set to ``0`` or negative to disable the guard
    entirely (the 90-day cap still applies).
    """
    raw = os.environ.get("GNUCASH_FX_GUARD_DAYS")
    if raw is None:
        return 7
    try:
        return int(raw)
    except ValueError:
        return 7


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

    @staticmethod
    def _anchor_for_as_of(as_of: date) -> date:
        """Translate a report's ``as_of`` into the date used for
        market-price filtering.

        The convention (locked by the cross-tool agreement test in
        ``TestCrossToolPriceAgreement``): future-dated TRANSACTIONS
        are excluded from "now" balances (events haven't happened
        yet) but future-dated PRICES are INCLUDED in "now"
        valuations — they're intentional forecasts the bookkeeper
        wrote, the most authoritative rate they have on file. So
        any anchor at or beyond today folds to ``date.max`` so
        every forecast is in scope; past anchors stay literal for
        historical reconstruction.

        Every report-level caller of ``_account_conversion_factors``
        and ``_rates_as_of`` runs its ``as_of`` through this helper
        first so the convention is enforced exactly once. Pre-v1.3
        release the convention was implicit in the ``as_of=None``
        default; now that the default is gone, the helper is the
        explicit home for it.
        """
        return date.max if as_of >= date.today() else as_of

    def _rates_as_of(
        self,
        book: piecash.Book,
        as_of: date,
        default_currency: piecash.Commodity | None = None,
    ) -> dict[str, Decimal]:
        """Latest user-supplied rate per non-default-currency commodity,
        as of a specific date.

        Returns ``{commodity_guid: Decimal rate}``. Each rate is the
        most recent market price (skipping ``type='transaction'``
        auto-placeholders) of the commodity quoted in the book's
        default currency, filtered to dates per
        :meth:`_anchor_for_as_of` — past anchors stay literal,
        anchors at or beyond today fold to ``date.max`` so future-
        dated forecast prices are included (convention).

        **Intermediate-currency chaining (issue #94).** A commodity
        with no price *directly* in the default currency, but
        reachable through an intermediate, is resolved via
        :meth:`_market_rate_to_default` (direct → inverse → single
        pivot, then a security-priced-in-foreign-currency outer hop).
        This covers a fund priced in USD inside an AED book
        (fund→USD→AED), a foreign-cash balance whose pair is only
        quoted through a vehicle currency (GBP→USD→AED), and the
        3-hop composition. Every leg reuses the market-price filter,
        so ``type='transaction'`` auto-placeholders never pollute a
        chained rate. Commodities with no resolvable path stay absent
        (caller falls back to cost basis).

        Args:
            book: Open piecash book.
            as_of: Upper bound on the price date. **Required** — pre-
                v1.3 release this defaulted to ``None`` (no upper
                bound, i.e. always-latest rates). Five historical-
                report sites passed nothing and silently used today's
                rates regardless of report date; the default has been
                dropped so every caller must declare its intent. Pass
                the report's as_of / end_date; the
                ``_anchor_for_as_of`` helper handles the "include
                future forecasts at now-or-future anchors" convention.
            default_currency: The book's default currency. Computed
                via :meth:`_require_default_currency` when ``None``.

        Returns:
            ``{commodity_guid: Decimal rate}``. Commodities without any
            qualifying price don't appear; callers fall back to
            ``split.value`` (cost basis) or skip the account.
        """
        anchor = self._anchor_for_as_of(as_of)
        if default_currency is None:
            default_currency = self._require_default_currency(book)
        latest: dict[str, tuple[date, Decimal]] = {}
        for p in book.prices:
            if p.currency != default_currency:
                continue
            if not _is_market_price(p):
                continue
            p_date = _to_date(p.date)
            if p_date > anchor:
                continue
            key = p.commodity.guid
            existing = latest.get(key)
            if existing is None or p_date > existing[0]:
                latest[key] = (p_date, Decimal(str(p.value)))
        result = {guid: rate for guid, (_d, rate) in latest.items()}

        # Issue #94: chain pass. For every commodity referenced by a
        # market price that the direct pass above couldn't rate, try
        # to reach the default currency through an intermediate. Only
        # commodities with at least one market price are candidates —
        # one with no price at all has no leg to chain and stays on
        # cost basis. Each resolution memoizes nothing here; the
        # per-commodity cost is a few indexed price walks, run only
        # for the non-direct minority.
        for commodity in self._commodities_with_market_prices(book):
            if commodity.guid in result or commodity == default_currency:
                continue
            chained = self._market_rate_to_default(
                book, commodity, default_currency, as_of,
            )
            if chained is not None:
                result[commodity.guid] = chained
        return result

    @staticmethod
    def _commodities_with_market_prices(
        book: piecash.Book,
    ) -> list[piecash.Commodity]:
        """Distinct commodities that appear on either side of a market
        price (``type='transaction'`` rows excluded).

        Both sides matter: a held currency may appear only as the
        *quote* side of a pair (``USD/GBP`` rather than ``GBP/USD``),
        and still needs chaining. Returned in a stable order (by
        namespace then mnemonic) so the chain pass is deterministic.
        """
        seen: dict[str, piecash.Commodity] = {}
        for p in book.prices:
            if not _is_market_price(p):
                continue
            for c in (p.commodity, p.currency):
                seen.setdefault(c.guid, c)
        return sorted(
            seen.values(),
            key=lambda c: (c.namespace or "", c.mnemonic or ""),
        )

    def _pivot_currencies(
        self,
        book: piecash.Book,
    ) -> list[piecash.Commodity]:
        """Currency commodities usable as a triangulation pivot —
        every ``CURRENCY``-namespace commodity that appears in a market
        price. Stable order (by mnemonic) for deterministic pivot
        selection."""
        currencies = {
            c.guid: c
            for c in self._commodities_with_market_prices(book)
            if c.namespace == "CURRENCY"
        }
        return sorted(currencies.values(), key=lambda c: c.mnemonic or "")

    def _cross_rate_with_path(
        self,
        book: piecash.Book,
        from_commodity: piecash.Commodity,
        to_commodity: piecash.Commodity,
        as_of: date,
    ) -> tuple[Decimal, list[str]] | None:
        """Rate from ``from_commodity`` to ``to_commodity`` with the
        intermediate path: direct, inverse, or single-pivot.

        ``1 unit of from_commodity == rate units of to_commodity``.

        Returns ``(rate, intermediates)`` where ``intermediates`` is
        the list of pivot-currency mnemonics strictly between source
        and target — ``[]`` for a direct/inverse hit, ``[P.mnemonic]``
        for a single-pivot triangulation. The path feeds the ``(via
        …)`` provenance annotation so a reader can tell a synthesized
        cross from a directly-quoted rate.

        Direct/inverse delegates to :meth:`_find_exchange_rate` (skips
        ``type='transaction'``, enforces the staleness cap). Otherwise
        each candidate pivot ``P`` with both ``from→P`` and ``P→to``
        resolving is scored by its **freshest worst leg** (ties broken
        by pivot mnemonic) so the choice is deterministic. Single pivot
        only — no graph search, so no cycles and bounded cost.

        ``from_commodity`` may be a security: ``from→P`` resolves to
        the security's quote-currency price (the first leg of case A).

        Valuation-only — invoice posting deliberately does **not** use
        this; a posted rate must be a real quote, not a synthesized
        cross.
        """
        if from_commodity == to_commodity:
            return (Decimal("1"), [])
        # Valuation chain: legs ignore the FX staleness cap so a
        # holding values at its latest available rate (matching the
        # cap-free direct path), regardless of age.
        direct = self._find_exchange_rate_aged(
            book,
            from_commodity=from_commodity,
            to_commodity=to_commodity,
            as_of=as_of,
            respect_staleness_cap=False,
        )
        if direct is not None:
            return (direct[0], [])
        best_key: tuple[int, str] | None = None
        best: tuple[Decimal, list[str]] | None = None
        for pivot in self._pivot_currencies(book):
            if pivot == from_commodity or pivot == to_commodity:
                continue
            leg1 = self._find_exchange_rate_aged(
                book, from_commodity=from_commodity,
                to_commodity=pivot, as_of=as_of,
                respect_staleness_cap=False,
            )
            if leg1 is None:
                continue
            leg2 = self._find_exchange_rate_aged(
                book, from_commodity=pivot,
                to_commodity=to_commodity, as_of=as_of,
                respect_staleness_cap=False,
            )
            if leg2 is None:
                continue
            key = (max(leg1[1], leg2[1]), pivot.mnemonic or "")
            if best_key is None or key < best_key:
                best_key = key
                best = (leg1[0] * leg2[0], [pivot.mnemonic or ""])
        return best

    def _cross_rate(
        self,
        book: piecash.Book,
        from_commodity: piecash.Commodity,
        to_commodity: piecash.Commodity,
        as_of: date,
    ) -> Decimal | None:
        """Rate-only wrapper over :meth:`_cross_rate_with_path`."""
        res = self._cross_rate_with_path(
            book, from_commodity, to_commodity, as_of,
        )
        return res[0] if res is not None else None

    def _market_rate_to_default_with_path(
        self,
        book: piecash.Book,
        commodity: piecash.Commodity,
        default_currency: piecash.Commodity,
        as_of: date,
    ) -> tuple[Decimal, list[str]] | None:
        """Market rate converting one unit of ``commodity`` to the
        book default currency, with the intermediate path, chaining
        when there is no direct price (issue #94).

        Resolution order:

        1. :meth:`_cross_rate_with_path` ``commodity → default`` —
           handles a direct/inverse default-currency price, a currency
           that triangulates through a pivot (case B), and a security
           whose quote currency *is* a pivot leg (case A).
        2. Security-outer fallback: for the newest market price of
           ``commodity`` in some quote currency ``X`` that itself
           reaches the default, return ``price(commodity in X) ×
           rate(X → default)`` with path ``[X] + rest`` — the 3-hop
           case C (fund priced in GBP, GBP only reachable via USD).

        Returns ``(rate, intermediates)`` or ``None`` when no path
        exists (caller keeps cost basis). ``intermediates`` is ``[]``
        only for a direct default-currency price.
        """
        if commodity == default_currency:
            return (Decimal("1"), [])
        res = self._cross_rate_with_path(
            book, commodity, default_currency, as_of,
        )
        if res is not None:
            return res
        for p in self._find_prices(
            book, commodity_guid=commodity.guid, market_only=True,
        ):
            quote = p.currency
            if quote == default_currency or quote == commodity:
                continue
            leg = self._cross_rate_with_path(
                book, quote, default_currency, as_of,
            )
            if leg is not None:
                return (
                    Decimal(str(p.value)) * leg[0],
                    [quote.mnemonic or ""] + leg[1],
                )
        return None

    def _market_rate_to_default(
        self,
        book: piecash.Book,
        commodity: piecash.Commodity,
        default_currency: piecash.Commodity,
        as_of: date,
    ) -> Decimal | None:
        """Rate-only wrapper over
        :meth:`_market_rate_to_default_with_path`."""
        res = self._market_rate_to_default_with_path(
            book, commodity, default_currency, as_of,
        )
        return res[0] if res is not None else None

    @staticmethod
    def _format_via(intermediates: list[str]) -> str | None:
        """Render a chain path as a ``(via …)`` provenance note.

        ``[]`` → ``None`` (direct rate, no annotation). ``["USD"]`` →
        ``"via USD"``. ``["GBP", "USD"]`` → ``"via GBP→USD"`` (the
        full hop sequence, so the reader knows exactly which legs to
        check if a synthesized rate looks off).
        """
        if not intermediates:
            return None
        return "via " + "→".join(intermediates)

    def _rate_provenance(
        self,
        book: piecash.Book,
        as_of: date,
        default_currency: piecash.Commodity,
    ) -> dict[str, str]:
        """``{commodity_guid: "via …"}`` for every commodity whose
        default-currency rate is *synthesized* through an intermediate.

        Directly-priced commodities are absent (no provenance to
        surface). Same family as ``fx_stale`` / ``discount_available``:
        a confidence signal so the reader distinguishes a rate they
        entered from one the system derived across two legs, each with
        its own staleness and rounding. Computed only for the chained
        minority, so it's cheap (and empty on single-currency books).
        """
        provenance: dict[str, str] = {}
        for commodity in self._commodities_with_market_prices(book):
            if commodity == default_currency:
                continue
            res = self._market_rate_to_default_with_path(
                book, commodity, default_currency, as_of,
            )
            if res is None:
                continue
            via = self._format_via(res[1])
            if via is not None:
                provenance[commodity.guid] = via
        return provenance

    def _account_conversion_factors(
        self,
        book: piecash.Book,
        as_of: date,
    ) -> dict[str, Decimal | None]:
        """Map ``{account_guid: factor}`` for default-currency conversion
        as of ``as_of``.

        ``factor * split.quantity = amount in default currency``.

        - ``Decimal("1")`` — the account is already in default currency.
        - A non-unit ``Decimal`` — the most recent market rate ≤
          ``as_of`` for the account's commodity in default currency.
        - ``None`` — no qualifying rate on file. Callers should fall
          back to ``split.value`` (transaction-currency amount), which
          equals cost basis for default-currency-denominated
          investment buys and degrades gracefully for foreign-currency
          holdings.

        ``as_of`` is required: pre-v1.3 release this took only
        ``book`` and silently fetched today's rates regardless of the
        caller's report date. Every caller now declares the date its
        valuation is anchored to — historical reports use historical
        rates, "now" helpers pass ``date.today()`` explicitly.

        Template accounts (under ``book.root_template``) are excluded
        from the map — they're scheduled-transaction scaffolding, not
        user-facing accounts.
        """
        default_currency = self._require_default_currency(book)
        rates = self._rates_as_of(
            book, as_of, default_currency=default_currency,
        )
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
        provenance: dict[str, str] | None = None,
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
            note = f"{quantity} {sym} @ {rate}"
            via = (provenance or {}).get(account.commodity.guid)
            if via:
                note += f" ({via})"
            return quantity * rate, note
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

        Thin wrapper over :meth:`_find_exchange_rate_aged` that drops
        the age/date metadata — the rate-only return that most callers
        (FX gain/loss, price-delete safety, valuation) want. See the
        aged variant for the selection rules and the staleness cap.

        Returns:
            Decimal rate, or ``None`` when no usable price exists in
            either direction WITHIN the staleness window.
        """
        aged = CurrencyMixin._find_exchange_rate_aged(
            book, from_commodity, to_commodity, as_of
        )
        return aged[0] if aged is not None else None

    @staticmethod
    def _find_exchange_rate_aged(
        book: piecash.Book,
        from_commodity: piecash.Commodity,
        to_commodity: piecash.Commodity,
        as_of: date,
        respect_staleness_cap: bool = True,
    ) -> tuple[Decimal, int, date] | None:
        """Cross-currency rate near ``as_of``, with the chosen
        price's age and date.

        ``1 unit of from_commodity == rate units of to_commodity``.

        Preference order:

        1. Direct price (``commodity=from, currency=to``) dated on or
           before ``as_of``, closest to ``as_of``.
        2. Inverse price (``commodity=to, currency=from``) dated on or
           before, closest. Returned as ``1 / p.value``.
        3. Direct after ``as_of``, closest.
        4. Inverse after ``as_of``, closest.

        **Staleness cap (Plumb Bob, 2026-06-04):** candidates more
        than ``GNUCASH_FX_STALENESS_DAYS`` (default 90) from
        ``as_of`` are excluded. Pre-fix the function would happily
        return a 5-year-old rate on a 2027 invoice; the documented
        "on or near DATE" promise was effectively unreachable.
        Setting the env var to ``0`` or a negative value disables
        the cap (restores pre-fix behavior). ``respect_staleness_cap=
        False`` disables it per-call — used by the valuation chain
        (issue #94), which must value a holding at its latest
        available rate regardless of age (matching the cap-free
        direct path in ``_rates_as_of``); the separate stale-price
        warning, not a hard cap, is what flags age for reporting. The
        cap stays on for invoice posting, where a stale rate is etched
        and must be refused.

        Skips piecash's auto-created ``type='transaction'`` rows —
        those are 1.0 placeholders generated on cross-currency invoice
        post and would mask the absence of a real market rate.

        Skips zero-or-negative prices on either branch (the inverse
        branch needs the guard to avoid div-by-zero; the direct branch
        gets the same guard for consistent failure signaling).

        Returns:
            ``(rate, age_days, price_date)`` where ``age_days`` is the
            absolute day distance ``|price_date - as_of|`` of the
            chosen price and ``price_date`` is that price's date — the
            inputs the freshness guard needs to judge staleness. Same-
            commodity returns ``(Decimal("1"), 0, as_of)``. ``None``
            when no usable price exists within the staleness window.
        """
        if from_commodity == to_commodity:
            return (Decimal("1"), 0, as_of)

        cap = _fx_staleness_days()
        # cap <= 0 (or respect_staleness_cap=False) disables the
        # staleness window.
        cap_enabled = respect_staleness_cap and cap > 0

        # Each candidate: (abs_age_days, rate, price_date).
        best_before_direct: tuple[int, Decimal, date] | None = None
        best_after_direct: tuple[int, Decimal, date] | None = None
        best_before_inverse: tuple[int, Decimal, date] | None = None
        best_after_inverse: tuple[int, Decimal, date] | None = None

        for p in book.prices:
            if not _is_market_price(p):
                continue
            p_date = _to_date(p.date)
            if p.commodity == from_commodity and p.currency == to_commodity:
                days = (as_of - p_date).days
                if cap_enabled and abs(days) > cap:
                    continue
                rate = Decimal(str(p.value))
                if rate <= 0:
                    continue
                if days >= 0:
                    if best_before_direct is None or days < best_before_direct[0]:
                        best_before_direct = (days, rate, p_date)
                else:
                    if best_after_direct is None or -days < best_after_direct[0]:
                        best_after_direct = (-days, rate, p_date)
            elif p.commodity == to_commodity and p.currency == from_commodity:
                days = (as_of - p_date).days
                if cap_enabled and abs(days) > cap:
                    continue
                if Decimal(str(p.value)) <= 0:
                    continue
                rate = Decimal("1") / Decimal(str(p.value))
                if days >= 0:
                    if best_before_inverse is None or days < best_before_inverse[0]:
                        best_before_inverse = (days, rate, p_date)
                else:
                    if best_after_inverse is None or -days < best_after_inverse[0]:
                        best_after_inverse = (-days, rate, p_date)

        for candidate in (
            best_before_direct,
            best_before_inverse,
            best_after_direct,
            best_after_inverse,
        ):
            if candidate is not None:
                age_days, rate, p_date = candidate
                return (rate, age_days, p_date)
        return None
