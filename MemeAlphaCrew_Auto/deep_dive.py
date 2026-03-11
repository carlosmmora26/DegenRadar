"""
deep_dive.py — El Microscopio / The Microscope.

Performs extended analysis on a wallet by fetching its last 200 transactions
and building a comprehensive trading profile with verified metrics.

Realiza un análisis extendido de una cartera obteniendo sus últimas 200
transacciones y construyendo un perfil de trading completo con métricas verificadas.

Unlike the enricher (50 txs), deep dive uses 200 txs and generates a full report
including: funding source, per-token PnL breakdown, best/worst tokens, and a
verified alpha score that feeds back into the master list.

A diferencia del enricher (50 txs), deep dive usa 200 txs y genera un reporte
completo incluyendo: fuente de financiamiento, desglose PnL por token,
mejores/peores tokens, y un alpha score verificado.
"""
import json
import os
import time
import logging
import pandas as pd
from tqdm import tqdm
from MemeAlphaCrew_Auto.config import (
    DATA_DIR,
    RAYDIUM_V4_PROGRAM_ID,
    SOL_MINT,
    RPC_PACING_SECONDS,
)
from MemeAlphaCrew_Auto.harvester import _parse_swap
from MemeAlphaCrew_Auto.scorer import calculate_metrics, calculate_alpha_score

logger = logging.getLogger(__name__)

DEEP_DIVES_DIR = os.path.join(DATA_DIR, "deep_dives")
os.makedirs(DEEP_DIVES_DIR, exist_ok=True)

# Deep dive settings
DEEP_DIVE_TX_LIMIT = 200
DEEP_DIVE_MIN_SCORE = 50  # Lowered since new scoring is stricter
DEEP_DIVE_MIN_TRADES = 5  # Must have at least 5 trades to qualify
DEEP_DIVE_TOP_N = 5


