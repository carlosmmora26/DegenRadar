"""
master_list.py — El Libro de Oro / The Book of Gold.

Persistent JSON storage that accumulates wallet data across multiple runs.
Tracks score evolution, re-appearances, and awards consistency bonuses.

Almacenamiento JSON persistente que acumula datos de carteras a través de
múltiples ejecuciones. Rastrea la evolución de puntuaciones, re-apariciones,
y otorga bonificaciones por consistencia.

Consistency Bonus / Bonificación por Consistencia:
  - +3 per re-appearance, capped at +12 (rewards wallets seen multiple times)
  - Only wallets with base score >= 25 receive the bonus
  - This encourages repeated evidence, not just one lucky run
"""
import os
import json
import time
import logging
import pandas as pd
from MemeAlphaCrew_Auto.config import DATA_DIR

logger = logging.getLogger(__name__)

MASTER_FILE = os.path.join(DATA_DIR, "master_wallets.json")
CONSISTENCY_BONUS_PER_SPOT = 3   # +3 per re-appearance (was +5, too generous)
CONSISTENCY_BONUS_CAP = 12       # Max +12 bonus (was +20)
MIN_SCORE_FOR_BONUS = 25         # Only give bonus if base score >= 25 (was 15)


class MasterList:
    """Manages the persistent master wallet database."""

    def __init__(self, filepath: str = MASTER_FILE):
        self.filepath = filepath
        self.wallets: dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                # Convert list format to dict keyed by wallet address
                if isinstance(data, list):
                    self.wallets = {w["wallet"]: w for w in data}
                elif isinstance(data, dict):
                    self.wallets = data
                logger.info(f"📖 Loaded master list: {len(self.wallets)} wallets")
            except Exception as e:
                logger.warning(f"Failed to load master list: {e}")
                self.wallets = {}
        else:
            self.wallets = {}

    def _save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.wallets, f, indent=2)

    def upsert(self, wallet: str, score: float, metrics: dict) -> str:
        """
        Insert or update a wallet in the master list.

        Returns: "new" or "updated"
        """
        now = int(time.time())

        if wallet in self.wallets:
            # ── UPDATE existing wallet ────────────────────────
            entry = self.wallets[wallet]
            entry["last_seen"] = now
            entry["times_spotted"] += 1

            # Track score history
            entry["score_history"].append({
                "timestamp": now,
                "score": score,
                "pnl_sol": metrics.get("pnl_sol", 0),
            })

            # Apply consistency bonus only if wallet meets minimum quality.
            # Bad wallets (low score) don't deserve bonus for just showing up.
            best = max(entry["best_score"], score)
            if best >= MIN_SCORE_FOR_BONUS:
                bonus = min(
                    entry["times_spotted"] * CONSISTENCY_BONUS_PER_SPOT,
                    CONSISTENCY_BONUS_CAP
                )
            else:
                bonus = 0
            entry["alpha_score"] = min(score + bonus, 100)
            entry["consistency_bonus"] = bonus

            # Update best metrics
            entry["best_score"] = max(entry["best_score"], score)
            entry["pnl_sol"] = metrics.get("pnl_sol", entry["pnl_sol"])
            entry["win_rate"] = metrics.get("win_rate", entry["win_rate"])
            entry["total_trades"] = metrics.get("total_trades", entry["total_trades"])
            entry["unique_tokens"] = metrics.get("unique_tokens", entry["unique_tokens"])
            entry["avg_hold_time"] = metrics.get("avg_hold_time", entry.get("avg_hold_time", 0))
            entry["copyability"] = metrics.get("copyability", entry.get("copyability", 0.5))
            entry["verified"] = metrics.get("verified", entry.get("verified", False))

            return "updated"
        else:
            # ── INSERT new wallet ─────────────────────────────
            self.wallets[wallet] = {
                "wallet": wallet,
                "alpha_score": score,
                "best_score": score,
                "consistency_bonus": 0,
                "first_seen": now,
                "last_seen": now,
                "times_spotted": 1,
                "score_history": [{
                    "timestamp": now,
                    "score": score,
                    "pnl_sol": metrics.get("pnl_sol", 0),
                }],
                "pnl_sol": metrics.get("pnl_sol", 0),
                "win_rate": metrics.get("win_rate", 0),
                "total_trades": metrics.get("total_trades", 0),
                "unique_tokens": metrics.get("unique_tokens", 0),
                "consistency": metrics.get("consistency", 0),
                "profitable_trades": metrics.get("profitable_trades", 0),
                "avg_hold_time": metrics.get("avg_hold_time", 0),
                "copyability": metrics.get("copyability", 0.5),
            }
            return "new"

    def bulk_upsert(self, results: list[dict]) -> dict:
        """
        Upserts a list of scored wallet results.
        Returns summary: {"new": N, "updated": N, "total": N}
        """
        new_count = 0
        updated_count = 0

        for r in results:
            status = self.upsert(r["wallet"], r["alpha_score"], r)
            if status == "new":
                new_count += 1
            else:
                updated_count += 1

        self._save()

        summary = {
            "new": new_count,
            "updated": updated_count,
            "total": len(self.wallets),
        }
        logger.info(
            f"📖 Master List: {new_count} new, {updated_count} updated, "
            f"{summary['total']} total wallets"
        )
        return summary

    def get_top(self, n: int = 20) -> list[dict]:
        """Returns top N wallets, prioritizing verified wallets then by alpha_score."""
        sorted_wallets = sorted(
            self.wallets.values(),
            key=lambda w: (w.get("verified", False), w["alpha_score"]),
            reverse=True
        )
        return sorted_wallets[:n]

    def get_all_addresses(self) -> list[str]:
        """Returns all wallet addresses in the master list."""
        return list(self.wallets.keys())

    def export_csv(self, filepath: str = None):
        """Exports the master list to CSV."""
        if filepath is None:
            filepath = os.path.join(DATA_DIR, "master_wallets.csv")

        if not self.wallets:
            logger.warning("Master list is empty, nothing to export.")
            return

        rows = []
        for w in self.wallets.values():
            rows.append({
                "wallet": w["wallet"],
                "alpha_score": w["alpha_score"],
                "best_score": w["best_score"],
                "consistency_bonus": w["consistency_bonus"],
                "times_spotted": w["times_spotted"],
                "pnl_sol": w["pnl_sol"],
                "win_rate": w["win_rate"],
                "total_trades": w["total_trades"],
                "unique_tokens": w["unique_tokens"],
                "avg_hold_time": w.get("avg_hold_time", 0),
                "copyability": w.get("copyability", 0.5),
                "first_seen": w["first_seen"],
                "last_seen": w["last_seen"],
            })

        df = pd.DataFrame(rows)
        df.sort_values("alpha_score", ascending=False, inplace=True)
        df.to_csv(filepath, index=False)
        logger.info(f"💾 Exported master list to: {filepath}")
