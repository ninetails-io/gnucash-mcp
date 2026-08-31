"""Closed-loop continuation engine for the sample persona books.

Implements specs/v1.5/GENERATOR_MODERNIZATION_SPEC.md §2: a per-persona
policy layer that OPENS THE BOOK IT IS WRITING (via GnuCashBook — the
generators dogfood the server's own reporting) and derives money flows
from actual state instead of constants:

- card payment = f(statement balance) — pay-in-full personas pay the
  true balance at the statement close; bounded revolvers pay
  minimum-plus and are paid DOWN to their utilization bound, never up
  to zero (the interest burden is load-bearing for the debt demos);
- sweep = f(actual surplus) — ``max(0, checking − buffer)`` at month
  end, capped by ``max_monthly_sweep``. The cap is what makes drift
  repair STAGED without a separate repair mode: on first contact with
  a drifted book the surplus is huge, the cap binds for a few months,
  and the audit trail reads like a person draining a cash pile at a
  believable pace. In steady state the cap never binds.

Everything the policy acts on is read from the book; the only
constants left are the policy parameters themselves (buffers, shares,
bounds — see DRIFT_ANALYSIS.md for the measured derivations).

Determinism: every randomized choice (payment lag days, settle lags)
draws from ``random.Random(f"{persona}:{purpose}:{anchor}")`` — seeded
per (persona, purpose, date), so a re-run over the same range emits
identical transactions regardless of how much history precedes it.

The frozen prefix is sacred: nothing here rewrites or re-dates any
transaction at or before the continuation cutoff. Repair is appended
story (spec §2.3).
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator

from gnucash_mcp.book import GnuCashBook

D = Decimal


# ── Policy definitions ──────────────────────────────────────────

@dataclass(frozen=True)
class CardPolicy:
    """How one credit card gets paid each statement cycle."""

    account: str                    # full account path
    label: str                      # short name for descriptions
    kind: str                       # "pif" | "revolver"
    close_day_default: int = 31     # used when no statement_close_day slot
    # Revolver-only knobs (None on PIF cards):
    bound_utilization: D | None = None  # pay DOWN to this share of limit
    payment_plus: D | None = None       # steady-state payment above interest
    max_payment: D | None = None        # catch-up cap (gradual paydown)
    accrue_interest: bool = False       # policy books monthly interest
    interest_account: str | None = None  # expense account for accrual
    # Absolute floor for the repair narrative (default: buffer / 2).
    repair_min: D | None = None


@dataclass(frozen=True)
class PersonaPolicy:
    """Per-persona money-flow policy (spec §2.1). All amounts in the
    book's default currency."""

    key: str                        # persona selector ("alex", ...)
    currency: str                   # the book's default currency (ISO)
    checking: str                   # full path
    savings: str                    # full path
    buffer: D                       # checking floor the sweep preserves
    cards: tuple[CardPolicy, ...]
    savings_share: D                # share of monthly surplus → savings
    invest_months: tuple[int, ...]  # calendar months with invest sweeps
    savings_target: D               # rebalance savings down to this
    rebalance_tranche: D            # per-quarter savings→invest amount
    max_monthly_sweep: D            # staging cap (repair pacing)
    min_sweep: D                    # ignore dribble surpluses below this
    # invest(book_path, when, amount, source_path) writes the persona's
    # investment purchase (lot-per-sweep etc.). None = surplus goes to
    # savings only.
    invest: Callable[[Path, date, D, str], None] | None = None
    # ensure_rate(book_path, currency, when) guarantees a real FX rate
    # row exists for a cross-currency settlement date. None = persona
    # has no cross-currency documents.
    ensure_rate: Callable[[Path, str, date], None] | None = None
    # book_repairs(book_path, cutoff, through) -> list[str]: one-shot
    # persona-specific repairs appended in narrative on first contact
    # (e.g. Sabine's Ausgleichskonto clearing). Must be idempotent —
    # check the book before writing.
    book_repairs: Callable[[Path, date, date], list[str]] | None = None
    # Narrative templates. {month} is "%B %Y" of the statement close.
    desc_statement: str = "{label} — {month} statement payment"
    desc_repair_card: str = "{label} — balance payoff (catching up after the summer)"
    desc_sweep: str = "Transfer to savings (monthly surplus sweep)"
    desc_repair_sweep: str = "Transfer to savings — accumulated surplus"
    desc_topup: str = "Transfer from savings (checking top-up)"
    desc_interest: str = "{label} — interest"
    # A statement payment this many times the trailing month's charges
    # (or larger) gets the repair narrative instead of the routine one.
    repair_factor: D = D("3")


