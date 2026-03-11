import time
import requests
import logging
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.signature import Signature
from MemeAlphaCrew_Scorer.config import RPC_URL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SolanaRPCClient:
    def __init__(self, rpc_url=RPC_URL):
        self.client = Client(rpc_url)
        self.rpc_url = rpc_url

    def _request_with_backoff(self, method, *args, **kwargs):
        max_retries = 5
        base_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                # Use requests for low-level control if needed, 
                # but solana-py Client usually handles standard RPC calls.
                # However, for rate limits (429), we need to catch them.
                response = method(*args, **kwargs)
                
                # Check for error in response if it's a dictionary (solana-py older versions or direct JSON-RPC)
                if hasattr(response, 'value') or (isinstance(response, dict) and 'error' not in response):
                    return response
                
                if isinstance(response, dict) and 'error' in response:
                    error_code = response['error'].get('code')
                    if error_code == -32005: # Rate limit reached in some Solana nodes
                        raise requests.exceptions.HTTPError("Rate limit reached")
                
                return response

            except Exception as e:
                import httpx
                from solana.exceptions import SolanaRpcException
                error_str = str(e)
                is_rate_limit = "429" in error_str or "Rate limit" in error_str or "Too Many Requests" in error_str
                
                # solana-py uses httpx under the hood, but wraps errors in SolanaRpcException
                if not is_rate_limit:
                    if isinstance(e, SolanaRpcException):
                        # Try to find 429 in any of the underlying exceptions/messages
                        if "429" in str(e.__cause__) or "429" in str(e.__context__):
                            is_rate_limit = True
                    elif isinstance(e, httpx.HTTPStatusError):
                        if e.response.status_code == 429:
                            is_rate_limit = True
                    # If we got an empty error message but it's an RPC error, it might still be a rate limit
                    elif isinstance(e, SolanaRpcException) and not error_str:
                        is_rate_limit = True 

                if is_rate_limit:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limit hit. Retrying in {delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                else:
                    logger.error(f"RPC Error Type: {type(e)}")
                    logger.error(f"RPC Error: {error_str}")
                    raise e
        
        raise Exception(f"Failed after {max_retries} retries due to rate limits.")

    def get_balance(self, wallet_address):
        pubkey = Pubkey.from_string(wallet_address)
        return self._request_with_backoff(self.client.get_balance, pubkey)

    def get_signatures_for_address(self, wallet_address, limit=50):
        pubkey = Pubkey.from_string(wallet_address)
        return self._request_with_backoff(self.client.get_signatures_for_address, pubkey, limit=limit)

    def get_transaction(self, signature):
        if isinstance(signature, str):
            signature = Signature.from_string(signature)
        return self._request_with_backoff(
            self.client.get_transaction, 
            signature, 
            max_supported_transaction_version=0
        )

    def get_token_accounts_by_owner(self, wallet_address):
        pubkey = Pubkey.from_string(wallet_address)
        # Filters can be added if specific mints are needed
        return self._request_with_backoff(self.client.get_token_accounts_by_owner, pubkey, program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"))
