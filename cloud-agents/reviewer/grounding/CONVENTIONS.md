# Coding Conventions — google-agentops-demo

These are the binding coding standards for the `google-agentops-demo` repository.
Every pull request is reviewed against these rules. Findings should cite the
rule number and text (e.g. "violates Rule 1: monetary values must be integer
cents").

## Domain & money

1. **Money is integer cents, never floats.** All monetary values are stored and
   passed as `int` cents (e.g. `unit_price_cents`, `subtotal_cents`). Never
   introduce `float` dollars into the domain. Currency is only formatted to a
   decimal string at the presentation boundary (e.g. `format_money`).

2. **Tax and rate math uses basis points.** Rates are expressed as integer basis
   points (bps, 1/100 of 1%), e.g. `tax_rate_bps`. Convert with `/ 10000` and
   `round()` to land back on whole cents — do not carry fractional cents.

## Value objects

3. **Value objects are frozen dataclasses.** Domain types (e.g. `LineItem`) are
   declared with `@dataclass(frozen=True)`. They are immutable; do not add
   setters or mutate fields after construction.

4. **Validation lives in `__post_init__` and raises `ValueError`.** Invariants
   (non-empty `sku`, non-negative prices, `quantity >= 1`) are enforced in
   `__post_init__`, raising `ValueError` with a clear message. Validation must
   not be silently skipped or downgraded to logging.

## Functions

5. **Public functions are pure and take `Iterable[LineItem]`.** Cart functions
   accept an `Iterable[LineItem]` and compute a result without mutating inputs or
   relying on external state. If a function must iterate twice, materialize once
   with `list(items)` (see `receipt_summary`).

6. **Every public function has a docstring.** Each public function carries a
   docstring describing what it returns; functions with non-obvious parameters
   document them in an `Args:` section (see `total_with_tax_cents`).

7. **Guard inputs explicitly.** Reject invalid inputs early with `ValueError`
   (e.g. negative `tax_rate_bps`, negative `cents` in `format_money`) rather than
   producing nonsensical output.

## Typing & style

8. **Full type annotations.** All parameters and return types are annotated.
   Modules use `from __future__ import annotations`. Prefer precise return types
   (e.g. `dict[str, int | str]`).

## Tests

9. **Every behavior change ships a regression test.** Any change to behavior is
   accompanied by a new or updated test under `tests/`. New public functions
   require tests covering both the happy path and the `ValueError` guard cases.

10. **Tests assert exact cent values.** Money assertions check exact integer
    cents (and exact formatted strings), not approximate floats.
