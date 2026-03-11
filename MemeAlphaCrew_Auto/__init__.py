"""
DegenRadar — Autonomous Smart Wallet Discovery on Solana.

A 7-step pipeline that discovers, scores, and monitors profitable
meme token traders on the Solana blockchain.

Un pipeline de 7 pasos que descubre, califica y monitorea traders
rentables de meme tokens en la blockchain de Solana.

Pipeline:
  1. Discovery  → Find trending tokens (DexScreener, Pump.fun)
  2. Seeds      → Spy on known good wallets for new tokens
  3. Harvest    → Extract wallet addresses from token transactions
  4. Filter     → Remove whales, insiders, and MEV bots
  5. Enrich     → Fetch full trade history for promising wallets
  6. Score      → Rank wallets by Alpha Score (0-100)
  7. Deep Dive  → Extended analysis on top wallets (200 txs)
"""

__version__ = "1.0.0"
