"""
Generates two synthetic finance-ops sources that a real controller would need to
reconcile: a bank feed export and an internal GL/ledger export. Deliberately
injects the mess real data has: settlement delays, FX/fee drift, missing
references, duplicates, and orphans on both sides.
"""
import random
import pandas as pd
from datetime import datetime, timedelta

random.seed(42)

COUNTERPARTIES = [
    "Acme Supply Co", "Globex Manufacturing", "Initech Consulting", "Umbrella Logistics",
    "Stark Materials", "Wayne Freight", "Wonka Distribution", "Hooli Cloud Services",
    "Soylent Foods Inc", "Massive Dynamic", "Cyberdyne Systems", "Oscorp Chemicals",
    "Tyrell Components", "Aperture Hardware", "Gringotts Treasury Services",
]

BASE_DATE = datetime(2026, 7, 1)
N_LEDGER = 55

ledger_rows = []
for i in range(1, N_LEDGER + 1):
    ref = f"GL{2026070000 + i}"
    date = BASE_DATE + timedelta(days=random.randint(0, 45))
    counterparty = random.choice(COUNTERPARTIES)
    amount = round(random.uniform(150, 48000) * random.choice([1, 1, 1, -1]), 2)  # mostly receipts, some payments
    direction = "credit" if amount > 0 else "debit"
    ledger_rows.append({
        "gl_ref": ref,
        "gl_date": date.strftime("%Y-%m-%d"),
        "counterparty": counterparty,
        "gl_amount": amount,
        "direction": direction,
        "memo": f"Invoice settlement {ref} - {counterparty}",
    })

ledger_df = pd.DataFrame(ledger_rows)

# --- Build bank feed from ledger, with deliberate distortions ---
bank_rows = []
bank_ref_counter = 90000

def make_bank_ref():
    global bank_ref_counter
    bank_ref_counter += 1
    return f"BK{bank_ref_counter}"

idx = list(ledger_df.index)
random.shuffle(idx)

n = len(idx)
clean_n = int(n * 0.60)        # exact matches
amount_mismatch_n = int(n * 0.10)   # fee/FX drift
timing_delay_n = int(n * 0.10)      # settles days later, no ref in memo
missing_from_bank_n = int(n * 0.08) # not yet settled -> ledger-only exception
fuzzy_desc_n = n - clean_n - amount_mismatch_n - timing_delay_n - missing_from_bank_n  # no clean ref string, needs fuzzy match

groups = {
    "clean": idx[:clean_n],
    "amount_mismatch": idx[clean_n:clean_n+amount_mismatch_n],
    "timing_delay": idx[clean_n+amount_mismatch_n:clean_n+amount_mismatch_n+timing_delay_n],
    "missing_from_bank": idx[clean_n+amount_mismatch_n+timing_delay_n:clean_n+amount_mismatch_n+timing_delay_n+missing_from_bank_n],
    "fuzzy_desc": idx[clean_n+amount_mismatch_n+timing_delay_n+missing_from_bank_n:],
}

for i in groups["clean"]:
    row = ledger_df.loc[i]
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": row.gl_date,
        "bank_amount": row.gl_amount,
        "description": f"PMT REF {row.gl_ref} {row.counterparty.upper()}",
        "_source_gl_ref": row.gl_ref,
        "_scenario": "clean",
    })

for i in groups["amount_mismatch"]:
    row = ledger_df.loc[i]
    fee = round(abs(row.gl_amount) * random.uniform(0.005, 0.02), 2)  # bank fee / FX spread
    drifted = round(row.gl_amount - fee if row.gl_amount > 0 else row.gl_amount + fee, 2)
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": row.gl_date,
        "bank_amount": drifted,
        "description": f"PMT REF {row.gl_ref} {row.counterparty.upper()} LESS FEE",
        "_source_gl_ref": row.gl_ref,
        "_scenario": "amount_mismatch",
    })

for i in groups["timing_delay"]:
    row = ledger_df.loc[i]
    delay = random.randint(2, 6)
    bdate = (datetime.strptime(row.gl_date, "%Y-%m-%d") + timedelta(days=delay)).strftime("%Y-%m-%d")
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": bdate,
        "bank_amount": row.gl_amount,
        "description": f"TRANSFER {row.counterparty.upper()} SETTLEMENT",  # no gl_ref in text
        "_source_gl_ref": row.gl_ref,
        "_scenario": "timing_delay",
    })

# missing_from_bank: intentionally produce nothing in bank feed (still awaiting settlement)

for i in groups["fuzzy_desc"]:
    row = ledger_df.loc[i]
    # bank shortens/garbles counterparty name, no ref code at all
    short_name = row.counterparty.split()[0].upper()
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": row.gl_date,
        "bank_amount": row.gl_amount,
        "description": f"ACH {short_name} XFER",
        "_source_gl_ref": row.gl_ref,
        "_scenario": "fuzzy_desc",
    })

# --- Orphan bank transactions with no ledger counterpart at all (bank fees, interest, misc) ---
ORPHAN_DESCS = [
    ("Monthly account maintenance fee", -45.00),
    ("Wire transfer fee", -35.00),
    ("Interest earned - operating account", 112.34),
    ("NSF fee - returned item", -50.00),
    ("Card processing fee batch", -218.60),
]
for desc, amt in ORPHAN_DESCS:
    d = BASE_DATE + timedelta(days=random.randint(0, 45))
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": d.strftime("%Y-%m-%d"),
        "bank_amount": amt,
        "description": desc,
        "_source_gl_ref": None,
        "_scenario": "bank_orphan",
    })

# --- Duplicate bank entries (double-submitted payment file) ---
dup_sources = random.sample(groups["clean"], 2)
for i in dup_sources:
    row = ledger_df.loc[i]
    bank_rows.append({
        "bank_ref": make_bank_ref(),
        "bank_date": row.gl_date,
        "bank_amount": row.gl_amount,
        "description": f"PMT REF {row.gl_ref} {row.counterparty.upper()} DUPLICATE SUBMISSION",
        "_source_gl_ref": row.gl_ref,
        "_scenario": "duplicate",
    })

bank_df = pd.DataFrame(bank_rows).drop(columns=["_source_gl_ref", "_scenario"]).sample(frac=1, random_state=7).reset_index(drop=True)
bank_df_full = pd.DataFrame(bank_rows)  # keep hidden truth for accuracy scoring later

ledger_df.to_csv("ledger.csv", index=False)
bank_df.to_csv("bank_feed.csv", index=False)
bank_df_full.to_csv("bank_feed_groundtruth.csv", index=False)  # not shown to the agent

print(f"Ledger entries: {len(ledger_df)}")
print(f"Bank feed entries: {len(bank_df)}")
print("Scenario breakdown (ground truth, hidden from agent):")
print(bank_df_full["_scenario"].value_counts())
print(f"Ledger entries with no bank counterpart at all (missing_from_bank): {missing_from_bank_n}")
