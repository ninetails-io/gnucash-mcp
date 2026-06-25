"""Cross-commodity currency-conversion mixin.

Composed into :class:`BaseGnuCashBook` unconditionally — currency
conversion is cross-cutting infrastructure, not a feature flag.

Single source of truth for rates as-of a date (``_rates_as_of``),
per-account conversion factors (``_account_conversion_factors``),
split valuation (``_split_in_default_currency``), account valuation
with cost-basis fallback (``_market_value``), and pairwise exchange
rates (``_find_exchange_rate``).

All helpers skip piecash's auto-created ``type='transaction'`` price
placeholders via :func:`_is_market_price` (re-exported through
``book._base``) — those would shadow real user-supplied quotes.
"""

import os
from datetime import date, datetime
from decimal import Decimal

import piecash


# ── FX staleness cap ───────────────────────────────────────────────
#
# Without the cap, ``_find_exchange_rate`` would use the temporally
# closest price at any distance — a 2027 invoice silently rated from
# 2026 — making the "on or near DATE" promise in the no-rate error
# unreachable. Candidates beyond ``|days_offset| <= cap`` are
# excluded; with none inside the window the function returns None
# and the caller's create_price-then-retry error fires. Default 90
# days (monthly close + grace); ``GNUCASH_FX_STALENESS_DAYS``
# overrides, 0 or negative disables.


def _fx_staleness_days() -> int:
    """Read the FX staleness cap from the environment.

    Resolved per-call (tests monkey-patch it; lookup is O(1)).
    """
    raw = os.environ.get("GNUCASH_FX_STALENESS_DAYS")
    if raw is None:
        return 90
    try:
        return int(raw)
    except ValueError:
        # Malformed → default, not 0 — a startup typo must not
        # silently disable the cap.
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


# ── FX entry-time implied-rate sanity ratio ───────────────────────
#
# A user-supplied cross-commodity split encodes its own rate via
# ``|value| / |quantity|``. When that implied rate differs from the
# latest price on file by this factor or more, the ledger-entry paths
# emit a NON-BLOCKING warning — catching decimal slips and inverted
# pairs at the source. Unlike the post/pay guard this never blocks:
# the user authored the rate, so an off-market value may be a genuine
# deal or a correction — flag it, let them decide. Default 2.0 (off by
# 2x+, comfortably catching 10x slips and inversions without nagging
# on normal rate variation). A ratio must exceed 1 to be meaningful;
# raise it high via ``GNUCASH_FX_SANITY_RATIO`` to quiet the check.


def _fx_sanity_ratio() -> Decimal:
    """Read the implied-rate sanity ratio from the environment.

    Resolved per-call (tests monkey-patch it). Malformed or <= 1
    falls back to the 2.0 default.
    """
    raw = os.environ.get("GNUCASH_FX_SANITY_RATIO")
    if raw is not None:
        try:
            val = Decimal(raw)
            if val > 1:
                return val
        except (ValueError, ArithmeticError):
            pass
    return Decimal("2")


