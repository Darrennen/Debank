# shadow_nav_app.py
# DebankClient (with robust retries + DNS diagnostics) + Streamlit UI in ONE file

import os
import time
import socket
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ------------------------------
# Debank client (v2)
# ------------------------------

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


# ------------------------------
# Streamlit UI
# ------------------------------
try:
    import streamlit as st
except Exception:
    # If streamlit isn't installed, just skip UI import errors
    st = None

if st:
    st.set_page_config(page_title="Shadow NAV x DeBank (One-File App)", layout="wide")

    st.sidebar.header("Setup")
    default_key = os.getenv("DEBANK_API_KEY", "")
    api_key = st.sidebar.text_input("DEBANK_API_KEY", value=default_key, type="password")
    header_name = st.sidebar.text_input("Header Name", value=os.getenv("DEBANK_HEADER_NAME", "AccessKey"))
    base_url = st.sidebar.text_input("Base URL", value=os.getenv("DEBANK_BASE_URL", DEFAULT_BASE_URL))

    st.sidebar.caption("If you're on the FREE tier, try: https://openapi.debank.com (limited endpoints)")
    show_debug = st.sidebar.toggle("Show debug tracebacks", value=False)

    st.sidebar.divider()
    addrs = st.sidebar.text_area("Wallet addresses (one per line)", placeholder="0x123...\n0xabc...")
    go = st.sidebar.button("Fetch")

    st.title("Shadow NAV Board – DeBank Pro API Demo (Single File)")
    st.caption("Wired to the endpoints your UI needs. DNS/auth errors are shown clearly.")

    def safe_call(label: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DebankError as e:
            st.error(f"{label}: {e}")
            if show_debug:
                st.exception(e)
            return None
        except Exception as e:
            st.error(f"{label}: Unexpected error: {e}")
            if show_debug:
                st.exception(e)
            return None

    if go:
        if not api_key:
            st.error("Please provide DEBANK_API_KEY (in env or sidebar).")
            st.stop()

        # Build client (and surface DNS/auth errors early)
        api = None
        try:
            api = DebankClient(api_key=api_key, header_name=header_name, base_url=base_url)
        except Exception as e:
            st.error(f"Client init failed: {e}")
            if show_debug:
                st.exception(e)
            st.stop()

        addresses = [a.strip() for a in addrs.splitlines() if a.strip()]
        if not addresses:
            st.warning("Enter at least one address.")
            st.stop()

        tabs = st.tabs(["Overview", "Per Wallet", "Approvals", "Activity", "Curves"])

        # ------------- Overview -------------
        with tabs[0]:
            st.subheader("Multi-wallet Overview")
            rows = []
            for addr in addresses:
                summary = safe_call("summarize_wallet", api.summarize_wallet, addr) or {}
                rows.append({
                    "address": addr,
                    "total_usd": summary.get("total_usd"),
                    "chains": len(summary.get("chains") or []),
                    "positions": len(summary.get("positions") or []),
                })
            st.dataframe(rows, use_container_width=True)

        # ------------- Per Wallet -------------
        with tabs[1]:
            st.subheader("Single Wallet Detail")
            if addresses:
                addr = st.selectbox("Select wallet", options=addresses)
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Total Balance (USD)**")
                    st.json(safe_call("get_total_balance", api.get_total_balance, addr))
                    st.markdown("**Used Chains**")
                    chains = safe_call("get_used_chains", api.get_used_chains, addr)
                    st.json(chains)
                with col2:
                    st.markdown("**DeFi Positions (cached across chains)**")
                    positions = safe_call("get_complex_protocol_list", api.get_complex_protocol_list, addr)
                    st.json((positions or [])[:5])
                    st.caption("Tip: On protocol row click, call get_protocol_detail(addr, protocol_id) for fresh drilldown.")

                st.markdown("---")
                st.markdown("**Coins in Wallet (all chains)**")
                tokens = safe_call("get_all_token_list", api.get_all_token_list, addr, True)
                st.json((tokens or [])[:25])

        # ------------- Approvals -------------
        with tabs[2]:
            st.subheader("Approvals / Allowances")
            if addresses:
                addr = st.selectbox("Select wallet for approvals", options=addresses, key="appr_addr")
                chain_id = st.text_input("Chain ID (e.g., eth, op, arb, bsc, polygon)", value="eth")
                if st.button("Load Approvals"):
                    st.markdown("**Token approvals**")
                    st.json(safe_call("get_token_approvals", api.get_token_approvals, addr, chain_id))
                    st.markdown("**NFT approvals**")
                    st.json(safe_call("get_nft_approvals", api.get_nft_approvals, addr, chain_id))

        # ------------- Activity -------------
        with tabs[3]:
            st.subheader("History / Activity")
            if addresses:
                addr = st.selectbox("Select wallet for history", options=addresses, key="hist_addr")
                chain_id = st.text_input("Chain ID", value="eth", key="hist_chain")
                st.caption("Use start_time (unix seconds) for pagination if needed.")
                if st.button("Load History"):
                    st.json(safe_call("get_history_list", api.get_history_list, addr, chain_id, page_count=50))

        # ------------- Curves -------------
        with tabs[4]:
            st.subheader("Curves (sparklines)")
            if addresses:
                addr = st.selectbox("Select wallet for curves", options=addresses, key="curve_addr")
                chain_id = st.text_input("Chain ID", value="eth", key="curve_chain")
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Total Net Curve**")
                    st.json((safe_call("get_total_net_curve", api.get_total_net_curve, addr) or [])[:30])
                with col2:
                    st.markdown("**Chain Net Curve**")
                    st.json((safe_call("get_chain_net_curve", api.get_chain_net_curve, addr, chain_id) or [])[:30])

else:
    # If Streamlit isn't available, give a tiny tip when someone runs it as a plain script.
    if __name__ == "__main__":
        print("Streamlit is not installed. Install it with `pip install streamlit` and run:")
        print("  streamlit run shadow_nav_app.py")
