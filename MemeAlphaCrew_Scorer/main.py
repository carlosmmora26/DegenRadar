import sys
import argparse
import json
import logging
from MemeAlphaCrew_Scorer.rpc_client import SolanaRPCClient
from MemeAlphaCrew_Scorer.parser import RaydiumParser
from MemeAlphaCrew_Scorer.filters import run_all_filters
from MemeAlphaCrew_Scorer.scorer import calculate_metrics, calculate_alpha_score
from MemeAlphaCrew_Scorer.config import MAX_TX_HISTORY_LIMIT, WALLETS_DATA_FILE

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="DegenRadar Scorer: Identify Smart Retail Traders on Solana")
    parser.add_argument("wallet", help="Solana wallet address to score")
    args = parser.parse_args()

    wallet_address = args.wallet
    logger.info(f"Analyzing wallet: {wallet_address}")

    client = SolanaRPCClient()
    
    try:
        # 1. Get Current Balance
        balance_resp = client.get_balance(wallet_address)
        current_balance = balance_resp.value / 1e9
        logger.info(f"Current Balance: {current_balance:.2f} SOL")

        # 2. Get Transaction Signatures
        sig_resp = client.get_signatures_for_address(wallet_address, limit=MAX_TX_HISTORY_LIMIT)
        signatures = [s.signature for s in sig_resp.value]
        logger.info(f"Fetched {len(signatures)} transactions.")

        if not signatures:
            logger.error("No transactions found for this wallet.")
            return

        # 3. Fetch and Parse Transactions
        parser_obj = RaydiumParser(wallet_address)
        tx_details = []
        import time
        for i, sig in enumerate(signatures):
            if (i+1) % 10 == 0:
                logger.info(f"Parsing transaction {i+1}/{len(signatures)}...")
            
            # Pacing for free tier
            if i > 0:
                time.sleep(0.5)
                
            tx_data = client.get_transaction(str(sig))
            parsed = parser_obj.parse_transaction(tx_data)
            if parsed:
                tx_details.append(parsed)

        import pandas as pd
        trades_df = pd.DataFrame(tx_details)
        logger.info(f"Identified {len(trades_df)} Raydium Swap events.")

        # 4. Calculate Metrics
        pnl, win_rate, consistency = calculate_metrics(trades_df)
        
        # 5. Apply Filters
        is_filtered, reason = run_all_filters(trades_df, current_balance, win_rate)
        
        if is_filtered:
            print("\n" + "="*40)
            print(f"WALLET: {wallet_address}")
            print(f"STATUS: DISCARDED")
            print(f"REASON: {reason}")
            print("="*40)
            return

        # 6. Calculate Alpha Score
        alpha_score = calculate_alpha_score(pnl, win_rate, consistency)

        # 7. Display Results
        print("\n" + "="*40)
        print(f"WALLET: {wallet_address}")
        print(f"STATUS: SMART RETAIL IDENTIFIED")
        print(f"ALPHA SCORE: {alpha_score}/100")
        print("-" * 20)
        print(f"Realized PnL: {pnl:.2f} SOL")
        print(f"Win Rate:     {win_rate*100:.1f}%")
        print(f"Consistency:  {consistency*100:.1f}%")
        print("="*40)

        # 8. Save results
        save_results(wallet_address, alpha_score, pnl, win_rate, consistency)

    except Exception as e:
        logger.error(f"Analysis failed: {e}")

def save_results(wallet, score, pnl, win_rate, consistency):
    data = {
        "wallet": wallet,
        "alpha_score": score,
        "pnl_sol": pnl,
        "win_rate": win_rate,
        "consistency": consistency
    }
    
    try:
        results = []
        import os
        if os.path.exists(WALLETS_DATA_FILE):
            with open(WALLETS_DATA_FILE, 'r') as f:
                content = f.read()
                if content:
                    results = json.loads(content)
        
        # Update or add new result
        updated = False
        for i, r in enumerate(results):
            if r['wallet'] == wallet:
                results[i] = data
                updated = True
                break
        if not updated:
            results.append(data)
            
        with open(WALLETS_DATA_FILE, 'w') as f:
            json.dump(results, f, indent=4)
        logger.info(f"Results saved to {WALLETS_DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save results: {e}")

if __name__ == "__main__":
    main()
