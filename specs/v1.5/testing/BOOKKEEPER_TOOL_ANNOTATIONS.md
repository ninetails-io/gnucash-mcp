# Bookkeeper loop — tool annotations + description rewrites

Branch: `feat/tool-annotations` (v1.5 cargo).
Change under test: derived MCP ToolAnnotations on all 110 tools;
rewritten descriptions for the 11 lowest-scoring tools from Glama's
per-tool review (delete_budget 2.4 … unvoid_transaction 3.4).
No behavior changes — the test is whether the new words and the
actual behavior agree anywhere a claim is checkable.

## Plan

1. Work normally first; note whether tool selection feels different.
2. Spot-verify the strongest new claims (scratch entities, test book):
   delete_budget permanence + bad-name error; update_vendor
   partial-update / notes="" / active=false semantics; get_account
   error-payload-on-miss; move_account cycle and same-name-sibling
   errors changing nothing; void→unvoid full-restore with splits
   returning unreconciled; delete_account_slot '/'-key rejection;
   net_worth series stepping from start_date with end_date last.
3. Standing question: what did you route around?

## Report (bookkeeper, 2026-08-04)

| Test | Claim | Result |
|---|---|---|
| delete_budget: throwaway | Permanent, removes amounts, no txns | ✅ |
| delete_budget: bad name | Errors cleanly | ✅ |
| update_vendor: partial | Omitted fields keep values | ✅ |
| update_vendor: notes="" | Clears | ✅ |
| update_vendor: active=false | Hides from default listing | ✅ |
| get_account: nonsense ref | Error payload, not exception | ✅ |
| move_account: cycle | Clean error, nothing changed | ✅ |
| move_account: same-name sibling | Clean error, nothing changed | ✅ |
| void → unvoid roundtrip | Full restore, splits unreconciled | ✅ |
| delete_account_slot: key with / | Rejected with explanation | ✅ |
| net_worth: start_date + interval | Rows step, end_date always last | ✅ |

Every claim verified. No description lies.

Qualitative: "Tool selection felt more confident. The behavior
annotations and the contract-style descriptions ('Errors, changing
nothing, if…') reduce the hesitation before calling a tool.
get_account specifically — knowing it returns an error payload
instead of raising — makes it a safe probe tool. Before this, I'd
avoid calling it speculatively."

Routed around (none attributable to this branch):
- tool_search ceremony to reach known tools — MCP-host-side loading
  friction, not this server's surface.
- Orphan "Test Vendor LLC" (000003) from a prior session — test-book
  hygiene; the new create_vendor duplicate warning would have
  prevented it.
- Account-structure guessing — already fixed by the frequent-used
  accounts section in get_book_summary (#146).

Recommendation: merge.
