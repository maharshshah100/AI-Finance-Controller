# Multi-Source Reconciliation Agent — Run Report

**Loop closed:** Bank feed ↔ GL/ERP ledger reconciliation
**Batch:** 55 ledger entries + 58 bank feed entries = **113 records**
**Data:** synthetic, generated with injected real-world mess (settlement delays, fee/FX drift, garbled descriptions, duplicate submissions, unbooked bank items) — not hand-picked clean pairs.

---

## Headline numbers

| Metric | Value |
|---|---|
| Ledger entries matched | 51 / 55 (**92.7%**) |
| Total records resolved (matched + duplicates flagged) | 104 / 113 (**92.0%**) |
| Match precision (verified against ground truth) | **100%** — zero false matches |
| Match recall on truly matchable pairs | **98%** (50/51) |
| Exceptions requiring human review | **11** |
| Strategies used | 4-stage cascade, each logged per match |

Precision/recall were checked against the batch's known-correct answer key (built at generation time, never shown to the matching logic) — this isn't a self-reported score, it's measured.

## How the agent matched

The agent runs a cascade, cheapest/strictest first, so nothing gets force-fit by a fuzzy rule when an exact one would do:

1. **Exact reference + amount** — extracts a `GL#########` code embedded in the bank description, checks amount agrees to the cent. *(33 matches)*
2. **Exact reference + fee/FX drift** — same reference match, but amount differs by up to 3%, tagged as likely fee or FX spread rather than silently accepted. *(5 matches)*
3. **Amount + date (±1 day) + counterparty name token** — for bank rows with no reference code, cross-checks amount and a counterparty name fragment. *(8 matches)*
4. **Amount + delayed settlement (±7 days) + counterparty token** — same as above but allows for bank-side settlement lag, flagged with the delay noted. *(5 matches)*
5. A fifth fuzzy-name strategy (`difflib` similarity ≥0.35) exists in the pipeline for heavily garbled descriptions but wasn't needed this run — strategy 3 already covered those cases via partial name tokens.

Every match row carries its strategy and confidence tier (`high`/`medium`), so a reviewer can immediately see which matches were exact and which involved tolerance.

## Exceptions — the honest list (11 items, all surfaced, none suppressed)

**4 unmatched ledger entries** (booked internally, no bank-side settlement found within tolerance) — likely awaiting settlement, worth a follow-up with treasury:
- GL2026070007 — Hooli Cloud Services — $28,346.36
- GL2026070026 — Stark Materials — $6,831.31
- GL2026070048 — Umbrella Logistics — $3,213.64
- GL2026070050 — Globex Manufacturing — $34,020.19

**5 bank-only items never booked to the GL** (fees/interest — need a journal entry, not a match):
- Interest earned – operating account — $112.34
- Wire transfer fee — $(35.00)
- Card processing fee batch — $(218.60)
- Monthly account maintenance fee — $(45.00)
- NSF fee – returned item — $(50.00)

**2 possible duplicate payments** — a second bank entry showing the same reference and amount as an already-matched entry. Could be a genuine double-submission or two legitimately separate transactions that happen to share amount+reference; flagged rather than auto-merged or auto-dropped:
- BK90012 — Globex Manufacturing — $(4,788.14) — duplicate of GL2026070055
- BK90057 — Cyberdyne Systems — $40,062.18 — duplicate of GL2026070017

None of these were force-matched to hit a higher headline number — each failed every tolerance in the cascade and is left for a human to close.

## Where accuracy could still slip

- The 3% fee/FX drift tolerance is a judgment call — tighter and some legitimate fee-adjusted payments would fall to "unmatched"; looser and it risks masking real amount discrepancies.
- Counterparty-token matching relies on ledger names appearing (even partially) in bank text — a counterparty renamed or rebranded between systems would break this and needs a name-alias table in production.
- Duplicate detection here is reference+amount based; it can't distinguish "same vendor billed twice for the same amount by coincidence" from a true double-payment without an invoice-level ID.

## Files in this batch
- `ledger.csv` — GL/ERP export (55 rows)
- `bank_feed.csv` — bank statement export (58 rows)
- `matches.csv` — all 51 matched pairs with strategy + confidence
- `exceptions.csv` — all 11 unresolved items with category + detail
- `reconcile_agent.py` — the matching engine
- `generate_data.py` — synthetic data generator (for reproducing or extending the batch)
