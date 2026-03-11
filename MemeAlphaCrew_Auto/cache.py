"""
cache.py — La Memoria / The Memory.

Tracks which tokens and wallets have been recently processed to avoid
wasting RPC calls on repeated work across cycles.

Rastrea qué tokens y carteras han sido procesados recientemente para
evitar desperdiciar llamadas RPC en trabajo repetido entre ciclos.

Three Optimizations / Tres Optimizaciones:
  1. Token Dedup:    Skip tokens harvested within last 24h (~50% savings)
  2. Wallet Enrich:  Skip wallets enriched within last 48h or with 20+ trades
  3. Cycle Stats:    Track outcomes to enable adaptive scheduling intervals

Estimated savings: ~40-60% fewer RPC calls for the same result.
Ahorro estimado: ~40-60% menos llamadas RPC para el mismo resultado.
"""
import os
import json
import time
import logging
from MemeAlphaCrew_Auto.config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(DATA_DIR, "cycle_cache.json")

# ── Cache TTLs ───────────────────────────────────────────────
TOKEN_TTL = 24 * 3600       # Don't re-harvest a token within 24h
WALLET_ENRICH_TTL = 48 * 3600  # Don't re-enrich a wallet within 48h


class CycleCache:
    """
    Remembers recently processed tokens and wallets to avoid
    redundant RPC calls across cycles.
    """

    def __init__(self, filepath: str = CACHE_FILE):
        self.filepath = filepath
        self.data: dict = {"tokens": {}, "enriched_wallets": {}, "cycle_stats": []}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {"tokens": {}, "enriched_wallets": {}, "cycle_stats": []}

    def _save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.data, f, indent=2)

    # ── Token Dedup ──────────────────────────────────────────

    def filter_new_tokens(self, token_mints: list[str]) -> list[str]:
        """
        Remove tokens we've already harvested recently.
        Returns only tokens that need processing.
        """
        now = time.time()
        self._cleanup_expired("tokens", TOKEN_TTL)

        new = []
        skipped = 0
        for mint in token_mints:
            last_seen = self.data["tokens"].get(mint, 0)
            if now - last_seen > TOKEN_TTL:
                new.append(mint)
            else:
                skipped += 1

        if skipped > 0:
            logger.info(
                f"  📦 Cache: {skipped} tokens skipped (already processed), "
                f"{len(new)} new to harvest"
            )
        return new

    def mark_tokens_processed(self, token_mints: list[str]):
        """Mark tokens as recently processed."""
        now = time.time()
        for mint in token_mints:
            self.data["tokens"][mint] = now
        self._save()

    # ── Wallet Enrich Dedup ──────────────────────────────────

    def should_enrich(self, wallet: str, master_trades: int = 0) -> bool:
        """
        Check if a wallet needs enrichment.
        Skip if recently enriched OR already has plenty of trades in master list.
        """
        now = time.time()
        last_enriched = self.data["enriched_wallets"].get(wallet, 0)

        # Recently enriched? Skip.
        if now - last_enriched < WALLET_ENRICH_TTL:
            return False

        # Already well-known in master list? Skip.
        if master_trades >= 20:
            return False

        return True

    def mark_wallet_enriched(self, wallet: str):
        """Mark wallet as recently enriched."""
        self.data["enriched_wallets"][wallet] = time.time()

    def save_enrichment_batch(self, wallets: list[str]):
        """Mark multiple wallets as enriched and save."""
        now = time.time()
        for w in wallets:
            self.data["enriched_wallets"][w] = now
        self._save()

    # ── Cycle Stats (for adaptive intervals) ─────────────────

    def record_cycle(self, new_wallets: int, new_promotions: int):
        """Record cycle outcome for adaptive interval decisions."""
        self.data["cycle_stats"].append({
            "timestamp": time.time(),
            "new_wallets": new_wallets,
            "new_promotions": new_promotions,
        })
        # Keep last 20 cycles
        self.data["cycle_stats"] = self.data["cycle_stats"][-20:]
        self._save()

    def should_skip_cycle(self) -> bool:
        """
        If the last 3 cycles produced 0 new promotions AND 0 new wallets
        with score > 30, suggest extending the interval.
        """
        stats = self.data.get("cycle_stats", [])
        if len(stats) < 3:
            return False

        last_3 = stats[-3:]
        total_new = sum(s.get("new_wallets", 0) for s in last_3)
        total_promos = sum(s.get("new_promotions", 0) for s in last_3)

        if total_new == 0 and total_promos == 0:
            return True
        return False

    # ── Cleanup ──────────────────────────────────────────────

    def _cleanup_expired(self, key: str, ttl: float):
        """Remove expired entries from a cache section."""
        now = time.time()
        section = self.data.get(key, {})
        expired = [k for k, ts in section.items() if now - ts > ttl * 2]
        for k in expired:
            del section[k]
