from MemeAlphaCrew_Scorer.config import (
    MAX_AVG_BUY_SIZE_SOL,
    MAX_CURRENT_SOL_BALANCE,
    MIN_UNIQUE_TOKENS_TRADED,
    BOT_WIN_RATE_THRESHOLD,
    GAMBLER_WIN_RATE_THRESHOLD
)

def is_whale(trades_df, current_sol_balance):
    """
    Filter 1: The Anti-Whale Check:
    - Discard if avg_buy_size_sol > 5 SOL.
    - Discard if current_sol_balance > 100 SOL.
    """
    if current_sol_balance > MAX_CURRENT_SOL_BALANCE:
        return True, f"Balance too high: {current_sol_balance} SOL"
    
    if trades_df.empty:
        return False, None
        
    buys = trades_df[trades_df['is_buy'] == True]
    if buys.empty:
        return False, None
        
    avg_buy_size = buys['trade_size_sol'].mean()
    if avg_buy_size > MAX_AVG_BUY_SIZE_SOL:
        return True, f"Avg buy size too high: {avg_buy_size:.2f} SOL"
        
    return False, None

def is_insider(trades_df):
    """
    Filter 2: The Insider Check:
    - Discard if unique_tokens_traded < 5 (in the last 50 txs).
    """
    if trades_df.empty:
        return True, "No trades found"
        
    unique_tokens = trades_df['token_mint'].nunique()
    if unique_tokens < MIN_UNIQUE_TOKENS_TRADED:
        return True, f"Too few unique tokens: {unique_tokens}"
        
    return False, None

def humanity_check(win_rate):
    """
    Filter 3: The Humanity Check:
    - Discard if win_rate == 100% (Bot/Scammer).
    - Discard if win_rate < 20% (Gambler).
    """
    if win_rate >= BOT_WIN_RATE_THRESHOLD:
        return True, f"Win rate too high (Bot/Scammer): {win_rate*100:.1f}%"
    
    if win_rate < GAMBLER_WIN_RATE_THRESHOLD:
        return True, f"Win rate too low (Gambler): {win_rate*100:.1f}%"
        
    return False, None

def run_all_filters(trades_df, current_sol_balance, win_rate):
    """Runs all filters and returns (is_filtered, reason)"""
    
    whale, reason = is_whale(trades_df, current_sol_balance)
    if whale:
        return True, reason
        
    insider, reason = is_insider(trades_df)
    if insider:
        return True, reason
        
    humanity, reason = humanity_check(win_rate)
    if humanity:
        return True, reason
        
    return False, None
