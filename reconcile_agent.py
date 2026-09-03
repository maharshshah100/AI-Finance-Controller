"""
Multi-source reconciliation agent.

Input:  ledger.csv (GL/ERP export), bank_feed.csv (bank statement export)
Output: reconciliation_report.md, exceptions.csv, matches.csv

The agent only ever sees ledger.csv and bank_feed.csv - no scenario labels,
no ground truth. It matches via a cascade of strategies, each stricter than
the last, and logs which strategy resolved each match so the report is
auditable rather than a black box.
"""
import re
import difflib
import pandas as pd
from datetime import datetime

ledger = pd.read_csv("ledger.csv")
bank = pd.read_csv("bank_feed.csv")
bank["gl_date"] = pd.to_datetime(bank["bank_date"])
ledger["gl_date_dt"] = pd.to_datetime(ledger["gl_date"])

AMOUNT_TOL_EXACT = 0.01
AMOUNT_TOL_FEE_PCT = 0.03      # allow up to 3% drift (fees/FX) before flagging as mismatch-not-match
DATE_WINDOW_EXACT = 1          # days
DATE_WINDOW_DELAY = 7          # days, for settlement-delay matching

GL_REF_RE = re.compile(r"\bGL\d{9,}\b")

def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())

matched_bank_idx = set()
matches = []       # list of dicts
exceptions = []    # list of dicts

ledger_remaining = ledger.copy()
bank_remaining = bank.copy()

def record_match(gl_row, bk_row, strategy, confidence, note=""):
    matches.append({
        "gl_ref": gl_row.gl_ref,
        "bank_ref": bk_row.bank_ref,
        "gl_amount": gl_row.gl_amount,
        "bank_amount": bk_row.bank_amount,
        "amount_delta": round(bk_row.bank_amount - gl_row.gl_amount, 2),
        "gl_date": gl_row.gl_date,
        "bank_date": bk_row.bank_date,
        "counterparty": gl_row.counterparty,
        "strategy": strategy,
        "confidence": confidence,
        "note": note,
    })

# ---------- Strategy 1: exact reference-code match embedded in bank description ----------
bank_remaining["extracted_ref"] = bank_remaining["description"].apply(
    lambda d: (GL_REF_RE.search(str(d)).group(0) if GL_REF_RE.search(str(d)) else None)
)

matched_gl_refs_s1 = set()
for _, bk in bank_remaining.iterrows():
    if bk.extracted_ref is None or bk.bank_ref in matched_bank_idx:
        continue
    candidates = ledger_remaining[ledger_remaining.gl_ref == bk.extracted_ref]
    if len(candidates) == 1:
        gl = candidates.iloc[0]
        if gl.gl_ref in matched_gl_refs_s1:
            continue  # a second bank row claims the same GL ref -> leave for duplicate detection below
        delta = abs(bk.bank_amount - gl.gl_amount)
        if delta <= AMOUNT_TOL_EXACT:
            record_match(gl, bk, "S1_exact_ref+amount", "high")
            matched_bank_idx.add(bk.bank_ref)
            matched_gl_refs_s1.add(gl.gl_ref)
        elif delta <= abs(gl.gl_amount) * AMOUNT_TOL_FEE_PCT:
            record_match(gl, bk, "S1_exact_ref+fee_drift", "medium",
                         note=f"amount differs by {delta:.2f} (within {AMOUNT_TOL_FEE_PCT:.0%} tolerance, likely fee/FX)")
            matched_bank_idx.add(bk.bank_ref)
            matched_gl_refs_s1.add(gl.gl_ref)
        # else: ref matches but amount is way off -> leave unmatched, will surface as exception

ledger_remaining = ledger_remaining[~ledger_remaining.gl_ref.isin(matched_gl_refs_s1)]
bank_remaining = bank_remaining[~bank_remaining.bank_ref.isin(matched_bank_idx)]

