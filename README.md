# ReconAgent

Multi-source reconciliation agent that matches a bank feed against a GL/ERP ledger through a rule-based matching cascade, reporting a **measured** match rate — verified against a hidden ground-truth key — instead of a self-reported one.

## What it solves

Bank feeds and internal ledgers never agree perfectly: settlement delays, bank fees, FX drift, garbled descriptions, and duplicate submissions all break naive amount-matching. This project reconciles a 113-record synthetic batch (55 ledger entries, 58 bank transactions) and produces:

- A **match rate**, computed, not asserted
- A full **exception list** for everything it couldn't resolve — nothing hidden or force-matched
- **Verified accuracy** — a second, independent script grades the matches against a hidden answer key the matching logic never sees

## Results

| Metric | Value |
|---|---|
| Ledger entries matched | 51 / 55 (92.7%) |
| Match precision (vs. ground truth) | 100% |
| Match recall on truly matchable pairs | 98% (50/51) |
| Exceptions requiring human review | 11 |

## How it works

Each ledger entry runs through a 4-stage cascade, strictest and cheapest first, stopping at the first confident match:

1. **Exact reference match** — a reference code embedded in the bank description (e.g. `GL2026070012`), amount agrees to the cent.
2. **Amount + date + counterparty** — no reference code, but amount matches exactly, date is within 1 day, and the counterparty name appears (even partially) in the bank description.
3. **Delayed settlement match** — same as above, but with a 7-day date window, since banks often post days after the ledger entry is booked.
4. **Fuzzy name match** — string-similarity fallback for heavily garbled or shortened bank descriptions.

Anything that survives all four stages unmatched becomes an **exception** — categorized as an unmatched ledger entry (likely awaiting settlement), a bank-only item never booked to the GL (fees, interest), or a possible duplicate payment.

There's no ML/LLM in the matching path — reconciliation needs to be deterministic and auditable, so every match is logged with the exact strategy and confidence tier that produced it.

## Why three scripts, not one

- `generate_data.py` builds the test case: two realistic, messy CSVs, plus a hidden answer key it keeps to itself.
- `reconcile_agent.py` does the actual work. It only ever reads `ledger.csv` and `bank_feed.csv` — never the answer key.
- `verify_accuracy.py` is a separate script that grades the agent's output against that hidden key afterward. This is what makes the match rate a measured claim rather than the agent grading its own homework.

## Running it

Requires Python 3.9+ and pandas (`pip install pandas`).

```bash
python3 generate_data.py      # builds ledger.csv, bank_feed.csv, bank_feed_groundtruth.csv
python3 reconcile_agent.py    # reads the two CSVs, writes matches.csv and exceptions.csv
python3 verify_accuracy.py    # grades matches.csv against the hidden ground truth
```

## File structure

```
generate_data.py               synthetic data generator
reconcile_agent.py              the matching engine
verify_accuracy.py              independent accuracy check
ledger.csv                      GL/ERP export (input)
bank_feed.csv                   bank statement export (input)
bank_feed_groundtruth.csv       hidden answer key (used only by verify_accuracy.py)
matches.csv                     output: 51 matched pairs, with strategy + confidence
exceptions.csv                  output: 11 unresolved items, with category + detail
reconciliation_report.md        full write-up of methodology and results
```

## Known limitations

- The fee/FX drift tolerance (3%) is a judgment call — tighter risks missing legitimate fee-adjusted payments, looser risks masking real discrepancies.
- Counterparty matching relies on the ledger name appearing (even partially) in the bank text — a renamed or rebranded counterparty would need a name-alias table in production.
- Duplicate detection is reference+amount based; it can't distinguish a true double-payment from two coincidentally identical transactions without an invoice-level ID.
