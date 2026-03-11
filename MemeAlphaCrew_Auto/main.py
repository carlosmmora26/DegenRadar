"""
main.py — El Orquestador / The Orchestrator.
Pipeline autónomo: Descubrimiento → Cosecha → Filtro → Puntuación → Lista Maestra → Análisis Profundo.
Autonomous pipeline: Discover → Harvest → Filter → Score → Master List → Deep Dive.
"""
import sys
import argparse
import logging
import pandas as pd
from tqdm import tqdm

# Importaciones locales / Local imports
from MemeAlphaCrew_Auto.config import OUTPUT_CSV, DATA_DIR
from MemeAlphaCrew_Auto.discovery import discover_survivor_tokens, discover_momentum_tokens
from MemeAlphaCrew_Auto.rpc_client import SolanaRPCClient
from MemeAlphaCrew_Auto.harvester import harvest_wallets
from MemeAlphaCrew_Auto.filters import run_all_filters
from MemeAlphaCrew_Auto.scorer import calculate_metrics, calculate_alpha_score
from MemeAlphaCrew_Auto.master_list import MasterList
from MemeAlphaCrew_Auto.seed_tracker import track_seed_wallets
from MemeAlphaCrew_Auto.deep_dive import run_deep_dives, print_deep_dive_report
from MemeAlphaCrew_Auto.enricher import enrich_wallets
from MemeAlphaCrew_Auto.watchlist import Watchlist
from MemeAlphaCrew_Auto.health import HealthCheck
from MemeAlphaCrew_Auto.cache import CycleCache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s │ %(levelname)-7s │ %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="MemeAlphaCrew Auto — Autonomous Smart Wallet Discovery"
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Number of top wallets to save (default: 20)"
    )
    parser.add_argument(
        "--seeds", action="store_true",
        help="Enable seed wallet tracking for additional token discovery"
    )
    parser.add_argument(
        "--deep-dive", action="store_true",
        help="Run deep dive analysis on top scoring wallets"
    )
    parser.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip DexScreener discovery (use only seed wallets)"
    )
    parser.add_argument(
        "--momentum", action="store_true",
        help="Enable Momentum Discovery (1h-24h tokens with high volume)"
    )
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   DegenRadar — Smart Wallet Discovery    ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    rpc_client = SolanaRPCClient()
    cache = CycleCache()
    token_mints = []

    # ── Step 1: Discovery ─────────────────────────────────────
    logger.info("━━━ STEP 1/7: DISCOVERY ━━━")

    if not args.skip_discovery:
        # Default survivor discovery
        logger.info("  🔎 Running Survivor Discovery (1d-21d)...")
        tokens = discover_survivor_tokens()
        if tokens:
            token_mints.extend([t["mint"] for t in tokens])
        
        # Momentum discovery if requested
        if args.momentum:
            logger.info("  🚀 Running Momentum Discovery (1h-24h)...")
            m_tokens = discover_momentum_tokens()
            if m_tokens:
                token_mints.extend([t["mint"] for t in m_tokens])
    else:
        logger.info("Skipping DexScreener discovery (--skip-discovery)")

    # ── Step 2: Seed Wallet Tracking ──────────────────────────
    if args.seeds:
        logger.info("━━━ STEP 2/7: SEED WALLET TRACKING ━━━")
        seed_mints = track_seed_wallets(rpc_client)
        if seed_mints:
            # Add seed-discovered mints (deduped)
            existing = set(token_mints)
            new_from_seeds = [m for m in seed_mints if m not in existing]
            token_mints.extend(new_from_seeds)
            logger.info(
                f"🌱 Seeds added {len(new_from_seeds)} new token mints "
                f"(total: {len(token_mints)})"
            )
    else:
        logger.info("━━━ STEP 2/7: SEED TRACKING (skipped — use --seeds) ━━━")

    if not token_mints:
        logger.warning("No token mints to analyze. Exiting.")
        print("\n💡 Tips:")
        print("  • Try --seeds with wallets in data/seed_wallets.txt")
        print("  • Market conditions may not match Survivor criteria right now")
        sys.exit(0)

    # Filter out recently processed tokens to save RPC calls
    original_count = len(token_mints)
    token_mints = cache.filter_new_tokens(token_mints)

    if not token_mints:
        logger.info(
            f"All {original_count} tokens were recently processed. "
            f"Nothing new to harvest."
        )
        sys.exit(0)

    # ── Step 3: Harvest ───────────────────────────────────────
    logger.info("━━━ STEP 3/7: HARVESTING WALLETS ━━━")

    wallet_trades = harvest_wallets(token_mints, rpc_client)

    # Mark tokens as processed (even if harvest found nothing)
    cache.mark_tokens_processed(token_mints)

    if not wallet_trades:
        logger.warning("No wallets found from harvesting. Exiting.")
        sys.exit(0)

    logger.info(f"Collected {len(wallet_trades)} unique wallets.")

    # ── Step 4: Filter ────────────────────────────────────────
    logger.info("━━━ STEP 4/7: APPLYING FILTERS ━━━")

    passed_wallets = {}
    filtered_count = 0
    rejection_reasons = {}  # Track which filter rejects most

    for wallet_addr, trades in tqdm(wallet_trades.items(),
                                     desc="  Filtering", ncols=80):
        trades_df = pd.DataFrame(trades)
        is_filtered, reason = run_all_filters(trades_df)

        if is_filtered:
            filtered_count += 1
            # Categorize rejection
            if "whale" in (reason or ""):
                rejection_reasons["whale"] = rejection_reasons.get("whale", 0) + 1
            elif "insider" in (reason or ""):
                rejection_reasons["insider"] = rejection_reasons.get("insider", 0) + 1
            elif "uncopyable" in (reason or ""):
                rejection_reasons["uncopyable"] = rejection_reasons.get("uncopyable", 0) + 1
            else:
                rejection_reasons["other"] = rejection_reasons.get("other", 0) + 1
            logger.debug(f"  REJECTED {wallet_addr[:12]}... → {reason}")
        else:
            passed_wallets[wallet_addr] = trades

    logger.info(
        f"✅ Filters applied: {len(passed_wallets)} passed, "
        f"{filtered_count} rejected"
    )
    if rejection_reasons:
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(rejection_reasons.items(), key=lambda x: -x[1]))
        logger.info(f"   Rejection breakdown: {breakdown}")

    if not passed_wallets:
        logger.warning("All wallets were filtered out. No results.")
        sys.exit(0)

    # ── Step 5: Enrich ─────────────────────────────────────────
    # Fetch full trade history for promising wallets so we score on
    # 30-50 trades instead of just the 1-2 from the token scan.
    logger.info("━━━ STEP 5/7: ENRICHING WALLET HISTORIES ━━━")
    # Load master list once — reused for enricher cache and later for upserting
    master = MasterList()
    enriched_wallets = enrich_wallets(
        passed_wallets, rpc_client,
        cache=cache,
        master_wallets=master.wallets,
    )

    # ── Step 6: Score ─────────────────────────────────────────
    logger.info("━━━ STEP 6/7: SCORING CANDIDATES ━━━")

    results = []

    for wallet_addr, trades_df in tqdm(enriched_wallets.items(),
                                        desc="  Scoring", ncols=80):
        if trades_df.empty:
            continue
        metrics = calculate_metrics(trades_df)
        alpha_score = calculate_alpha_score(metrics)

        results.append({
            "wallet": wallet_addr,
            "alpha_score": alpha_score,
            "pnl_sol": metrics["pnl_sol"],
            "win_rate": metrics["win_rate"],           # 0.0-1.0 fraction (NOT percentage)
            "consistency": metrics["consistency"],      # 0.0-1.0 fraction (NOT percentage)
            "total_trades": metrics["total_trades"],
            "unique_tokens": metrics["unique_tokens"],
            "profitable_trades": metrics["profitable_trades"],
            "avg_hold_time": metrics["avg_hold_time"],
            "copyability": metrics["copyability"],
        })

    # Sort by alpha score descending
    results.sort(key=lambda x: x["alpha_score"], reverse=True)

    # ── Health Check: validate before saving ─────────────────
    health = HealthCheck()
    if not health.validate_results(results):
        logger.error(
            "🚨 HEALTH CHECK FAILED — results have data corruption. "
            "Skipping master list update to prevent damage. "
            "Check logs and fix the issue."
        )
        print("\n🚨 ABORTING: Data failed health check. See errors above.")
        sys.exit(1)

    # ── Step 6b: Master List ──────────────────────────────────
    logger.info("━━━ Updating Master List ━━━")
    master._load()  # Reload to pick up any changes since enrichment
    summary = master.bulk_upsert(results)
    master.export_csv()

    # Validate master list after update
    health.validate_master_list(master.wallets)

    # ── Step 6c: Promote to Watchlist ────────────────────────
    logger.info("━━━ Updating Watchlist ━━━")
    watchlist = Watchlist()
    promotions = watchlist.bulk_promote(master.wallets)

    # Use master list for final display (includes consistency bonuses)
    top_results = master.get_top(args.top)

    # Also save the simple CSV (convert fractions to % for readability)
    csv_results = []
    for r in results[:args.top]:
        row = dict(r)
        row["win_rate"] = round(r["win_rate"] * 100, 1)
        row["consistency"] = round(r["consistency"] * 100, 1)
        csv_results.append(row)
    results_df = pd.DataFrame(csv_results)
    results_df.to_csv(OUTPUT_CSV, index=False)

    # ── Display Results ───────────────────────────────────────
    print()
    print("┌──────────────────────────────────────────────────────────────────────────────────────┐")
    print("│                          🏆 TOP COPYABLE ALPHA WALLETS                              │")
    print("├────┬────────────────────┬───────┬────────┬───────┬──────────┬──────┬─────────────────┤")
    print("│ #  │ Wallet             │ Score │ PnL    │ WinR% │ CopyScr  │ Seen │ Avg Hold        │")
    print("├────┼────────────────────┼───────┼────────┼───────┼──────────┼──────┼─────────────────┤")

    for i, r in enumerate(top_results, 1):
        wallet_short = r["wallet"][:18] + "..."
        seen = r.get("times_spotted", 1)
        pnl = r.get("pnl_sol", 0)
        wr = r.get("win_rate", 0)
        wr_display = wr * 100 if wr <= 1.0 else wr  # Handle legacy % values
        copy_score = r.get("copyability", 0.5)
        hold_time = r.get("avg_hold_time", 0)

        # Format hold time human-readable
        if hold_time >= 3600:
            hold_str = f"{hold_time/3600:.1f}h"
        elif hold_time >= 60:
            hold_str = f"{hold_time/60:.1f}m"
        elif hold_time > 0:
            hold_str = f"{hold_time:.0f}s"
        else:
            hold_str = "n/a"

        # Copyability label
        if copy_score >= 0.7:
            copy_label = f"{copy_score:.1f} EASY"
        elif copy_score >= 0.3:
            copy_label = f"{copy_score:.1f} MED"
        elif copy_score > 0:
            copy_label = f"{copy_score:.1f} HARD"
        else:
            copy_label = f"{copy_score:.1f} MEV"

        print(
            f"│ {i:>2} │ {wallet_short:<18} │ {r['alpha_score']:>5.1f} │ "
            f"{pnl:>+6.2f} │ {wr_display:>4.0f}% │ {copy_label:<8} │ {seen:>4} │ {hold_str:>15} │"
        )
        if i >= args.top:
            break

    print("└────┴────────────────────┴───────┴────────┴───────┴──────────┴──────┴─────────────────┘")
    print()
    print(f"📊 Results:  {OUTPUT_CSV}")
    print(f"📖 Master:   {master.filepath}  ({summary['total']} total wallets)")
    print(f"    └─ {summary['new']} new, {summary['updated']} updated this run")
    print()

    # ── Step 7: Deep Dive ─────────────────────────────────────
    if args.deep_dive:
        logger.info("━━━ STEP 7/7: DEEP DIVE ANALYSIS ━━━")
        reports = run_deep_dives(results, rpc_client)
        for report in reports:
            print_deep_dive_report(report)

        # Retroaliment master list with verified scores
        if reports:
            master._load()  # Refresh before updating
            for report in reports:
                if "verified_alpha_score" in report and "verified_metrics" in report:
                    wallet = report["wallet"]
                    master.upsert(wallet, report["verified_alpha_score"], report["verified_metrics"])
            master._save()
            logger.info(f"Master list updated with {len(reports)} verified scores")
    else:
        logger.info("━━━ STEP 7/7: DEEP DIVE (skipped — use --deep-dive) ━━━")

    # ── Record cycle stats for adaptive intervals ──────────
    new_wallets_found = summary.get("new", 0)
    cache.record_cycle(
        new_wallets=new_wallets_found,
        new_promotions=promotions,
    )

    print("✨ Done!")


if __name__ == "__main__":
    main()