# ---------- Strategy 2: exact amount + date window + counterparty token in description ----------
matched_gl_refs_s2 = set()
for _, gl in ledger_remaining.iterrows():
    best = None
    cp_tokens = [t.upper() for t in re.split(r"\s+", gl.counterparty) if len(t) > 2]
    for _, bk in bank_remaining.iterrows():
        if bk.bank_ref in matched_bank_idx:
            continue
        date_diff = abs((pd.to_datetime(bk.bank_date) - pd.to_datetime(gl.gl_date)).days)
        amt_diff = abs(bk.bank_amount - gl.gl_amount)
        cp_hit = any(tok in str(bk.description).upper() for tok in cp_tokens)
        if amt_diff <= AMOUNT_TOL_EXACT and date_diff <= DATE_WINDOW_EXACT and cp_hit:
            best = (bk, "S2_amount+date+counterparty", "high", "")
            break
    if best:
        bk, strat, conf, note = best
        record_match(gl, bk, strat, conf, note)
        matched_bank_idx.add(bk.bank_ref)
        matched_gl_refs_s2.add(gl.gl_ref)

ledger_remaining = ledger_remaining[~ledger_remaining.gl_ref.isin(matched_gl_refs_s2)]
bank_remaining = bank_remaining[~bank_remaining.bank_ref.isin(matched_bank_idx)]

# ---------- Strategy 3: exact amount + wider date window (settlement delay) + counterparty token ----------
matched_gl_refs_s3 = set()
for _, gl in ledger_remaining.iterrows():
    best = None
    cp_tokens = [t.upper() for t in re.split(r"\s+", gl.counterparty) if len(t) > 2]
    for _, bk in bank_remaining.iterrows():
        if bk.bank_ref in matched_bank_idx:
            continue
        date_diff = abs((pd.to_datetime(bk.bank_date) - pd.to_datetime(gl.gl_date)).days)
        amt_diff = abs(bk.bank_amount - gl.gl_amount)
        cp_hit = any(tok in str(bk.description).upper() for tok in cp_tokens)
        if amt_diff <= AMOUNT_TOL_EXACT and date_diff <= DATE_WINDOW_DELAY and cp_hit:
            best = (bk, "S3_amount+delayed_settlement+counterparty", "medium",
                    f"bank date {date_diff}d after GL date - likely settlement delay")
            break
    if best:
        bk, strat, conf, note = best
        record_match(gl, bk, strat, conf, note)
        matched_bank_idx.add(bk.bank_ref)
        matched_gl_refs_s3.add(gl.gl_ref)

ledger_remaining = ledger_remaining[~ledger_remaining.gl_ref.isin(matched_gl_refs_s3)]
bank_remaining = bank_remaining[~bank_remaining.bank_ref.isin(matched_bank_idx)]

# ---------- Strategy 4: fuzzy counterparty-name match (short/garbled bank descriptions) + exact amount + date window ----------
matched_gl_refs_s4 = set()
for _, gl in ledger_remaining.iterrows():
    best = None
    best_score = 0.0
    for _, bk in bank_remaining.iterrows():
        if bk.bank_ref in matched_bank_idx:
            continue
        date_diff = abs((pd.to_datetime(bk.bank_date) - pd.to_datetime(gl.gl_date)).days)
        amt_diff = abs(bk.bank_amount - gl.gl_amount)
        if amt_diff > AMOUNT_TOL_EXACT or date_diff > DATE_WINDOW_EXACT:
            continue
        score = difflib.SequenceMatcher(None, norm(gl.counterparty), norm(bk.description)).ratio()
        if score > best_score:
            best_score = score
            best = bk
    if best is not None and best_score >= 0.35:  # short-name/garbled match threshold
        record_match(gl, best, "S4_fuzzy_name+exact_amount+date", "medium",
                     note=f"fuzzy name similarity={best_score:.2f}")
        matched_bank_idx.add(best.bank_ref)
        matched_gl_refs_s4.add(gl.gl_ref)

ledger_remaining = ledger_remaining[~ledger_remaining.gl_ref.isin(matched_gl_refs_s4)]
bank_remaining = bank_remaining[~bank_remaining.bank_ref.isin(matched_bank_idx)]

# ---------- Duplicate detection among bank rows that matched the SAME gl_ref via strategy 1 pass ----------
# Re-scan all bank rows (including already-matched ones) for extra rows pointing at an already-matched gl_ref
bank_full = pd.read_csv("bank_feed.csv")
bank_full["extracted_ref"] = bank_full["description"].apply(
    lambda d: (GL_REF_RE.search(str(d)).group(0) if GL_REF_RE.search(str(d)) else None)
)
matched_refs_now = {m["gl_ref"] for m in matches}
claimed_bank_refs_now = {m["bank_ref"] for m in matches}

