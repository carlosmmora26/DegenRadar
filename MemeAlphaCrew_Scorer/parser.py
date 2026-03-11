import logging
import pandas as pd
from MemeAlphaCrew_Scorer.config import RAYDIUM_V4_PROGRAM_ID, SOL_MINT

logger = logging.getLogger(__name__)

class RaydiumParser:
    def __init__(self, wallet_address):
        self.wallet_address = wallet_address

    def parse_transaction(self, tx_response):
        """
        Parses a transaction to identify Raydium Swap events and balance changes.
        Returns a dictionary with trade details or None if not a relevant swap.
        """
        if not tx_response or not hasattr(tx_response, 'value') or tx_response.value is None:
            return None

        # Convert to dictionary for robust parsing across library versions
        import json
        try:
            tx_data = json.loads(tx_response.to_json())['result']
        except:
            # Fallback for different response formats
            try:
                tx_data = json.loads(tx_response.value.to_json())
            except:
                return None

        if not tx_data:
            return None

        meta = tx_data.get('meta')
        transaction = tx_data.get('transaction')
        
        if not meta or not transaction:
            return None

        # Check if Raydium V4 is involved
        message = transaction.get('message', {})
        account_keys = message.get('accountKeys', [])
        program_ids = [str(key) for key in account_keys]
        
        if RAYDIUM_V4_PROGRAM_ID not in program_ids:
            return None

        # Identify account index for our wallet
        wallet_index = -1
        for i, key in enumerate(account_keys):
            if str(key) == self.wallet_address:
                wallet_index = i
                break
        
        if wallet_index == -1:
            return None

        # Calculate SOL change
        pre_balances = meta.get('preBalances', [])
        post_balances = meta.get('postBalances', [])
        if len(pre_balances) > wallet_index and len(post_balances) > wallet_index:
            sol_change = (post_balances[wallet_index] - pre_balances[wallet_index]) / 1e9
        else:
            sol_change = 0

        # Calculate Token changes
        pre_token_balances = meta.get('preTokenBalances', [])
        post_token_balances = meta.get('postTokenBalances', [])
        
        token_changes = []
        
        # Map tokens by mint
        pre_map = {str(b.get('mint')): b for b in pre_token_balances if b.get('accountIndex') == wallet_index}
        post_map = {str(b.get('mint')): b for b in post_token_balances if b.get('accountIndex') == wallet_index}
        
        all_mints = set(pre_map.keys()) | set(post_map.keys())
        
        for mint in all_mints:
            if mint == SOL_MINT: # Wrap SOL etc.
                continue
                
            pre_amount = float(pre_map.get(mint, {}).get('uiTokenAmount', {}).get('uiAmount', 0) or 0)
            post_amount = float(post_map.get(mint, {}).get('uiTokenAmount', {}).get('uiAmount', 0) or 0)
            change = post_amount - pre_amount
            
            if change != 0:
                token_changes.append({
                    'mint': mint,
                    'change': change,
                    'is_buy': change > 0,
                    'is_sell': change < 0
                })

        if not token_changes:
            return None

        # We assume one swap per transaction for simplicity in "Meme" context
        main_token = token_changes[0]
        
        return {
            'signature': transaction.get('signatures', [None])[0],
            'timestamp': tx_data.get('blockTime'),
            'sol_change': sol_change,
            'token_mint': main_token['mint'],
            'token_change': main_token['change'],
            'is_buy': main_token['is_buy'],
            'is_sell': main_token['is_sell'],
            'trade_size_sol': abs(sol_change)
        }

    def process_transactions(self, transactions):
        """Processes a list of transactions and returns a DataFrame of trades."""
        trades = []
        for tx in transactions:
            parsed = self.parse_transaction(tx)
            if parsed:
                trades.append(parsed)
        
        return pd.DataFrame(trades)