# ── Small deterministic helpers ─────────────────────────────────

def _seeded(persona: str, purpose: str, anchor: date, lo: int, hi: int) -> int:
    rng = random.Random(f"{persona}:{purpose}:{anchor.isoformat()}")
    return rng.randint(lo, hi)


def month_ends(after: date, through: date) -> Iterator[date]:
    """Every month-end date d with after < d <= through."""
    y, m = after.year, after.month
    while True:
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        end = nxt - timedelta(days=1)
        if end > through:
            return
        if end > after:
            yield end
        y, m = nxt.year, nxt.month


def _clamp_day(year: int, month: int, day: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return date(year, month, min(day, last))


def find_cutoff(book_path: Path) -> date:
    """The frozen edge: the latest posted transaction date. Clamped to
    today so a stray future-dated row can never push the continuation
    window into negative territory."""
    con = sqlite3.connect(str(book_path))
    try:
        row = con.execute(
            "SELECT MAX(date(post_date)) FROM transactions"
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        raise SystemExit(f"{book_path}: no transactions — not a built book?")
    cutoff = date.fromisoformat(row[0])
    return min(cutoff, date.today())


# ── Book-state readers (dogfooding the server's own reporting) ──

def _balance(book: GnuCashBook, account: str, as_of: date) -> D:
    return D(str(book.get_balance(account, as_of_date=as_of)))


def _slot_str(book: GnuCashBook, account: str, key: str) -> str | None:
    try:
        payload = book.get_account_slots(account, key=key)
    except ValueError:
        return None
    slots = payload.get("slots", payload) if isinstance(payload, dict) else {}
    value = slots.get(key)
    return None if value is None else str(value)


def _slot_int(book: GnuCashBook, account: str, key: str,
              default: int) -> int:
    value = _slot_str(book, account, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slot_decimal(book: GnuCashBook, account: str, key: str) -> D | None:
    value = _slot_str(book, account, key)
    try:
        return D(value)
    except Exception:
        return None


def _month_charges(book: GnuCashBook, account: str, close: date) -> D:
    """Magnitude of one statement cycle's net new charges: the balance
    movement over the ~30 days before ``close``, floored at zero."""
    prev = close - timedelta(days=30)
    delta = _balance(book, account, prev) - _balance(book, account, close)
    return delta if delta > 0 else D("0")


# ── Policy passes ───────────────────────────────────────────────

def _pay_card(book: GnuCashBook, policy: PersonaPolicy, card: CardPolicy,
              close: date, cutoff: date, through: date,
              log: list[str]) -> None:
    """Emit one statement payment (and revolver interest) for the
    cycle closing at ``close``. No-ops when the payment would land in
    the frozen prefix or past the horizon."""
    pay_lag = _seeded(policy.key, f"paylag:{card.label}", close, 3, 7)
    pay_date = close + timedelta(days=pay_lag)
    if pay_date <= cutoff or pay_date > through:
        return

    balance = _balance(book, card.account, close)  # liability: owed < 0
    owed = -balance
    if owed <= 0:
        return

    if card.kind == "revolver":
        limit = _slot_decimal(book, card.account, "credit_limit") or D("0")
        apr = _slot_decimal(book, card.account, "apr")
        if card.accrue_interest and apr is not None and owed > 0:
            interest = (owed * apr / D("100") / D("12")).quantize(D("0.01"))
            if interest > 0:
                book.create_transaction(
                    description=policy.desc_interest.format(label=card.label),
                    trans_date=close,
                    splits=[
                        {"account": card.account, "amount": str(-interest)},
                        {"account": card.interest_account,
                         "amount": str(interest)},
                    ],
                    check_duplicates=False,
                )
                owed += interest
        # NB: an explicit None-check — Decimal("0") is falsy, so ``or``
        # would silently turn a pay-to-zero bound into pay-to-limit.
        bound_util = (card.bound_utilization
                      if card.bound_utilization is not None else D("1"))
        bound = (limit * bound_util).quantize(D("0.01"))
        # Pay DOWN to the bound (the deliberate-debt persona keeps her
        # profile), with at least interest+payment_plus so the balance
        # never ratchets while under the bound. max_payment turns a
        # large arrears into a gradual catch-up instead of one payoff.
        floor_payment = ((card.payment_plus or D("0"))
                         + (owed * (apr or D("0")) / D("100") / D("12"))
                         ).quantize(D("0.01"))
        payment = max(owed - bound, floor_payment)
        if card.max_payment is not None:
            payment = min(payment, card.max_payment)
        payment = min(payment, owed)
    else:
        payment = owed

    if payment <= 0:
        return

    # Never overdraft checking for a card payment; cap and warn. In a
    # healthy book this cannot bind (PIF charges << checking).
    available = _balance(book, policy.checking, pay_date) - D("100")
    if payment > available:
        log.append(f"WARN {card.label}: payment {payment} capped to "
                   f"available {available} on {pay_date}")
        payment = available.quantize(D("0.01"))
        if payment <= 0:
            return

    # The repair narrative is for genuine catch-up payoffs: large
    # relative to the trailing cycle AND large on the persona's own
    # scale (half the buffer) — a routine payment after a quiet cycle
    # shouldn't claim to be a summer of catching up.
    trailing = _month_charges(book, card.account, close)
    repair_min = (card.repair_min if card.repair_min is not None
                  else policy.buffer / 2)
    is_repair = (trailing > 0
                 and payment >= trailing * policy.repair_factor
                 and payment >= repair_min)
    desc_tpl = policy.desc_repair_card if is_repair else policy.desc_statement
    desc = desc_tpl.format(label=card.label, month=close.strftime("%B %Y"))

    book.create_transaction(
        description=desc, trans_date=pay_date,
        splits=[
            {"account": policy.checking, "amount": str(-payment)},
            {"account": card.account, "amount": str(payment)},
        ],
        check_duplicates=False,
    )
    tag = "repair" if is_repair else "pay"
    log.append(f"{tag} {card.label}: {payment} on {pay_date} "
               f"(close {close})")


def _sweep(book: GnuCashBook, policy: PersonaPolicy, book_path: Path,
           month_end: date, log: list[str]) -> None:
    """Month-end surplus disposition: savings share monthly, invest
    share in cadence months, both bounded by the staging cap."""
    checking = _balance(book, policy.checking, month_end)
    surplus = checking - policy.buffer

    # The corridor's LOW side: a flow account that dipped under half
    # the buffer gets topped back up from savings (the move a real
    # person makes when checking runs thin — Sabine's Bankkonto lives
    # this way). The sweep is the HIGH side of the same corridor.
    if checking < policy.buffer * D("0.5"):
        available = _balance(book, policy.savings, month_end)
        topup = min(policy.buffer - checking, available).quantize(D("0.01"))
        if topup >= policy.min_sweep:
            book.create_transaction(
                description=policy.desc_topup, trans_date=month_end,
                splits=[
                    {"account": policy.savings, "amount": str(-topup)},
                    {"account": policy.checking, "amount": str(topup)},
                ],
                check_duplicates=False,
            )
            log.append(f"topup←savings: {topup} on {month_end}")
        return

    if surplus < policy.min_sweep:
        return
    sweep_total = min(surplus, policy.max_monthly_sweep)
    staged = surplus > policy.max_monthly_sweep

    # Steady state: savings share monthly, invest share in cadence
    # months (it parks in checking between quarters, like the original
    # sweep rhythm). While STAGED (drift repair — the surplus exceeds
    # the cap), the whole tranche goes to savings: repair is about
    # getting the pile out of checking at a believable pace; the
    # quarterly rebalance then walks savings down to its target.
    invest_month = (policy.invest is not None
                    and month_end.month in policy.invest_months)
    if staged:
        to_savings, to_invest = sweep_total, D("0")
    else:
        to_savings = (sweep_total * policy.savings_share).quantize(D("0.01"))
        to_invest = ((sweep_total - to_savings).quantize(D("0.01"))
                     if invest_month else D("0"))

    if to_savings >= policy.min_sweep:
        desc = policy.desc_repair_sweep if staged else policy.desc_sweep
        book.create_transaction(
            description=desc, trans_date=month_end,
            splits=[
                {"account": policy.checking, "amount": str(-to_savings)},
                {"account": policy.savings, "amount": str(to_savings)},
            ],
            check_duplicates=False,
        )
        log.append(f"sweep→savings: {to_savings} on {month_end}"
                   + (" (staged)" if staged else ""))

    if to_invest >= policy.min_sweep:
        policy.invest(book_path, month_end, to_invest, policy.checking)
        log.append(f"sweep→invest: {to_invest} on {month_end}")


def _rebalance(book: GnuCashBook, policy: PersonaPolicy, book_path: Path,
               month_end: date, log: list[str]) -> None:
    """Savings-pile rebalance (RULED 2026-08-31): quarterly
    savings→investment tranches until savings reaches its target, then
    the steady-state split alone. Reads like a person who got advice."""
    if policy.invest is None or month_end.month not in policy.invest_months:
        return
    savings = _balance(book, policy.savings, month_end)
    excess = savings - policy.savings_target
    if excess <= 0:
        return
    tranche = min(policy.rebalance_tranche, excess).quantize(D("0.01"))
    if tranche < policy.min_sweep:
        return
    policy.invest(book_path, month_end, tranche, policy.savings)
    log.append(f"rebalance savings→invest: {tranche} on {month_end}")


def _card_closes(book: GnuCashBook, card: CardPolicy, cutoff: date,
                 through: date) -> list[date]:
    """Statement close dates from one month before the cutoff's month
    (a statement the prefix left hanging — closed pre-cutoff, payment
    due post-cutoff — still gets its payment) through ``through``."""
    close_day = _slot_int(book, card.account, "statement_close_day",
                          card.close_day_default)
    y, m = cutoff.year, cutoff.month
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    closes = []
    while True:
        close = _clamp_day(y, m, close_day)
        if close > through:
            return closes
        closes.append(close)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def run_policy(policy: PersonaPolicy, book_path: Path, cutoff: date,
               through: date) -> list[str]:
    """The month-by-month closed loop (spec §2.2 step 4). Sequential by
    construction: each month's decisions see every transaction the
    engine has already written."""
    log: list[str] = []
    book = GnuCashBook(str(book_path))

    closes: list[tuple[date, CardPolicy]] = []
    for card in policy.cards:
        closes += [(c, card) for c in _card_closes(book, card, cutoff,
                                                   through)]

    events: list[tuple[date, str, object]] = []
    events += [(c, "card", card) for c, card in closes]
    events += [(e, "monthend", None) for e in month_ends(cutoff, through)]
    events.sort(key=lambda t: (t[0], t[1]))  # card pass before sweep on ties

    for when, kind, payload in events:
        if kind == "card":
            _pay_card(book, policy, payload, when, cutoff, through, log)
        else:
            _sweep(book, policy, book_path, when, log)
            _rebalance(book, policy, book_path, when, log)
    return log


# ── A/R / A/P settlement (aging pass) ───────────────────────────

def settle_documents(policy: PersonaPolicy, book_path: Path, cutoff: date,
                     through: date,
                     payment_account: str | None = None) -> list[str]:
    """Pay the prefix's posted-but-unpaid documents that a living book
    would have settled by ``through``.

    The plan generators leave the most recent documents open on
    purpose; without this pass every continuation ages them into
    looks-like-corruption receivables (the A/R twin of the card
    drift). Rule: a document settles ``~28-38`` seeded days after it
    opened, never before the cutoff, and anything whose settle date
    would land past ``through`` stays open — so the trailing window of
    genuinely recent open documents is preserved by construction."""
    log: list[str] = []
    book = GnuCashBook(str(book_path))
    pay_from = payment_account or policy.checking

    owner_by_type = {"invoice": "customer", "bill": "vendor",
                     "voucher": "employee"}

    outstanding = book.get_outstanding_invoices(compact=False, limit=250)
    for doc in outstanding.get("invoices", []):
        if doc.get("is_credit_note"):
            continue
        posted = doc.get("date_posted")
        if posted is None:
            continue
        posted = date.fromisoformat(str(posted)[:10])
        settle_lag = _seeded(policy.key, "settle", posted, 28, 38)
        pay_date = posted + timedelta(days=settle_lag)
        if pay_date <= cutoff:
            pay_date = cutoff + timedelta(
                days=_seeded(policy.key, "settle-late", posted, 4, 12))
        if pay_date > through:
            continue  # stays open — the recent window
        amount = D(str(doc.get("amount_due", "0")))
        if amount <= 0:
            continue
        currency = doc.get("currency")
        owner_type = owner_by_type.get(doc.get("type"), "customer")
        cross = currency is not None and currency != policy.currency
        if cross and policy.ensure_rate is not None:
            policy.ensure_rate(book_path, currency, pay_date)
        book.pay_invoice(
            invoice_id=doc["id"], payment_account=pay_from,
            amount=str(amount), payment_date=pay_date.isoformat(),
            owner_type=owner_type, force=cross,
        )
        log.append(f"settle {doc['id']}: {amount} {currency or ''} "
                   f"on {pay_date} (posted {posted})")
    return log


# ── Verification (spec §2.4) ────────────────────────────────────

def verify_invariants(policy: PersonaPolicy, book_path: Path, cutoff: date,
                      through: date) -> list[str]:
    """Post-run invariants over the continued range. Returns warnings;
    raises SystemExit on a hard violation (overdraft, revolver over
    limit)."""
    warnings: list[str] = []
    book = GnuCashBook(str(book_path))

    limits = {card.account: _slot_decimal(book, card.account, "credit_limit")
              for card in policy.cards}
    for month_end in list(month_ends(cutoff, through)) + [through]:
        checking = _balance(book, policy.checking, month_end)
        if checking < 0:
            raise SystemExit(
                f"{policy.key}: OVERDRAFT — checking {checking} at "
                f"{month_end}")
        lo, hi = policy.buffer * D("0.5"), policy.buffer * D("3")
        if month_end != through and not (lo <= checking <= hi):
            warnings.append(
                f"buffer band: checking {checking} outside "
                f"[{lo}, {hi}] at {month_end}")
        for card in policy.cards:
            limit = limits[card.account]
            owed = -_balance(book, card.account, month_end)
            if limit is not None and owed > limit:
                raise SystemExit(
                    f"{policy.key}: {card.label} owes {owed} over limit "
                    f"{limit} at {month_end}")

    for card in policy.cards:
        if card.kind != "pif":
            continue
        # After each statement payment the residual is only the charges
        # that landed during the payment lag — a fraction of a cycle.
        for close in _card_closes(book, card, cutoff, through):
            lag = _seeded(policy.key, f"paylag:{card.label}", close, 3, 7)
            pay_date = close + timedelta(days=lag)
            if pay_date <= cutoff or pay_date > through:
                continue
            residual = -_balance(book, card.account, pay_date)
            # ``± one cycle's charges`` (spec §2.4). _month_charges is a
            # NET movement — payments inside the window shrink it — so
            # allow 1.5× plus a floor to keep this a drift alarm, not a
            # lag-noise alarm. A never-paid card still trips it.
            cycle = _month_charges(book, card.account, close)
            bound = max(cycle * D("1.5"), D("500"))
            if residual > bound:
                warnings.append(
                    f"PIF residue: {card.label} owes {residual} after "
                    f"the {close} statement payment (bound {bound})")
    return warnings