duplicate_rows = []
for _, bk in bank_full.iterrows():
    if bk.bank_ref in claimed_bank_refs_now:
        continue
    if bk.extracted_ref in matched_refs_now:
        gl_row = ledger[ledger.gl_ref == bk.extracted_ref].iloc[0]
        amt_diff = abs(bk.bank_amount - gl_row.gl_amount)
        if amt_diff <= AMOUNT_TOL_EXACT:
            duplicate_rows.append(bk)
            exceptions.append({
                "side": "bank",
                "ref": bk.bank_ref,
                "amount": bk.bank_amount,
                "date": bk.bank_date,
                "counterparty": gl_row.counterparty,
                "category": "possible_duplicate_payment",
                "detail": f"Same amount+ref as already-matched {bk.extracted_ref}; second bank entry for one ledger entry.",
            })
            matched_bank_idx.add(bk.bank_ref)

bank_remaining = bank_remaining[~bank_remaining.bank_ref.isin(matched_bank_idx)]

# ---------- Remaining ledger rows = ledger-only exceptions (awaiting settlement) ----------
for _, gl in ledger_remaining.iterrows():
    exceptions.append({
        "side": "ledger",
        "ref": gl.gl_ref,
        "amount": gl.gl_amount,
        "date": gl.gl_date,
        "counterparty": gl.counterparty,
        "category": "unmatched_ledger_entry",
        "detail": "No bank transaction found within tolerance/date window - likely awaiting settlement or booked in error.",
    })

# ---------- Remaining bank rows = bank-only exceptions (orphans, unmapped fees, garbled beyond fuzzy threshold) ----------
for _, bk in bank_remaining.iterrows():
    # try to guess if it's a bank fee/interest style orphan vs. an unresolved ledger link
    guess = "unmatched_bank_transaction"
    detail = "No ledger entry found within tolerance/date window."
    desc_upper = str(bk.description).upper()
    if any(w in desc_upper for w in ["FEE", "INTEREST", "NSF", "MAINTENANCE"]):
        guess = "bank_only_item_no_gl_counterpart"
        detail = "Description suggests a bank-generated item (fee/interest) never booked to the GL - needs a journal entry, not a match."
    exceptions.append({
        "side": "bank",
        "ref": bk.bank_ref,
        "amount": bk.bank_amount,
        "date": bk.bank_date,
        "counterparty": bk.description,
        "category": guess,
        "detail": detail,
    })

# ---------- Assemble outputs ----------
matches_df = pd.DataFrame(matches)
exceptions_df = pd.DataFrame(exceptions)

total_ledger = len(ledger)
total_bank = len(bank)
total_records = total_ledger + total_bank
n_matched_pairs = len(matches_df)
n_exceptions = len(exceptions_df)

match_rate_ledger = n_matched_pairs / total_ledger
records_resolved = n_matched_pairs * 2 + len(duplicate_rows)  # each match consumes 1 ledger + 1 bank row; dup rows consume 1 bank row
records_resolved_pct = records_resolved / total_records

strategy_counts = matches_df["strategy"].value_counts() if not matches_df.empty else pd.Series(dtype=int)
exception_counts = exceptions_df["category"].value_counts() if not exceptions_df.empty else pd.Series(dtype=int)

matches_df.to_csv("matches.csv", index=False)
exceptions_df.to_csv("exceptions.csv", index=False)

print("=== RECONCILIATION SUMMARY ===")
print(f"Ledger entries:        {total_ledger}")
print(f"Bank feed entries:     {total_bank}")
print(f"Total records in batch:{total_records}")
print(f"Matched pairs:         {n_matched_pairs}")
print(f"Duplicate bank items flagged: {len(duplicate_rows)}")
print(f"Exceptions:            {n_exceptions}")
print(f"Ledger match rate:     {match_rate_ledger:.1%}")
print(f"Record-level resolution: {records_resolved_pct:.1%}")
print()
print("Matches by strategy:")
print(strategy_counts)
print()
print("Exceptions by category:")
print(exception_counts)
