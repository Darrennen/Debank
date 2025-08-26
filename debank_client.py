# debank_client_v2.py
# Drop-in replacement for DebankClient with stronger retries and clearer errors.

import os
import time
import socket
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = "https://api.cloud.debank.com"

class DebankError(Exception):
    pass

class DebankClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        header_name: Optional[str] = None,
        timeout: int = 20,
        max_retries: int = 3,
        backoff: float = 0.8,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("DEBANK_API_KEY")
        if not self.api_key:
            raise ValueError("Missing API key. Set DEBANK_API_KEY or pass api_key=...")
        # DeBank Cloud usually uses 'AccessKey'; some orgs use 'X-API-Key'
        self.header_name = header_name or os.getenv("DEBANK_HEADER_NAME", "AccessKey")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff

        # robust session with retries on 429/5xx, and connection errors
        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=50)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ---------- Utils ----------
    def _headers(self) -> Dict[str, str]:
        return {
            self.header_name: self.api_key,
            "accept": "application/json",
            "user-agent": "shadow-nav/1.1",
        }

    def _diagnose_dns(self) -> Optional[str]:
        try:
            host = self.base_url.split("://", 1)[-1].split("/", 1)[0]
            socket.gethostbyname(host)
            return None
        except Exception as e:
            return f"DNS failed for host in Base URL '{self.base_url}': {e}"

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        # quick DNS check to return a clearer message
        dns_issue = self._diagnose_dns()
        if dns_issue:
            raise DebankError(dns_issue + ". Try switching networks or DNS (1.1.1.1 / 8.8.8.8).")

        try:
            r = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            # Connection/DNS/Timeout etc.
            raise DebankError(f"Network error calling {url}: {e}. Check VPN/Proxy/DNS.") from e

        # Manual handling for rate limit (429) to give a friendlier hint
        if r.status_code == 429:
            raise DebankError("Rate limited by DeBank (HTTP 429). Reduce frequency or add backoff.")

        # Raise for other HTTP errors
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            # Include body for debugging if JSON
            body = None
            try:
                body = r.json()
            except Exception:
                body = r.text[:500]
            raise DebankError(f"HTTP {r.status_code} on {path}: {body}") from e

        # Normalize payloads
        data = r.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    # ---------------- Wallet Summary ----------------
    def get_total_balance(self, addr: str) -> Dict[str, Any]:
        return self._get("/v1/user/total_balance", {"id": addr})

    def get_chain_balance(self, addr: str, chain_id: str) -> Dict[str, Any]:
        return self._get("/v1/user/chain_balance", {"id": addr, "chain_id": chain_id})

    def get_used_chains(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/used_chain_list", {"id": addr})

    # ---------------- Tokens ----------------
    def get_token_list(self, addr: str, chain_id: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/token_list", {"id": addr, "chain_id": chain_id, "is_all": str(is_all).lower()})

    def get_all_token_list(self, addr: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_token_list", {"id": addr, "is_all": str(is_all).lower()})

    def get_token(self, addr: str, chain_id: str, token_id: str) -> Dict[str, Any]:
        return self._get("/v1/user/token", {"id": addr, "chain_id": chain_id, "token_id": token_id})

    # --------------- DeFi Positions ---------------
    def get_complex_protocol_list(self, addr: str, chain_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if chain_id:
            return self._get("/v1/user/complex_protocol_list", {"id": addr, "chain_id": chain_id})
        return self._get("/v1/user/all_complex_protocol_list", {"id": addr})

    def get_protocol_detail(self, addr: str, protocol_id: str) -> Dict[str, Any]:
        return self._get("/v1/user/protocol", {"id": addr, "protocol_id": protocol_id})

    # ---------------- Curves ----------------
    def get_total_net_curve(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/total_net_curve", {"id": addr})

    def get_chain_net_curve(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/chain_net_curve", {"id": addr, "chain_id": chain_id})

    # ---------------- Activity ----------------
    def get_all_history_list(self, addr: str, start_time: Optional[int] = None, page_count: int = 50) -> List[Dict[str, Any]]:
        params = {"id": addr, "page_count": page_count}
        if start_time:
            params["start_time"] = start_time
        return self._get("/v1/user/all_history_list", params)

    def get_history_list(self, addr: str, chain_id: str, start_time: Optional[int] = None, page_count: int = 50) -> List[Dict[str, Any]]:
        params = {"id": addr, "chain_id": chain_id, "page_count": page_count}
        if start_time:
            params["start_time"] = start_time
        return self._get("/v1/user/history_list", params)

    # ---------------- Approvals ----------------
    def get_token_approvals(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/token_authorized_list", {"id": addr, "chain_id": chain_id})

    def get_nft_approvals(self, addr: str, chain_id: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/nft_authorized_list", {"id": addr, "chain_id": chain_id})

    # ---------------- Helper ----------------
    def summarize_wallet(self, addr: str) -> Dict[str, Any]:
        total = self.get_total_balance(addr)
        chains = self.get_used_chains(addr)
        positions = self.get_complex_protocol_list(addr)
        return {
            "address": addr,
            "total_usd": total.get("usd_value"),
            "net_usd": total.get("net_usd_value") or total.get("usd_value"),
            "chains": chains,
            "positions": positions,
        }