def deep_dive_wallet(wallet_addr: str, rpc_client) -> dict | None:
    """
    Performs a deep dive analysis on a single wallet.

    Fetches the last 200 transactions, parses all Raydium swaps,
    and calculates extended metrics.

    Returns a DeepDiveReport dict or None if analysis fails.
    """
    logger.info(f"🔬 Deep diving wallet: {wallet_addr[:16]}...")

    # Step 0: Find funding source
    funding_source = rpc_client.get_funding_source(wallet_addr)
    if funding_source:
        logger.info(f"   💰 Funding source: {funding_source[:16]}...")

    # Fetch transaction signatures for this wallet
    try:
        sig_resp = rpc_client.get_signatures_for_address(
            wallet_addr, limit=DEEP_DIVE_TX_LIMIT
        )
        signatures = sig_resp.value if hasattr(sig_resp, 'value') else []
    except Exception as e:
        logger.warning(f"   Failed to fetch sigs for deep dive: {e}")
        return None

    if not signatures:
        logger.info(f"   No transactions found for {wallet_addr[:16]}")
        return None

    logger.info(f"   Parsing {len(signatures)} transactions...")

    # Parse all transactions
    trades = []
    for sig_info in tqdm(signatures, desc=f"  Deep dive {wallet_addr[:8]}",
                         leave=False, ncols=80):
        try:
            sig_str = str(sig_info.signature)
            tx_resp = rpc_client.get_transaction(sig_str)
            trade = _parse_swap(tx_resp, target_mint="")
            if trade and trade["wallet"] == wallet_addr:
                trades.append(trade)
        except Exception:
            continue
        time.sleep(RPC_PACING_SECONDS)

    if not trades:
        logger.info(f"   No Raydium swaps found for {wallet_addr[:16]}")
        return None

    trades_df = pd.DataFrame(trades)

    # ── Extended Metrics ──────────────────────────────────────
    report = _build_report(wallet_addr, trades_df, funding_source)

    # Calculate verified alpha score with full trade history
    dd_metrics = calculate_metrics(trades_df)
    verified_score = calculate_alpha_score(dd_metrics)
    report["verified_alpha_score"] = verified_score
    report["verified_metrics"] = {
        "pnl_sol": dd_metrics["pnl_sol"],
        "win_rate": dd_metrics["win_rate"],
        "total_trades": dd_metrics["total_trades"],
        "copyability": dd_metrics["copyability"],
        "unique_tokens": dd_metrics["unique_tokens"],
        "avg_hold_time": dd_metrics["avg_hold_time"],
        "consistency": dd_metrics["consistency"],
        "verified": True,
    }
    logger.info(
        f"   Verified score: {verified_score:.1f} "
        f"(PnL: {dd_metrics['pnl_sol']:+.2f}, WR: {dd_metrics['win_rate']:.0%}, "
        f"trades: {dd_metrics['total_trades']})"
    )

    # Save report to disk
    report_path = os.path.join(DEEP_DIVES_DIR, f"{wallet_addr[:16]}.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    logger.info(f"   📄 Report saved: {report_path}")

    return report


def _build_report(wallet_addr: str, trades_df: pd.DataFrame, funding_source: str = None) -> dict:
    """Builds a comprehensive trading profile from trade data."""

    total_trades = len(trades_df)
    buys = trades_df[trades_df['is_buy'] == True]
    sells = trades_df[trades_df['is_sell'] == True]

    # Basic metrics
    total_pnl = trades_df['sol_change'].sum()

    # Win rate: per-token round-trip profitability (consistent with scorer)
    token_pnl = trades_df.groupby('token_mint')['sol_change'].sum()
    tokens_with_sells = set(
        trades_df[trades_df['is_sell'] == True]['token_mint'].unique()
    )
    completed = token_pnl[token_pnl.index.isin(tokens_with_sells)]
    profitable = int((completed > 0).sum())
    total_completed = len(completed)
    win_rate = profitable / total_completed if total_completed > 0 else 0

    # Token diversity
    unique_tokens = trades_df['token_mint'].nunique()

    # Trade sizes
    avg_buy_size = buys['trade_size_sol'].mean() if not buys.empty else 0
    max_buy_size = buys['trade_size_sol'].max() if not buys.empty else 0

    # Biggest win and biggest loss
    biggest_win = trades_df['sol_change'].max() if not trades_df.empty else 0
    biggest_loss = trades_df['sol_change'].min() if not trades_df.empty else 0

    # Activity span
    timestamps = trades_df['timestamp'].dropna()
    if len(timestamps) >= 2:
        activity_span_days = (timestamps.max() - timestamps.min()) / (60 * 60 * 24)
        trades_per_day = total_trades / max(activity_span_days, 1)
    else:
        activity_span_days = 0
        trades_per_day = 0

    # Per-token PnL breakdown
    token_pnl = {}
    for mint in trades_df['token_mint'].unique():
        token_trades = trades_df[trades_df['token_mint'] == mint]
        token_pnl[mint] = {
            "pnl_sol": round(token_trades['sol_change'].sum(), 4),
            "trades": len(token_trades),
            "buys": len(token_trades[token_trades['is_buy'] == True]),
            "sells": len(token_trades[token_trades['is_sell'] == True]),
        }

    # Sort tokens by PnL
    best_tokens = sorted(token_pnl.items(), key=lambda x: x[1]["pnl_sol"], reverse=True)
    worst_tokens = sorted(token_pnl.items(), key=lambda x: x[1]["pnl_sol"])

    return {
        "wallet": wallet_addr,
        "analysis_timestamp": int(time.time()),
        "funding_source": funding_source,
        "transactions_analyzed": total_trades,
        "summary": {
            "total_pnl_sol": round(total_pnl, 4),
            "win_rate": round(win_rate, 4),  # Fraction 0-1 (consistent with scorer)
            "unique_tokens_traded": unique_tokens,
            "total_buys": len(buys),
            "total_sells": len(sells),
            "activity_span_days": round(activity_span_days, 1),
            "trades_per_day": round(trades_per_day, 1),
        },
        "trade_sizes": {
            "avg_buy_sol": round(avg_buy_size, 4),
            "max_buy_sol": round(max_buy_size, 4),
        },
        "extremes": {
            "biggest_win_sol": round(biggest_win, 4),
            "biggest_loss_sol": round(biggest_loss, 4),
        },
        "top_3_tokens": [
            {"mint": mint[:20] + "...", **data}
            for mint, data in best_tokens[:3]
        ],
        "worst_3_tokens": [
            {"mint": mint[:20] + "...", **data}
            for mint, data in worst_tokens[:3]
        ],
    }


def run_deep_dives(results: list[dict], rpc_client, min_score: float = DEEP_DIVE_MIN_SCORE,
                   top_n: int = DEEP_DIVE_TOP_N) -> list[dict]:
    """
    Runs deep dives on the top N wallets with score above min_score.

    Args:
        results: List of scored wallet dicts from the main pipeline.
        rpc_client: SolanaRPCClient instance.
        min_score: Minimum alpha score to qualify for deep dive.
        top_n: Max number of wallets to deep dive.

    Returns:
        List of deep dive report dicts.
    """
    # Filter and select top candidates (score + min trades)
    candidates = [
        r for r in results
        if r["alpha_score"] >= min_score
        and r.get("total_trades", 0) >= DEEP_DIVE_MIN_TRADES
    ]
    candidates.sort(key=lambda x: x["alpha_score"], reverse=True)
    candidates = candidates[:top_n]

    if not candidates:
        logger.info("No wallets qualify for deep dive (score too low).")
        return []

    logger.info(f"🔬 Running deep dives on {len(candidates)} top wallets...")

    reports = []
    for r in candidates:
        report = deep_dive_wallet(r["wallet"], rpc_client)
        if report:
            reports.append(report)

    logger.info(f"✅ Deep dives complete: {len(reports)} reports generated")
    return reports


def print_deep_dive_report(report: dict):
    """Pretty-prints a deep dive report to console."""
    s = report["summary"]
    t = report["trade_sizes"]
    e = report["extremes"]

    print(f"\n{'─'*60}")
    print(f"🔬 DEEP DIVE: {report['wallet'][:32]}...")
    if report.get("funding_source"):
        print(f"💰 FUNDED BY: {report['funding_source'][:32]}...")
    print(f"{'─'*60}")
    if "verified_alpha_score" in report:
        print(f"  VERIFIED ALPHA SCORE  : {report['verified_alpha_score']:.1f}")
    print(f"  Transactions analyzed : {report['transactions_analyzed']}")
    print(f"  Activity span         : {s['activity_span_days']} days")
    print(f"  Trades/day            : {s['trades_per_day']}")
    print(f"  Total PnL             : {s['total_pnl_sol']:+.4f} SOL")
    print(f"  Win Rate              : {s['win_rate'] * 100:.1f}%")
    print(f"  Unique Tokens         : {s['unique_tokens_traded']}")
    print(f"  Avg Buy Size          : {t['avg_buy_sol']:.4f} SOL")
    print(f"  Biggest Win           : {e['biggest_win_sol']:+.4f} SOL")
    print(f"  Biggest Loss          : {e['biggest_loss_sol']:+.4f} SOL")

    if report.get("top_3_tokens"):
        print(f"\n  📈 Best tokens:")
        for t in report["top_3_tokens"]:
            print(f"     {t['mint']} → {t['pnl_sol']:+.4f} SOL ({t['trades']} trades)")

    if report.get("worst_3_tokens"):
        print(f"\n  📉 Worst tokens:")
        for t in report["worst_3_tokens"]:
            print(f"     {t['mint']} → {t['pnl_sol']:+.4f} SOL ({t['trades']} trades)")

    print(f"{'─'*60}")
