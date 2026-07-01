# v1.1 — Modular tool loading

No specs live here, by design.

v1.1.0 was a **refactor release**, not a feature release. It split the
`book.py` monolith into per-area mixins composed via `build_book_class`,
with tool registration lazy-loaded per module and gated by the
`--modules` flag. The user-visible surface didn't gain features — it
gained the ability to load only the tool categories a client needs.

Because the work was internal restructuring rather than new behavior, it
was implemented directly against the codebase without a design spec. The
record of what shipped lives in:

- `CHANGELOG.md` → the v1.1.0 entry
- the `feat/modularize-book` commit history

Feature releases in this project get durable specs (see `v0.9/`, `v1.0/`,
`v1.2/`, `v1.3/`, `v1.4/`). The two releases without specs are the two
that weren't features: v0.1 (initial scaffold) and v1.1 (this
modularization refactor).
