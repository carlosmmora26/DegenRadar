"""
health.py — El Doctor / The Doctor.

Validates data integrity after each pipeline cycle.
Catches bugs like the win_rate percentage fiasco before they corrupt days of data.

Valida la integridad de datos después de cada ciclo del pipeline.
Atrapa bugs como el fiasco del win_rate como porcentaje antes de que
corrompan días de datos.

WHAT IT VALIDATES / QUÉ VALIDA:
  - win_rate must be 0-1 (fraction), NOT 0-100 (percentage)
  - consistency must be 0-1
  - alpha_score must be 0-100
  - Average score not suspiciously high (> 60 = red flag)
  - Not all scores identical (scoring engine broken)
  - At least some wallets have 5+ trades (enricher working)

If validation fails → pipeline ABORTS and skips saving to prevent damage.
Si la validación falla → pipeline ABORTA y omite guardar para prevenir daño.
"""
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Expected Ranges ──────────────────────────────────────────
VALID_RANGES = {
    "win_rate":     (0.0, 1.0),    # Fraction, NOT percentage
    "consistency":  (0.0, 1.0),    # Fraction, NOT percentage
    "copyability":  (0.0, 1.0),
    "alpha_score":  (0.0, 100.0),
    "pnl_sol":      (-1000.0, 10000.0),  # Generous bounds
    "total_trades": (0, 100000),
    "unique_tokens": (0, 10000),
}


class HealthCheck:
    """Runs validation checks on pipeline output."""

    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict = {}

    def validate_results(self, results: list[dict]) -> bool:
        """
        Validate scored results before they go to master list.
        Returns True if healthy, False if critical errors found.
        """
        self.errors = []
        self.warnings = []

        if not results:
            self.warnings.append("No results to validate")
            return True

        self.stats["total_results"] = len(results)

        # ── Check each result for valid ranges ───────────────
        bad_win_rate = 0
        bad_consistency = 0
        bad_score = 0

        for r in results:
            wallet = r.get("wallet", "???")[:12]

            for field, (lo, hi) in VALID_RANGES.items():
                val = r.get(field)
                if val is None:
                    continue
                if not (lo <= val <= hi):
                    self.errors.append(
                        f"{wallet}: {field}={val} outside [{lo}, {hi}]"
                    )
                    if field == "win_rate":
                        bad_win_rate += 1
                    elif field == "consistency":
                        bad_consistency += 1
                    elif field == "alpha_score":
                        bad_score += 1

        # ── Aggregated checks ────────────────────────────────
        scores = [r["alpha_score"] for r in results]
        avg_score = sum(scores) / len(scores)
        max_score = max(scores)
        min_score = min(scores)

        self.stats["avg_score"] = round(avg_score, 1)
        self.stats["max_score"] = round(max_score, 1)
        self.stats["scores_above_50"] = len([s for s in scores if s >= 50])

        # Suspicious: if average score is very high, something is wrong
        if avg_score > 60:
            self.warnings.append(
                f"Average score {avg_score:.1f} is suspiciously high — "
                f"check scoring logic"
            )

        # Suspicious: all scores identical
        if max_score == min_score and len(results) > 10:
            self.errors.append(
                f"All {len(results)} scores are identical ({max_score}) — "
                f"scoring is broken"
            )

        # Data format corruption (the win_rate bug)
        if bad_win_rate > 0:
            self.errors.append(
                f"{bad_win_rate} wallets have win_rate > 1.0 — "
                f"stored as percentage instead of fraction!"
            )
        if bad_consistency > 0:
            self.errors.append(
                f"{bad_consistency} wallets have consistency > 1.0 — "
                f"stored as percentage instead of fraction!"
            )

        # Enrichment check: how many wallets have enough trades
        trades_counts = [r.get("total_trades", 0) for r in results]
        with_5_plus = len([t for t in trades_counts if t >= 5])
        self.stats["wallets_5plus_trades"] = with_5_plus

        if with_5_plus == 0 and len(results) > 20:
            self.warnings.append(
                "No wallets have 5+ trades — enricher may not be working"
            )

        # ── Report ───────────────────────────────────────────
        has_errors = len(self.errors) > 0
        self._log_report()
        return not has_errors

    def validate_master_list(self, wallets: dict) -> bool:
        """
        Validate the master list data integrity.
        Run after bulk_upsert to catch corruption.
        """
        bad = 0
        for addr, info in wallets.items():
            wr = info.get("win_rate", 0)
            if wr > 1.0:
                bad += 1

        if bad > 0:
            self.errors.append(
                f"MASTER LIST CORRUPTION: {bad}/{len(wallets)} wallets "
                f"have win_rate > 1.0"
            )
            self._log_report()
            return False

        return True

    def _log_report(self):
        """Log the health check report."""
        if self.errors:
            logger.error("🚨 HEALTH CHECK FAILED")
            for e in self.errors:
                logger.error(f"  ❌ {e}")
        else:
            logger.info("✅ Health check passed")

        for w in self.warnings:
            logger.warning(f"  ⚠️  {w}")

        if self.stats:
            stats_str = " | ".join(f"{k}: {v}" for k, v in self.stats.items())
            logger.info(f"  📊 {stats_str}")

    def get_summary(self) -> dict:
        """Returns a summary dict for the cycle report."""
        return {
            "timestamp": datetime.now().isoformat(),
            "healthy": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }
