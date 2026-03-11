# DegenRadar — Solana Smart Retail Wallet Discovery Bot

## What This Does
Autonomous bot that discovers, scores, and monitors "Smart Retail" Solana meme token traders. Finds wallets that consistently profit on meme coins, ranks them, and watches their future trades in real-time.

## Two Processes
1. **Scheduler** (`auto_scheduler.py`): Runs the 7-step discovery pipeline every 2-4h (adaptive)
2. **Watcher** (`watcher.py`): Polls top 30 wallets every 2 min for new trades, logs alerts

## Architecture — 7-Step Pipeline

```
1. Discovery (DexScreener + Pump.fun APIs) → token mints
2. Seed Tracking (spy on 6 known wallets) → more mints
3. Harvest (token → wallets via RPC getSignaturesForAddress, with token dedup cache)
4. Filter (anti-whale >25 SOL avg, anti-insider, anti-MEV <2s hold)
5. Enrich (top 50 filtered wallets → fetch 50 txs each from wallet history)
6. Score (weighted: PnL×0.35 + WinRate×0.25 + Copyability×0.25 + Consistency×0.15)
   → Health check validates data before saving
   → Update master list (10,700+ wallets)
   → Promote qualifying wallets to watchlist
7. Deep Dive (wallets with score≥50 and 5+ trades → fetch 200 txs for detailed analysis)
```

## File Map

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~300 | Pipeline orchestrator — runs all 7 steps |
| `auto_scheduler.py` | ~136 | 24/7 loop with adaptive intervals |
| `config.py` | ~108 | All thresholds, weights, AMM program IDs |
| `rpc_client.py` | ~190 | Solana RPC wrapper: multi-node rotation, backoff, pacing |
| `discovery.py` | ? | Token discovery from DexScreener + Pump.fun |
| `seed_tracker.py` | ? | Spy on known wallet addresses for new tokens |
| `harvester.py` | ? | Token → wallet extraction from on-chain tx |
| `filters.py` | ? | Anti-whale, anti-insider, anti-MEV filters |
| `enricher.py` | ~100 | Wallet history enrichment (50 txs per wallet) |
| `scorer.py` | ~200 | Alpha score calculation with confidence multipliers |
| `master_list.py` | ~150 | Persistent wallet DB with consistency bonus |
| `deep_dive.py` | ~200 | Detailed 200-tx analysis for top wallets |
| `watchlist.py` | ~150 | VIP list management (promote/demote/evict) |
| `watcher.py` | ~200 | Real-time trade detection (polls every 2 min) |
| `health.py` | ~100 | Data validation (catches format bugs) |
| `cache.py` | ~150 | Token/wallet dedup to save RPC calls |

## Key Data Contracts

### Wallet dict (internal)
```python
{
    "address": "CbNKh5...",
    "total_trades": 15,
    "unique_tokens": 7,
    "win_rate": 0.65,        # FRACTION 0-1, NEVER percentage
    "consistency": 0.72,     # FRACTION 0-1, NEVER percentage
    "copyability": 0.85,     # FRACTION 0-1
    "total_pnl_sol": 45.2,
    "alpha_score": 62.3,     # 0-100
    "appearances": 3,        # times seen across cycles
}
```

### Scoring Rules
- Confidence multipliers: {1: 0.10, 2: 0.25, 3: 0.45, 4: 0.65, 5: 0.80}, 6-9: 0.90, 10+: 1.0
- Hard caps: <5 trades → max score 35, 1 unique token → max score 45
- Consistency bonus: +3/appearance, cap +12, min base score 25
- Watchlist promotion: score≥40, 5+ trades, 2+ tokens, max 30 wallets

## Known Issues / Areas to Investigate

1. **Deep dive parser**: `_parse_swap(tx_resp, target_mint="")` with empty target_mint may not match swaps correctly — the 63 existing deep dives showed `total_trades: 0` in their summaries
2. **No temporal decay**: Wallets not seen in weeks keep their score forever
3. **Seed wallets may be stale**: The 6 seed wallets haven't been validated recently
4. **Enricher effectiveness unknown**: Just deployed — unclear if it's actually finding more trades
5. **Watcher duplicate alerts**: Same wallet+token can trigger multiple alerts in one cycle
6. **No error recovery in watcher**: If RPC fails mid-cycle, it skips the wallet silently
7. **`get_funding_source`** returns the first non-self account key — may not be the actual funder in complex transactions

## Critical Invariants (DO NOT BREAK)
- `win_rate` and `consistency` are stored as **fractions (0.0-1.0)**, NEVER as percentages (0-100)
- `scorer.py` has asserts to enforce this — do not remove them
- `health.py` aborts the entire pipeline if corrupted data is detected — do not weaken this
- API keys are in `.env` (gitignored) and loaded via `dotenv`

## Tech Stack
- Python 3.11+
- `solana-py` + `solders` for RPC
- `httpx` for HTTP (DexScreener, Pump.fun)
- `tqdm` for progress bars
- No database — JSON files in `data/`

## How to Run
```bash
# Set up env
cp MemeAlphaCrew_Auto/.env.example MemeAlphaCrew_Auto/.env  # add your RPC keys

# Discovery pipeline (one cycle)
python3 -m MemeAlphaCrew_Auto.main --seeds --deep-dive --momentum --top 20

# Scheduler (24/7)
python3 -m MemeAlphaCrew_Auto.auto_scheduler

# Watcher (24/7, separate process)
python3 -m MemeAlphaCrew_Auto.watcher

# Watcher (one cycle only)
python3 -m MemeAlphaCrew_Auto.watcher --once
```

## What I Want From a Code Review
1. **Bugs**: Logic errors, race conditions, data corruption risks
2. **Robustness**: Error handling gaps, silent failures, edge cases
3. **Performance**: Unnecessary RPC calls, O(n²) loops, memory leaks
4. **Architecture**: Coupling issues, missing abstractions, testability
5. **Solana-specific**: Incorrect tx parsing, missed swap types, program ID gaps
