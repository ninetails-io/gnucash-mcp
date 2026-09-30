"""Canonical dump of what the credit-note parity flow wrote, GUIDs
mapped to roles, clock timestamps dropped, transactions ordered by
type. ``tests/fixtures/parity_credit_note_desktop.txt`` is this dump
of the book GnuCash desktop produced on 2026-09-29; the test runs the
same flow on the server and expects the same text."""
import sqlite3
import sys


def dump(path: str) -> str:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    names = {}  # guid -> role
    def role(g, r):
        if g and g not in names: names[g] = r
        return names.get(g, g)
    # accounts, customers, commodities by name
    for g, n in q("select guid, name from accounts"): names[g] = f"acct:{n}"
    for g, n in q("select guid, name from customers"): names[g] = f"cust:{n}"
    for g, m in q("select guid, mnemonic from commodities"): names[g] = f"ccy:{m}"
    for g, i in q("select guid, id from invoices"): names[g] = f"inv:{i}"
    for g, i in q("select guid, name from billterms"): names[g] = f"term:{i}"
    out = []
    def N(v): return names.get(v, v) if isinstance(v, str) else v
    # credit note row (its posting transaction and lot get their roles first)
    inv = q("select * from invoices where id='Parity01'")[0]
    _cols0 = [d[0] for d in con.execute("select * from invoices limit 1").description]
    _inv0 = dict(zip(_cols0, inv))
    if _inv0.get("post_txn"): names[_inv0["post_txn"]] = "txn:I"
    if _inv0.get("post_lot"): names[_inv0["post_lot"]] = "lot:Credit Note Parity01"
    cols = [d[0] for d in con.execute("select * from invoices limit 1").description]
    out.append("INVOICE " + " ".join(f"{c}={N(v)!r}" for c, v in zip(cols, inv) if c not in ("guid",)))
    # entries
    ecols = [d[0] for d in con.execute("select * from entries limit 1").description]
    for e in q("select * from entries where invoice=(select guid from invoices where id='Parity01')"):
        out.append("ENTRY " + " ".join(f"{c}={N(v)!r}" for c, v in zip(ecols, e) if c not in ("guid","date_entered")))
    # transactions dated today, roled by txn-type slot
    txns = q("select guid, num, post_date, description, currency_guid from transactions where post_date >= '2026-09-29' order by enter_date")
    for g, num, pd, desc, cur in txns:
        t = q("select string_val from slots where obj_guid=? and name='trans-txn-type'", g)
        ttype = t[0][0] if t else "-"
        role(g, f"txn:{ttype}")
    txns.sort(key=lambda t: (names[t[0]], t[2]))
    for g, num, pd, desc, cur in txns:
        out.append(f"TXN {names[g]} num={num!r} post_date={pd!r} desc={desc!r} ccy={N(cur)}")
        # Splits ordered by the account's NAME, not its GUID: GUIDs
        # differ between two books built the same way.
        rows = q("select account_guid, memo, action, reconcile_state, value_num, value_denom, quantity_num, quantity_denom, lot_guid, reconcile_date from splits where tx_guid=?", g)
        for s in sorted(rows, key=lambda r: (N(r[0]), r[4])):
            lot = s[8]
            if lot:
                title = q("select string_val from slots where obj_guid=? and name='title'", lot)
                role(lot, f"lot:{title[0][0] if title else '?'}")
            # rec_date: the epoch on an unreconciled split. Left out of
            # the first dump, which hid a local-midnight epoch on every
            # posted document's receivable split (2026-09-30).
            out.append(f"  SPLIT acct={N(s[0])} memo={s[1]!r} action={s[2]!r} rec={s[3]} rec_date={s[9]} value={s[4]}/{s[5]} qty={s[6]}/{s[7]} lot={N(lot)}")
    # lots touched
    for lot in sorted({r for r in names if names[r].startswith("lot:")}, key=lambda r: names[r]):
        l = q("select is_closed, account_guid from lots where guid=?", lot)[0]
        out.append(f"LOT {names[lot]} is_closed={l[0]} acct={N(l[1])}")
    # slots on every roled object (+frame children)
    # Slots only on objects the flow created (lot 000018 pre-exists in the base).
    objs = [g for g in names if names[g].startswith(("txn:", "inv:Parity01")) or names[g] == "lot:Credit Note Parity01"]
    def slots_of(obj, prefix):
        rows = q("select name, slot_type, string_val, int64_val, timespec_val, gdate_val, guid_val, numeric_val_num, numeric_val_denom, double_val from slots where obj_guid=? order by name", obj)
        for name, st, sv, iv, tv, gv, guid, nn, nd, dv in rows:
            val = {4: repr(sv), 1: iv, 6: tv, 10: gv, 5: N(guid), 3: f"{nn}/{nd}", 9: "frame"}.get(st, "?")
            # The columns this slot's type does NOT use: desktop's SQL
            # backend fills them one way, piecash's ORM another, and a
            # dump of the typed column alone can't tell (2026-09-30).
            fill = [
                None if st == 1 else f"i={iv}",
                None if st == 4 else f"s={sv!r}",
                None if st == 2 else f"d={dv}",
                None if st == 6 else f"t={tv}",
                None if st == 3 else f"n={nn}/{nd}",
                None if st == 10 else f"gd={gv}",
            ]
            out.append(
                f"SLOT {prefix} {name} type={st} {val} fill["
                + " ".join(f for f in fill if f) + "]"
            )
            if st == 9 and guid:
                slots_of(guid, prefix)
    for obj in sorted(objs, key=lambda g: names[g]):
        slots_of(obj, names[obj])
    return "\n".join(out)


if __name__ == "__main__":
    print(dump(sys.argv[1]))
