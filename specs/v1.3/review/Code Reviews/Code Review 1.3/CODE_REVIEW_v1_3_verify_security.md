# Security Claims — Adversarial Verification

Each claim was tested by reading the cited source and constructing a
concrete proof-of-concept. The goal was refutation, not confirmation;
findings below are what survived a refutation attempt.

---

## Claim SEC-1 — path traversal in `get_audit_log`

### Verdict
**CONFIRMED** (with one mitigating constraint).

### Reasoning
File cited: `src/gnucash_mcp/tools/admin.py`, `get_audit_log` lines
93-160. The handler builds the file path with:

```python
target_date = log_date or datetime.now().astimezone().strftime("%Y-%m-%d")
log_file = audit_dir / f"{target_date}.txt"
```

There is no validation of `log_date`. The string is interpolated
directly into the path, and `pathlib.Path.__truediv__` does **not**
collapse `..` segments — it just concatenates components. Reading
the file is then unconditional once `log_file.exists()` is true. The
function is decorated `@audit_log(classification="read")`, which has
no write-gate; the only thing protecting the file is whether the
host's filesystem permissions let the server process open it.

A real constraint exists: the suffix `.txt` is **always** appended.
The attacker can only target files whose absolute path ends in
`.txt`. On a server running as the gnucash-mcp user, candidates
include the project's own `audit/*.txt` log files, any user-side
`*.txt` data the process can read, and many configuration / readme
artifacts. This narrows but does not eliminate the disclosure
surface — e.g., `~/.ssh/known_hosts` is not `.txt`, but a `notes.txt`
in the user's home directory is. The path-resolution behaviour is the
bug; the `.txt` requirement just limits the blast radius.

A second-order concern: the file is read with up to 2 MB of content
returned verbatim through the MCP boundary. If the attacker can guess
or enumerate a sensitive `.txt`, the contents come back as the tool
response.

### Concrete proof-of-concept

Tested `pathlib` behaviour out-of-band:

```
audit_dir = Path('/tmp/audit')
target_date = '../../../../etc/passwd'
log_file = audit_dir / f'{target_date}.txt'
# log_file = /tmp/audit/../../../../etc/passwd.txt
# resolved = /private/etc/passwd.txt
```

The path traverses out of the audit directory cleanly. Concrete
attack inputs:

1. `get_audit_log(log_date="../../../../etc/hosts")` — would resolve
   to `/etc/hosts.txt`. (Doesn't exist by default, but demonstrates
   escape.)
2. `get_audit_log(log_date="../../../../../Users/stephen/notes")` —
   resolves to `/Users/stephen/notes.txt`. If present, contents are
   returned in full.
3. `get_audit_log(log_date="../../sample_books/alex_chen_phase11_README")`
   — resolves to `<project>/sample_books/alex_chen_phase11_README.txt`
   if such a file exists (the audit dir lives under the project's log
   directory, so `..` walks into project-adjacent paths).

A trivial fix would validate `log_date` against
`re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date)` before constructing
the path, or use `log_file.resolve().is_relative_to(audit_dir.resolve())`
as a post-construction guard.

---

## Claim SEC-2 — `prune_backups` wipes every auto backup chain when
called with `stage=None, keep_last_n=0, dry_run=False`

### Verdict
**CONFIRMED as factual; framing as "unprotected catastrophe" is
contested by the in-source design rationale.**

### Reasoning
File cited: `src/gnucash_mcp/book/backup.py`, `prune_backups` lines
518-639.

Tracing the call `prune_backups(keep_last_n=0, stage=None,
dry_run=False)`:

1. `keep_last_n >= 0` passes (line 541).
2. `stage is None`, so the `_ALL_STAGE_NAMES` check at line 543 is
   skipped.
3. The catastrophic-footgun guard at lines 561-574 requires
   `stage == _MANUAL_STAGE_NAME`. With `stage=None`, the guard
   **does not fire**. Confirmed.
4. `target_stages = set(_AUTO_STAGE_NAMES)` =
   `{"session", "weekly", "monthly"}` (line 587).
5. For each entry-list per stage: `keep = entries[:0]` is empty,
   `drop = entries[0:]` is every backup. All session, weekly, and
   monthly backups end up in `would_delete`.
6. With `dry_run=False`, the loop at line 626 unlinks every file in
   `would_delete`.

So yes — the call deletes every auto backup (session + weekly +
monthly chains) in a single call. Manual backups are correctly
protected (filtered out at step 4 because they're not in
`_AUTO_STAGE_NAMES`).

**However**, the claim's framing as "catastrophe" overstates what
the source code itself documents. The comment at lines 549-560 is
explicit:

> "Auto-stage zero-retention is still allowed because those have
> policy-driven retention and the user can always rebuild them."

The auto-backup driver (`_maybe_auto_backup`, line 643) regenerates
the chain on the next first-write of a new process — so the impact
is bounded to "between this moment and the next session", not
"data lost forever". Manual backups, which carry the
"pre-tax-filing" / "pre-irreplaceable-thing" human-marked snapshots,
are intentionally separated and **are** guarded.

The behaviour is therefore *intentional* by the design comment, not
an oversight. Whether to upgrade the protection (e.g., require a
non-default `confirm_auto=True` flag for `keep_last_n=0` on `None`
stage) is a policy call, not a fix to an unintended bypass. The
review item is more accurately phrased as a *hardening* recommendation
than a bug refutation.

### Concrete proof-of-concept