def _to_date(dt: date | datetime) -> date:
    """Convert a datetime or date to a date object.

    piecash may return either datetime or date for price dates depending
    on how the price was created. This normalizes to date.
    """
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def _is_market_price(price) -> bool:
    """True iff ``price`` is a real market quote, not a piecash
    auto-placeholder.

    piecash auto-creates a ``type='transaction'`` Price row on every
    cross-currency transaction — a bookkeeping artifact, not a user
    quote. Every helper that walks ``book.prices`` must skip these
    or they shadow real quotes. Centralized so all call sites answer
    the same way; a future placeholder type needs one change.
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
        """Indexed lookup over ``book.prices``, newest first.

        Replaces the linear ``for p in book.prices`` walks.
        ``commodity_guid`` filters the held instrument,
        ``currency_guid`` the quote side; ``market_only`` (default)
        skips ``type='transaction'`` auto-placeholders.
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
        first so the convention is enforced exactly once; this
        helper is the convention's explicit home.
        """
        return date.max if as_of >= date.today() else as_of

    def _rates_as_of(
        self,
        book: piecash.Book,
        as_of: date,
        default_currency: piecash.Commodity | None = None,
    ) -> dict[str, Decimal]:
        """Latest user-supplied rate per non-default-currency
        commodity, as of a specific date.

        Returns ``{commodity_guid: Decimal rate}`` — the most recent
        market price of each commodity quoted in the default
        currency, date-filtered per :meth:`_anchor_for_as_of`.
        Commodities with no qualifying price are absent; callers
        fall back to cost basis.

        Commodities with no *direct* default-currency price chain
        through intermediates via
        :meth:`_market_rate_to_default_with_path` — see its docstring
        for the resolution cases. Every leg reuses the market-price
        filter, so auto-placeholders never pollute a chained rate.

        ``as_of`` is **required** deliberately: a default
        (always-latest) would let historical-report callers silently
        use today's rates. Pass the report's as_of / end_date;
        ``_anchor_for_as_of`` handles the forecast-price convention.
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

        # Chain pass for commodities the direct pass couldn't rate
        # (only priced commodities are candidates — no price, no leg).
        # Past anchors forbid after-anchor fallbacks in the legs: the
        # direct pass hard-filters future prices for historical
        # reports, and a chained commodity must honor the same
        # convention. Now/future anchors fold to date.max, where the
        # flag is moot.
        allow_after = anchor >= date.today()
        for commodity in self._commodities_with_market_prices(book):
            if commodity.guid in result or commodity == default_currency:
                continue
            # Future-folded ``anchor`` (not raw as_of) keeps the
            # chain on the same forecast convention as the direct
            # pass; the legs run cap-free, so date.max selects the
            # latest rate rather than excluding everything as stale.
            chained = self._market_rate_to_default(
                book, commodity, default_currency, anchor,
                allow_after=allow_after,
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
        allow_after: bool = True,
    ) -> tuple[Decimal, list[str]] | None:
        """Rate from ``from_commodity`` to ``to_commodity`` with the
        intermediate path: direct, inverse, or single-pivot.

        ``1 unit of from_commodity == rate units of to_commodity``.
        Returns ``(rate, intermediates)`` — ``[]`` for direct/inverse,
        ``[P.mnemonic]`` for a pivot — feeding the ``(via …)``
        provenance note.

        Candidate pivots are scored by **freshest worst leg** (ties
        by mnemonic) so the choice is deterministic. Single pivot
        only — no graph search, no cycles, bounded cost.
        ``from_commodity`` may be a security (``from→P`` resolves its
        quote-currency price).

        Valuation-only — invoice posting deliberately does NOT use
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
            allow_after=allow_after,
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
                allow_after=allow_after,
            )
            if leg1 is None:
                continue
            leg2 = self._find_exchange_rate_aged(
                book, from_commodity=pivot,
                to_commodity=to_commodity, as_of=as_of,
                respect_staleness_cap=False,
                allow_after=allow_after,
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
        allow_after: bool = True,
    ) -> Decimal | None:
        """Rate-only wrapper over :meth:`_cross_rate_with_path`."""
        res = self._cross_rate_with_path(
            book, from_commodity, to_commodity, as_of,
            allow_after=allow_after,
        )
        return res[0] if res is not None else None

    def _market_rate_to_default_with_path(
        self,
        book: piecash.Book,
        commodity: piecash.Commodity,
        default_currency: piecash.Commodity,
        as_of: date,
        allow_after: bool = True,
    ) -> tuple[Decimal, list[str]] | None:
        """Market rate converting one unit of ``commodity`` to the
        book default, with the intermediate path, chaining when
        there is no direct price.

        Resolution: (1) :meth:`_cross_rate_with_path` ``commodity →
        default`` (direct/inverse, pivot triangulation, security
        whose quote currency is a pivot leg); (2) security-outer
        fallback — newest price of ``commodity`` in quote currency
        ``X`` × rate(X → default), the 3-hop case (fund priced in
        GBP, GBP only reachable via USD).

        Returns ``(rate, intermediates)`` or ``None`` (caller keeps
        cost basis); ``[]`` only for a direct default-currency price.
        """
        if commodity == default_currency:
            return (Decimal("1"), [])
        res = self._cross_rate_with_path(
            book, commodity, default_currency, as_of,
            allow_after=allow_after,
        )
        if res is not None:
            return res
        for p in self._find_prices(
            book, commodity_guid=commodity.guid, market_only=True,
        ):
            # Newest-first list with no date bound; the outer hop
            # honors the same anchor convention as the legs —
            # past anchors never price off a future quote.
            if not allow_after and _to_date(p.date) > as_of:
                continue
            quote = p.currency
            if quote == default_currency or quote == commodity:
                continue
            leg = self._cross_rate_with_path(
                book, quote, default_currency, as_of,
                allow_after=allow_after,
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
        allow_after: bool = True,
    ) -> Decimal | None:
        """Rate-only wrapper over
        :meth:`_market_rate_to_default_with_path`."""
        res = self._market_rate_to_default_with_path(
            book, commodity, default_currency, as_of,
            allow_after=allow_after,
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
        default-currency rate is *synthesized* through an
        intermediate; directly-priced commodities are absent.

        A confidence signal — the reader distinguishes a rate they
        entered from one derived across legs, each with its own
        staleness and rounding.
        """
        # Fold the anchor and apply allow_after exactly as
        # ``_rates_as_of`` does — otherwise this pass can resolve a
        # DIFFERENT path than the rate's and the "(via …)" note lies.
        anchor = self._anchor_for_as_of(as_of)
        allow_after = anchor >= date.today()
        provenance: dict[str, str] = {}
        for commodity in self._commodities_with_market_prices(book):
            if commodity == default_currency:
                continue
            res = self._market_rate_to_default_with_path(
                book, commodity, default_currency, anchor,
                allow_after=allow_after,
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
        """Map ``{account_guid: factor}`` for default-currency
        conversion as of ``as_of``.

        ``factor * split.quantity = amount in default currency``:
        ``Decimal("1")`` for default-currency accounts; the
        most-recent rate ≤ ``as_of`` otherwise; ``None`` when no
        rate is on file (callers fall back to ``split.value`` —
        cost basis for default-currency buys, graceful degradation
        for foreign holdings).

        ``as_of`` is required so every caller declares its valuation
        date — historical reports must not silently use today's
        rates. Template accounts are excluded.
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

    def _fx_sanity_warnings(
        self, book, validated_splits, trans_currency, as_of,
    ) -> list[dict]:
        """Non-blocking warnings for cross-commodity splits whose
        implied rate (``|value| / |quantity|``) is grossly off the
        latest price on file — a decimal slip or inverted pair caught
        at entry, before it corrupts everything downstream.

        Returns ``{"type": "fx_rate_sanity", "message": ...}`` dicts.
        Never raises and never blocks: the user authored the rate, so
        an unusual one may be a real off-market deal or a correction —
        flag, don't refuse. Silent when no reference price exists
        (nothing to compare against) so price-free books aren't nagged.

        ``validated_splits`` are the resolved dicts from
        :meth:`_validate_transaction_splits` (``account``, ``value``,
        ``quantity``). ``as_of`` is the transaction date.
        """
        as_of = as_of or date.today()
        ratio_cap = _fx_sanity_ratio()
        out: list[dict] = []
        for v in validated_splits:
            account = v["account"]
            if account.commodity == trans_currency:
                continue
            value = abs(Decimal(str(v["value"])))
            quantity = abs(Decimal(str(v["quantity"])))
            if value == 0 or quantity == 0:
                continue
            # implied rate is trans_currency per unit of the account's
            # commodity — same orientation as the reference below.
            implied = value / quantity
            # ANY prior quote is a valid sanity anchor — a 6-month-old
            # rate still catches a 10x slip — so bypass the staleness
            # cap that the post/pay path enforces (there a stale rate
            # would be etched into a booked amount; here it only
            # answers "is this grossly off?"). The aged variant also
            # hands back the quote's date for the message.
            aged = self._find_exchange_rate_aged(
                book, account.commodity, trans_currency, as_of,
                respect_staleness_cap=False,
            )
            if aged is None:
                continue
            reference, _age_days, price_date = aged
            if reference <= 0:
                continue
            hi = max(implied, reference)
            lo = min(implied, reference)
            if lo > 0 and hi / lo >= ratio_cap:
                out.append({
                    "type": "fx_rate_sanity",
                    "message": (
                        f"Split for '{account.fullname}': implied rate "
                        f"{implied:.4f} {trans_currency.mnemonic}/"
                        f"{account.commodity.mnemonic} is {hi / lo:.1f}x the "
                        f"stored rate {reference:.4f} ({price_date}) — "
                        f"verify the amount and quantity (possible misplaced "
                        f"decimal or inverted rate)."
                    ),
                })
        return out

    def _market_value(
        self,
        account,
        quantity: Decimal,
        *,
        book: piecash.Book,
        rates: dict[str, Decimal],
        default_currency: piecash.Commodity,
        today: date | None = None,
        with_cost_fallback: bool = True,
        provenance: dict[str, str] | None = None,
    ) -> tuple[Decimal, str | None]:
        """Value an account-level quantity with a display annotation
        (market rate vs cost-basis fallback). Sibling to
        :meth:`_split_in_default_currency` — same conversion model,
        account-level aggregation.

        Args:
            book: Needed to value the cost-basis fallback in the book
                default currency (see below).
            rates: Map from :meth:`_rates_as_of`, passed in so the
                price-map builds once per report.
            today: Cost-basis fallback ignores splits dated after
                this; ``None`` applies no date filter.
            with_cost_fallback: When False, a missing rate returns
                ``Decimal("0")`` with a "no price data" note instead
                of a silent cost-basis substitute.

        Returns:
            ``(value_in_default_currency, display_note)``;
            ``display_note`` is None for default-currency accounts.
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
        # No market price for the holding: fall back to cost basis in
        # the book default. ``split.value`` is in each purchase's
        # transaction currency; convert each at its posting-date rate
        # (mirroring calculate_lot_gain / _lot_decimals) so a holding
        # bought across foreign currencies isn't summed as raw mixed
        # units. Missing per-leg rate degrades to the raw value.
        cost_basis = Decimal("0")
        for s in account.splits:
            if today is not None and s.transaction.post_date > today:
                continue
            value = Decimal(str(s.value))
            txn_ccy = s.transaction.currency
            if txn_ccy != default_currency:
                leg_rate = self._cross_rate(
                    book, txn_ccy, default_currency,
                    as_of=s.transaction.post_date,
                )
                if leg_rate is not None:
                    value = value * leg_rate
            cost_basis += value
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
        allow_after: bool = True,
    ) -> tuple[Decimal, int, date] | None:
        """Cross-currency rate near ``as_of``, with the chosen
        price's age and date.

        ``1 unit of from_commodity == rate units of to_commodity``.

        Preference order:

        1. Direct price (``commodity=from, currency=to``) on or
           before ``as_of``, closest.
        2. Inverse price, on or before, closest (``1 / p.value``).
        3. Direct after ``as_of``, closest.
        4. Inverse after, closest.

        **Staleness cap:** candidates beyond
        ``GNUCASH_FX_STALENESS_DAYS`` (default 90) from ``as_of``
        are excluded — without it a 5-year-old rate could serve a
        current invoice. ``respect_staleness_cap=False`` disables it
        per-call for the valuation chain, which values holdings at
        the latest available rate regardless of age (the stale-price
        warning flags age there); the cap stays ON for invoice
        posting, where a stale rate gets etched.

        ``allow_after=False`` drops preferences 3–4 — used by the
        valuation chain for PAST anchors so a commodity first priced
        after the report date doesn't silently value at that future
        rate.

        Skips ``type='transaction'`` placeholders and
        zero-or-negative prices (div-by-zero guard on the inverse
        branch; same guard on direct for consistent signaling).

        Returns:
            ``(rate, age_days, price_date)`` — the freshness guard's
            inputs. Same-commodity → ``(Decimal("1"), 0, as_of)``;
            ``None`` when nothing usable exists within the window.
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
                if days < 0 and not allow_after:
                    continue
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
                if days < 0 and not allow_after:
                    continue
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
