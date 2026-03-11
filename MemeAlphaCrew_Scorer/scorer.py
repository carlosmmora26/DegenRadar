import numpy as np
from MemeAlphaCrew_Scorer.config import (
    WEIGHT_REALIZED_PNL,
    WEIGHT_CONSISTENCY,
    WEIGHT_WIN_RATE
)

def calculate_metrics(trades_df):
    """
    Calculates PnL, Win Rate, and Consistency from trades.
    Note: Realized PnL is simplified as the sum of SOL changes.
    Since 'Buy' has negative SOL change and 'Sell' has positive, 
    the sum represents the current PnL of closed positions (and cost of open ones).
    """
    if trades_df.empty:
        return 0, 0, 0
    
    # Simple PnL: sum of sol_change (incorporates both buys and sells)
    total_pnl_sol = trades_df['sol_change'].sum()
    
    # Win Rate: count trades where sol_change was positive (profitable sells)
    # This is a bit simplistic, but fits the 'Swap' event parsing.
    profitable_trades = len(trades_df[trades_df['sol_change'] > 0])
    total_trades = len(trades_df)
    win_rate = profitable_trades / total_trades if total_trades > 0 else 0
    
    # Consistency: Ratio of unique tokens traded to total trades (0.5 target)
    unique_tokens = trades_df['token_mint'].nunique()
    consistency = min(unique_tokens / 10.0, 1.0) # Reward diversification up to 10 tokens
    
    return total_pnl_sol, win_rate, consistency

def calculate_alpha_score(pnl_sol, win_rate, consistency):
    """
    Calculates the Alpha Score (0-100).
    Normalizes metrics before weighting.
    """
    # Normalize PnL: 0 at 0 SOL, 1 at 10 SOL profit.
    normalized_pnl = min(max(pnl_sol / 10.0, 0), 1.0)
    
    # Normalizing win_rate (already 0-1) and consistency (already 0-1)
    
    raw_score = (
        (normalized_pnl * WEIGHT_REALIZED_PNL) +
        (consistency * WEIGHT_CONSISTENCY) +
        (win_rate * WEIGHT_WIN_RATE)
    )
    
    return round(raw_score * 100, 2)
