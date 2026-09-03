"""
Verifies the reconciliation agent's output against the hidden ground-truth
mapping created at data-generation time. Run this AFTER reconcile_agent.py.

This is the "don't just trust the self-reported match rate" check - it proves
precision/recall against a known-correct answer key rather than the agent's
own accounting of itself.
"""
import pandas as pd

gt = pd.read_csv("bank_feed_groundtruth.csv")
matches = pd.read_csv("matches.csv")
exceptions = pd.read_csv("exceptions.csv")
ledger = pd.read_csv("ledger.csv")

gt_map = dict(zip(gt.bank_ref, gt._source_gl_ref))
gt_scenario = dict(zip(gt.bank_ref, gt._scenario))

# --- Precision: of the matches made, how many pair the correct bank_ref with correct gl_ref? ---
correct = sum(1 for _, m in matches.iterrows() if gt_map.get(m.bank_ref) == m.gl_ref)
precision = correct / len(matches)

# --- Recall: of all bank rows that truly had a matchable ledger counterpart, how many were found? ---
matchable_bank_refs = set(gt[gt._scenario.isin(["clean", "amount_mismatch", "timing_delay", "fuzzy_desc"])].bank_ref)
found_bank_refs = set(matches.bank_ref)
recall = len(matchable_bank_refs & found_bank_refs) / len(matchable_bank_refs)
missed = matchable_bank_refs - found_bank_refs

# --- Exception sanity: were the ledger entries with NO bank row at all correctly flagged? ---
true_orphan_ledger = set(ledger.gl_ref) - set(gt.dropna(subset=["_source_gl_ref"])._source_gl_ref)
flagged_ledger_exceptions = set(exceptions[exceptions.category == "unmatched_ledger_entry"].ref)

print(f"Precision (matches that are actually correct): {correct}/{len(matches)} = {precision:.1%}")
print(f"Recall (matchable bank rows actually found):    {len(matchable_bank_refs & found_bank_refs)}/{len(matchable_bank_refs)} = {recall:.1%}")
print(f"Missed matchable bank rows: {sorted(missed) if missed else 'none'}")
print(f"Ledger exceptions correctly identified as unmatchable: {flagged_ledger_exceptions == true_orphan_ledger}")
