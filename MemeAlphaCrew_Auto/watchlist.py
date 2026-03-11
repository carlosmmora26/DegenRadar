"""
watchlist.py — La Lista VIP / The VIP List.

Manages a persistent list of top wallets to monitor in real-time.
Wallets get promoted from the master_list when their score qualifies.
The watcher process uses this list to detect new trades.

Gestiona una lista persistente de las mejores carteras para monitoreo
en tiempo real. Las carteras son promovidas desde la master_list cuando
su puntuación califica. El proceso watcher usa esta lista para detectar
nuevos trades.

Promotion Criteria / Criterios de Promoción:
  - Alpha Score >= 40
  - 5+ trades observed
  - 2+ tokens traded (not a one-hit wonder)
  - Max 30 wallets simultaneously (weakest gets evicted if full)
  - Inactive wallets (14+ days) are automatically demoted
"""
import os
import json
import time
import logging
from MemeAlphaCrew_Auto.config import DATA_DIR

logger = logging.getLogger(__name__)

WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

# ── Watchlist Settings ───────────────────────────────────────
MAX_WATCHED = 30            # Max wallets to watch simultaneously
MIN_SCORE_TO_PROMOTE = 40   # Minimum alpha_score to enter watchlist
MIN_TRADES_TO_PROMOTE = 5   # Must have 5+ trades to be trustworthy
MIN_TOKENS_TO_PROMOTE = 2   # Must have traded 2+ tokens (not a one-hit wonder)
INACTIVE_DAYS = 14          # Demote if not seen in this many days


class Watchlist:
    """Manages the persistent watchlist of wallets to monitor."""

    def __init__(self, filepath: str = WATCHLIST_FILE):
        self.filepath = filepath
        self.wallets: dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self.wallets = json.load(f)
                logger.info(f"👁️ Watchlist loaded: {len(self.wallets)} wallets")
            except Exception as e:
                logger.warning(f"Failed to load watchlist: {e}")
                self.wallets = {}
        else:
            self.wallets = {}

    def _save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.wallets, f, indent=2)

    def promote(self, wallet: str, score: float, metrics: dict) -> bool:
        """
        Add or update a wallet in the watchlist if it qualifies.

        Returns True if wallet was added/updated, False if rejected.
        """
        trades = metrics.get("total_trades", 0)
        tokens = metrics.get("unique_tokens", 0)

        if score < MIN_SCORE_TO_PROMOTE:
            return False
        if trades < MIN_TRADES_TO_PROMOTE:
            return False
        if tokens < MIN_TOKENS_TO_PROMOTE:
            return False

        now = int(time.time())

        if wallet in self.wallets:
            # Update existing
            entry = self.wallets[wallet]
            entry["alpha_score"] = score
            entry["last_updated"] = now
            entry["pnl_sol"] = metrics.get("pnl_sol", entry.get("pnl_sol", 0))
            entry["win_rate"] = metrics.get("win_rate", entry.get("win_rate", 0))
            entry["total_trades"] = trades
            entry["unique_tokens"] = tokens
            return True
        else:
            # Check if we have room
            if len(self.wallets) >= MAX_WATCHED:
                # Evict the lowest-scoring wallet
                if not self._evict_weakest(score):
                    return False  # All current wallets are better

            self.wallets[wallet] = {
                "wallet": wallet,
                "alpha_score": score,
                "promoted_at": now,
                "last_updated": now,
                "last_tx_sig": None,       # Watcher fills this in
                "last_alert_at": None,     # When we last alerted on this wallet
                "pnl_sol": metrics.get("pnl_sol", 0),
                "win_rate": metrics.get("win_rate", 0),
                "total_trades": trades,
                "unique_tokens": tokens,
                "alerts": [],              # History of detected trades
            }
            logger.info(
                f"⭐ Promoted to watchlist: {wallet[:12]}... "
                f"(score: {score:.1f}, trades: {trades})"
            )
            return True

    def _evict_weakest(self, new_score: float) -> bool:
        """
        Remove the lowest-scoring wallet if the new one is better.
        Returns True if eviction happened, False otherwise.
        """
        if not self.wallets:
            return True

        weakest_addr = min(
            self.wallets, key=lambda w: self.wallets[w]["alpha_score"]
        )
        weakest_score = self.wallets[weakest_addr]["alpha_score"]

        if new_score > weakest_score:
            logger.info(
                f"   Evicted {weakest_addr[:12]}... (score: {weakest_score:.1f}) "
                f"for new wallet (score: {new_score:.1f})"
            )
            del self.wallets[weakest_addr]
            return True
        return False

    def demote_inactive(self):
        """Remove wallets that haven't been seen recently."""
        now = int(time.time())
        cutoff = now - (INACTIVE_DAYS * 86400)
        to_remove = [
            addr for addr, info in self.wallets.items()
            if info.get("last_updated", 0) < cutoff
        ]
        for addr in to_remove:
            logger.info(f"   Demoted inactive: {addr[:12]}...")
            del self.wallets[addr]

        if to_remove:
            logger.info(f"👁️ Demoted {len(to_remove)} inactive wallets")

    def bulk_promote(self, master_wallets: dict) -> int:
        """
        Scan the master list and promote qualifying wallets.
        Returns number of wallets promoted.
        """
        promoted = 0
        candidates = []

        for addr, info in master_wallets.items():
            score = info.get("alpha_score", 0)
            trades = info.get("total_trades", 0)
            tokens = info.get("unique_tokens", 0)

            if (score >= MIN_SCORE_TO_PROMOTE
                    and trades >= MIN_TRADES_TO_PROMOTE
                    and tokens >= MIN_TOKENS_TO_PROMOTE):
                candidates.append((addr, score, info))

        # Sort by score descending, take top MAX_WATCHED
        candidates.sort(key=lambda x: x[1], reverse=True)

        for addr, score, info in candidates[:MAX_WATCHED]:
            if self.promote(addr, score, info):
                promoted += 1

        # Clean up stale entries
        self.demote_inactive()

        self._save()
        logger.info(
            f"👁️ Watchlist: {promoted} promoted, "
            f"{len(self.wallets)} total watched"
        )
        return promoted

    def get_all(self) -> list[dict]:
        """Returns all watched wallets sorted by score."""
        return sorted(
            self.wallets.values(),
            key=lambda w: w["alpha_score"],
            reverse=True
        )

    def update_last_sig(self, wallet: str, signature: str, save: bool = True):
        """Update the last known transaction signature for a wallet."""
        if wallet in self.wallets:
            self.wallets[wallet]["last_tx_sig"] = signature
            if save:
                self._save()

    def record_alert(self, wallet: str, alert: dict, save: bool = True):
        """Record a trade alert for a wallet."""
        if wallet in self.wallets:
            entry = self.wallets[wallet]
            entry["last_alert_at"] = int(time.time())
            # Keep last 50 alerts max
            entry["alerts"].append(alert)
            entry["alerts"] = entry["alerts"][-50:]
            if save:
                self._save()
