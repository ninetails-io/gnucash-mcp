# Upgrading to 1.5

Version 1.5 stores scheduled transactions, budgets, invoice links,
credit notes, payments, prices, voids, lots, and reconciliation
details in exactly the format GnuCash desktop uses, so the server and
desktop can work on the same book. A book that version 1.2 through
1.4.4 has written to is converted to that format once, on the first
write after you upgrade.

This guide takes about ten minutes. Read it before your first write
with 1.5.

---

## Before you upgrade

1. **Close GnuCash desktop** if it has the book open.
2. **Know where your backups are.** For a book file, the server keeps
   them in a folder beside it: `mybook.gnucash.mcp/backups/`.
3. **If your book is in PostgreSQL or MySQL/MariaDB**, take a dump
   now (`pg_dump`, or `mysqldump` / `mariadb-dump`). The server makes
   its own copy only of book files.

## Install 1.5

- **Claude Desktop bundle:** download the new `.mcpb` from the
  [latest release](https://github.com/ninetails-io/gnucash-mcp/releases/latest)
  and double-click it. If the installer asks for your book, choose
  the same file as before. Quit and reopen Claude Desktop.
- **Installed from a clone:**

  ```bash
  cd gnucash-mcp
  git pull
  uv tool install -e . --reinstall
  ```

  Then quit and reopen your AI client.

Reading the book changes nothing. You can ask questions, run reports,
and look at the dashboard before converting.

## The first write converts the book

The first write that touches schedules, budgets, invoices or bills,
prices, voids, or reconciliation converts everything at once:

1. **The server copies the book first.** The copy is in the backups
   folder, labelled `pre-1-5-upgrade`
   (`mybook-<timestamp>-manual-pre-1-5-upgrade.gnucash`), and is never
   removed by backup retention. If the copy cannot be made, the write
   is refused and nothing changes.
2. **It converts the stored shapes.** No transaction is posted, and
   no transaction's amounts, dates, or accounts change.
3. **It reports what it converted**, in the response and in the
   audit log.

The conversion is one-way. The copy from step 1 is how you go back.

**Make this first write before you next open the book in GnuCash
desktop.** Until it is converted, a schedule created by an earlier
version can't be opened in GnuCash 5.12's Scheduled Transaction
Editor (GnuCash closes). While any remain, the dashboard says so:

> N schedules on the 1.4 recipe: GnuCash's schedule editor crashes on
> them until converted

To convert on purpose, without changing anything, ask your assistant:

> Update one of my scheduled transactions without changing anything.

That calls `update_scheduled_transaction` with no changes, which
converts the whole book and posts nothing. A book with no schedules
converts on its next ordinary write of one of the kinds above.

## Check that it worked

- The response to the first write lists what was converted, and the
  dashboard no longer shows the schedule line above.
- The backups folder holds a marker file,
  `.pre-1-5-upgrade-<book name>`. Its second line names the copy that
  was taken (`snapshot: mybook-…-pre-1-5-upgrade.gnucash`), or reads
  `snapshot: none (nothing to convert)` if the book needed nothing.
- Open the book in GnuCash desktop. Scheduled transactions open in
  the editor, and Since Last Run lists them.

## Use one version per book

Once a book is converted, don't point a 1.4 server at it, for example
from a second computer or another AI client you haven't upgraded.
Upgrade every installation that uses the book. If a 1.4 server does
write to a converted book, the next 1.5 write reports it, and the
dashboard names what to review for 30 days.

## Going back

To return to the book as it was before the conversion, restore the
`pre-1-5-upgrade` copy by following
[RESTORE_FROM_BACKUP.md](RESTORE_FROM_BACKUP.md). Anything written
after the conversion is not in that copy. To keep using the restored
book, reinstall the earlier version as well; 1.5 would convert it
again on its next write.

---

## What you'll notice in 1.5

- **Minimum GnuCash version is 3.8.** A book the server has written to
  carries a feature marker that GnuCash 3.0–3.7 can't open.
- **Invoices total the way GnuCash totals them**, including its tax
  rounding and any line discount entered in desktop. A draft may
  differ by a cent from what an earlier version showed. Posted
  documents keep the total they were posted at.
- **Due dates follow your billing terms**, as in GnuCash, including
  proximo terms. A document with no terms is due on its posting date,
  so it may show as overdue for the first time.
- **A payment from an account in another currency** is recorded in
  that account's currency, as GnuCash records it. Payments already in
  the book are left as they are.
- **One price per currency pair per day.** A second price on the same
  day replaces the first when its source ranks the same or higher.
- **Books can live in PostgreSQL or MySQL/MariaDB**: see the README.

Most other changes add new tools, parameters, and response fields.
One field was removed: `no_terms` is no longer in
`get_outstanding_documents`, since every document now has a due
date. The full list is in the [CHANGELOG](../CHANGELOG.md).