Given a state of the synthetic-book deployment with:

```
session: 7 backups (per default keep_last_n)
weekly:  4 backups
monthly: 6 backups
manual:  3 backups (pre-tax-filing, year-end-2025, before-migration)
```

A single tool call:

```
prune_backups(keep_last_n=0, stage=None, dry_run=False)
```

would unlink **17 files** (7+4+6) and leave the 3 manual backups
intact. Worked-example trace through the code path is the one above.
The `would_delete` list returned to the caller would contain all 17
entries; the actual `deleted` list (after the unlink loop) would
contain however many `p.unlink()` succeeded.

A misclicking caller (or an LLM that misread the parameter sense)
could trigger this with no second-level confirmation. The fix would
be a single `if stage is None and keep_last_n == 0 and not dry_run`
guard mirroring the manual-stage guard above it — symmetric, cheap,
and consistent with the project's stated "seatbelt against data
loss" philosophy.

---

## Claim SEC-3 — unbounded slot value sizes (`set_account_slot`,
`void_transaction.reason`)

### Verdict
**CONFIRMED.**

### Reasoning
Files cited:

- `src/gnucash_mcp/book/admin.py`, `set_account_slot` lines 71-115.
- `src/gnucash_mcp/book/reconciliation.py`, `void_transaction` lines
  448-542.

`set_account_slot`: the only validation on `value` is its type
annotation (`value: str`). There is no `len(value) <` check anywhere
in the function. The key is validated against
`_SLOT_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")`, but the value
passes through to `account[key] = value` → `book.save()` unfiltered.
Piecash stores this in the KVP slot table as a string column; SQLite
TEXT has no practical limit short of `SQLITE_MAX_LENGTH` (default
1 GB, configurable up to 2^31-1).

`void_transaction`: the only validation on `reason` is
`if not reason or not reason.strip(): raise ValueError(...)` at line
478. No upper bound. The reason is then stored via
`transaction["void-reason"] = reason` at line 509, again as a slot.

Same exhaustion model. A caller can pass arbitrarily large strings
to either tool. The book file grows by that amount (plus SQLite's
internal overhead) on each call; subsequent `book.open()` calls
parse the slot table on connection and hold relevant rows in memory
during operations like `get_account_slots(account)` which iterates
all slots.

Tool-layer wrappers (`tools/admin.py` lines 50-67, `tools/reconciliation.py`
`void_transaction`) add no validation either. The MCP boundary itself
imposes no per-string size limit beyond JSON parsing — i.e., effectively
none for a request that the client is willing to send.

### Concrete proof-of-concept

Three concrete attack paths:

1. **One-shot bloat.** A single call:

   ```
   set_account_slot(
       account="Assets:Bank:Checking",
       key="notes",
       value="A" * (100 * 1024 * 1024),   # 100 MB
   )
   ```

   The string is stored verbatim. The next `book.save()` writes a
   ~100 MB row to the SQLite KVP table. Subsequent
   `get_account_slots(account="Assets:Bank:Checking")` materialises
   the full string into the response (passing through `_slot_value_str`
   without truncation) and the JSON serializer attempts to encode
   100 MB into the MCP response. Both ends — the server process
   memory and the JSON-over-stdio transport — strain.

2. **Per-write quadratic on void.** Every `void_transaction(guid, reason)`
   call accepts an arbitrarily large `reason` and stores it in the
   transaction's slot. The book's audit log capture
   (`_stage_audit_before` plus the dispatch-table formatter) holds
   the before-state dict in memory across the write, doubling the
   memory cost during the call. Repeating the call across N
   transactions with a 50 MB reason multiplies the on-disk and
   in-memory cost N-fold; `get_audit_log` then renders these into
   the daily `.txt` file (capped at 2 MB read, but unlimited write —
   the file itself grows without bound and could outpace disk).

3. **Cumulative file growth.** No per-account or per-book slot cap
   exists either. A loop of `set_account_slot(key=f"junk_{i}", value="A"*1e6)`
   adds 1 MB rows indefinitely. The book file's SQLite KVP table
   grows linearly; piecash's `book.open()` parses the schema and
   relationships eagerly on connect, so opening a many-MB book
   takes proportionally longer — eventually pushing past the
   `open()` retry/backoff window and presenting as "book locked"
   to legitimate writes.

A trivial mitigation: cap `len(value)` at, say, 4096 bytes in
`set_account_slot` (slots are designed for short metadata like
"apr"=0.1899, "credit_limit"=12000), and cap `len(reason)` at,
say, 1024 bytes in `void_transaction` (audit-trail prose, not
freeform document storage). Both could be a single line at the
top of each method.

---

## Cross-claim summary

| Claim | Verdict | Severity stands as written? |
|------|---------|------------------------------|
| SEC-1 path traversal | Confirmed | Yes — file-read disclosure of any `.txt` the server user can read |
| SEC-2 prune_backups | Confirmed factual, but intentional per source comment | Partial — claim is true, framing as "unprotected catastrophe" understates the in-source design rationale; suggest reframing as a hardening recommendation |
| SEC-3 unbounded slot values | Confirmed | Yes — concrete DoS / file-growth exhaustion path with no upper bound enforced |

None of the three claims could be refuted on the code as it stands
on `develop` at HEAD. SEC-1 and SEC-3 are unambiguous defects; SEC-2
is a deliberate-design-choice flagged as worth hardening rather than
a bypass of an existing protection.
