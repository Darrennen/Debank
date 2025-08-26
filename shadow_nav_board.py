# shadow_nav_board.py
# Shadow NAV board — one-page UI with per-wallet comment log + persistence.
# - Uses Streamlit Secrets when available (DEBANK_API_KEY / DEBANK_BASE_URL / DEBANK_HEADER_NAME)
# - Persists wallets & comments to local JSON (shadow_nav_store.json) so they survive reload/log out
# - Board at top; click a wallet to show Dollar Value, DeFi Positions, and Token Holdings below

import os
import json
import time
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = "https://pro-openapi.debank.com"
STORE_PATH = os.environ.get("SHADOW_NAV_STORE", "shadow_nav_store.json")

# ---------------- DeBank client ----------------

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
        proxies: Optional[Dict[str, str]] = None,
        user_agent: str = "shadow-nav/board/1.4",
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("DEBANK_API_KEY")
        if not self.api_key:
            raise ValueError("Missing API key. Set DEBANK_API_KEY or pass api_key=...")
        self.header_name = header_name or os.getenv("DEBANK_HEADER_NAME", "AccessKey")
        self.timeout = timeout

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
        if proxies:
            self.session.proxies.update(proxies)

        self.user_agent = user_agent

    def _headers(self) -> Dict[str, str]:
        return {
            self.header_name: self.api_key,
            "accept": "application/json",
            "user-agent": self.user_agent,
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
        dns_issue = self._diagnose_dns()
        if dns_issue:
            raise DebankError(dns_issue + ". Try VPN/proxy or change DNS (1.1.1.1 / 8.8.8.8).")
        try:
            r = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise DebankError(f"Network error calling {url}: {e}") from e
        if r.status_code == 429:
            raise DebankError("Rate limited by DeBank (HTTP 429). Reduce frequency or add backoff.")
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            try:
                body = r.json()
            except Exception:
                body = r.text[:500]
            raise DebankError(f"HTTP {r.status_code} on {path}: {body}") from e
        data = r.json()
        return data["data"] if isinstance(data, dict) and "data" in data else data

    # Endpoints used
    def get_total_balance(self, addr: str) -> Dict[str, Any]:
        return self._get("/v1/user/total_balance", {"id": addr})

    def get_all_token_list(self, addr: str, is_all: bool = True) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_token_list", {"id": addr, "is_all": str(is_all).lower()})

    def get_complex_protocol_list(self, addr: str) -> List[Dict[str, Any]]:
        return self._get("/v1/user/all_complex_protocol_list", {"id": addr})


# ---------------- Streamlit UI ----------------
try:
    import streamlit as st
except Exception:
    st = None

if st:
    st.set_page_config(page_title="Shadow NAV Board — One Page", layout="wide")

    # ---- Rerun helper (new/old Streamlit) ----
    def do_rerun():
        try:
            st.rerun()
        except AttributeError:
            try:
                st.experimental_rerun()  # older versions
            except AttributeError:
                pass

    # ---- Secrets/env config helpers ----
    def cfg(name: str, default=None):
        try:
            val = st.secrets.get(name)  # Streamlit Secrets
        except Exception:
            val = None
        if val is None:
            val = os.getenv(name)       # Environment variable
        return val if val is not None else default

    api_key_default     = cfg("DEBANK_API_KEY", "")
    header_name_default = cfg("DEBANK_HEADER_NAME", "AccessKey")
    base_url_default    = cfg("DEBANK_BASE_URL", DEFAULT_BASE_URL)

    have_api_key = bool(api_key_default)
    have_header  = bool(header_name_default)
    have_base    = bool(base_url_default)

    # ---- Persistence helpers (wallets & comments) ----
    def load_store() -> Dict[str, Any]:
        try:
            if os.path.exists(STORE_PATH):
                with open(STORE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"wallets": [], "comments": {}, "active_idx": None}

    def save_store(wallets: List[Dict[str, str]], comments: Dict[str, List[Dict[str, str]]], active_idx: Optional[int]):
        try:
            data = {"wallets": wallets, "comments": comments, "active_idx": active_idx}
            with open(STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            st.warning(f"Could not save store: {e}")

    # ---- Initial state (load persisted store ONCE) ----
    if "store_loaded" not in st.session_state:
        store = load_store()
        st.session_state.wallets = store.get("wallets", [])
        st.session_state.comments = store.get("comments", {})
        st.session_state.active_idx = store.get("active_idx", None)
        st.session_state.refresh_nonce = 0
        st.session_state.store_loaded = True

    # ---- Sidebar ----
    st.sidebar.header("Setup")
    api_key     = st.sidebar.text_input("DEBANK_API_KEY", value=api_key_default, type="password", disabled=have_api_key)
    header_name = st.sidebar.text_input("Header Name", value=header_name_default, disabled=have_header)
    base_url    = st.sidebar.text_input("Base URL", value=base_url_default, disabled=have_base)
    if have_api_key:
        st.sidebar.success("Using DEBANK_API_KEY from secrets/env")

    st.sidebar.divider()
    st.sidebar.caption(
        "Enter wallets (one per line). Formats:\n"
        "  Client, Wallet Label, 0xAddress\n"
        "  Wallet Label, 0xAddress\n"
        "  0xAddress  (auto-labeled Wallet N)"
    )
    wallets_text = st.sidebar.text_area(
        "Wallets",
        placeholder="Darren, Darren #1, 0x123...\nDarren, Darren #2, 0xabc...\nAlice, Wallet A, 0x456...",
        height=140,
    )
    col_sb1, col_sb2 = st.sidebar.columns(2)

    def parse_wallets(text: str) -> List[Dict[str, str]]:
        items, i = [], 1
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            client, label, addr = None, None, None
            if len(parts) >= 3:
                client, label, addr = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                label, addr = parts[0], parts[1]
            else:
                addr = parts[0]
            label = label or f"Wallet {i}"
            client = client or "Unassigned"
            items.append({"client": client, "label": label, "addr": addr})
            i += 1
        return items

    if col_sb1.button("Load Wallets"):
        st.session_state.wallets = parse_wallets(wallets_text)
        if st.session_state.active_idx is not None and st.session_state.active_idx >= len(st.session_state.wallets):
            st.session_state.active_idx = None
        save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)

    if col_sb2.button("Clear All"):
        st.session_state.wallets = []
        st.session_state.comments = {}
        st.session_state.active_idx = None
        save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)

    # ---- Build client ----
    def build_client() -> Optional[DebankClient]:
        if not api_key:
            st.error("Please provide DEBANK_API_KEY.")
            return None
        try:
            return DebankClient(api_key=api_key, base_url=base_url, header_name=header_name)
        except Exception as e:
            st.error(f"Client init failed: {e}")
            return None

    api = build_client()

    # ---- Helpers ----
    def safe_call(msg, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DebankError as e:
            st.error(f"{msg}: {e}")
        except Exception as e:
            st.error(f"{msg}: Unexpected error: {e}")
        return None

    def fmt_usd(v) -> str:
        try:
            return f"${float(v):,.2f}"
        except Exception:
            return "-"

    def position_rows(positions):
        rows = []
        for p in positions or []:
            usd = p.get("usd_value")
            if usd is None:
                total = 0.0
                for it in p.get("portfolio_item_list", []) or []:
                    stats = (it or {}).get("stats") or {}
                    v = stats.get("net_usd_value") or stats.get("usd_value") or stats.get("asset_usd_value") or 0
                    try:
                        total += float(v)
                    except Exception:
                        pass
                usd = total
            rows.append({
                "Protocol": p.get("name") or p.get("id"),
                "Chain": p.get("chain"),
                "USD Value": usd,
            })
        rows.sort(key=lambda r: (r["USD Value"] or 0), reverse=True)
        return rows

    def token_rows(tokens):
        rows = []
        for t in tokens or []:
            usd = t.get("usd_value")
            if usd is None:
                try:
                    usd = float(t.get("price") or 0) * float(t.get("amount") or 0)
                except Exception:
                    usd = 0.0
            rows.append({
                "Token": t.get("display_symbol") or t.get("symbol") or t.get("name"),
                "Chain": t.get("chain"),
                "Amount": t.get("amount"),
                "Price": t.get("price"),
                "USD Value": usd,
            })
        rows.sort(key=lambda r: (r["USD Value"] or 0), reverse=True)
        return rows

    # --------- PAGE: Board (top) + Selected Wallet (bottom) ---------
    st.title("Shadow NAV Board — One Page (Persisted)")

    if not st.session_state.wallets:
        st.info("Add wallets in the sidebar and click **Load Wallets**.")
        st.stop()

    # Filters / actions
    clients = sorted({w["client"] for w in st.session_state.wallets})
    sel_clients = st.multiselect("Filter by Client", options=clients, default=clients)
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("Refresh balances"):
        st.session_state.refresh_nonce = int(time.time())  # used only to trigger rerender paths

    selected_total_placeholder = c2.empty()
    selected_total_value = 0.0

    # Board list
    st.markdown("### Wallets")
    hdr = st.columns([2, 3, 4, 2, 1, 2, 1])
    hdr[0].markdown("**Client**")
    hdr[1].markdown("**Wallet**")
    hdr[2].markdown("**Address**")
    hdr[3].markdown("**Dollar Value**")
    hdr[4].markdown("**Select**")
    hdr[5].markdown("**Comments**")
    hdr[6].markdown("**Delete**")

    to_delete_idx = None
    for idx, w in enumerate(st.session_state.wallets):
        if w["client"] not in sel_clients:
            continue

        cols = st.columns([2, 3, 4, 2, 1, 2, 1])
        cols[0].write(w["client"])

        # Clicking label or address selects the wallet (details shown below)
        if cols[1].button(w["label"], key=f"open_label_{idx}"):
            st.session_state.active_idx = idx
            save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)
        if cols[2].button(w["addr"], key=f"open_addr_{idx}"):
            st.session_state.active_idx = idx
            save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)

        total = {"total_usd_value": None}
        if api:
            total = safe_call(f"{w['label']} total", api.get_total_balance, w["addr"]) or total
        cols[3].write(fmt_usd(total.get("total_usd_value") or total.get("usd_value") or 0))

        sel = cols[4].checkbox("", key=f"sel_{idx}")
        if sel:
            try:
                selected_total_value += float(total.get("total_usd_value") or total.get("usd_value") or 0)
            except Exception:
                pass

        # ----- Per-wallet Comment Log (timestamped, persisted) -----
        with cols[5].expander("💬 Log", expanded=False):
            addr = w["addr"]
            log = st.session_state.comments.get(addr, [])
            if log:
                for entry in reversed(log[-5:]):
                    st.write(f"- *{entry['ts']}*: {entry['text']}")
            else:
                st.caption("No comments yet.")
            new_text = st.text_input("Add a comment", key=f"cmt_input_{idx}", placeholder="e.g., Moved funds to Aave")
            if st.button("Save", key=f"cmt_save_{idx}"):
                tstamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                if new_text.strip():
                    st.session_state.comments.setdefault(addr, []).append({"ts": tstamp, "text": new_text.strip()})
                    save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)
                    st.success("Saved.")
                    do_rerun()
                else:
                    st.warning("Please type something before saving.")

        if cols[6].button("🗑", key=f"del_{idx}"):
            to_delete_idx = idx

    if to_delete_idx is not None:
        if st.session_state.active_idx == to_delete_idx:
            st.session_state.active_idx = None
        st.session_state.wallets.pop(to_delete_idx)
        save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)
        do_rerun()

    selected_total_placeholder.metric("Selected Total Balance", fmt_usd(selected_total_value))

    # ---- Selected wallet details (appear below the board) ----
    st.markdown("---")
    st.markdown("### Selected Wallet")

    if st.session_state.active_idx is None or st.session_state.active_idx >= len(st.session_state.wallets):
        st.caption("Click a wallet label or address above to view details here.")
    else:
        w = st.session_state.wallets[st.session_state.active_idx]
        header_cols = st.columns([6, 1, 1])
        header_cols[0].markdown(f"**{w['client']} — {w['label']}**  \n`{w['addr']}`")
        if header_cols[1].button("↻ Refresh", key="detail_refresh"):
            st.session_state.refresh_nonce = int(time.time())
            do_rerun()
        if header_cols[2].button("Clear Selection", key="detail_clear"):
            st.session_state.active_idx = None
            save_store(st.session_state.wallets, st.session_state.comments, st.session_state.active_idx)
            do_rerun()

        # Dollar Value
        total = {"total_usd_value": 0}
        if api:
            total = safe_call("Total Balance", api.get_total_balance, w["addr"]) or total
        st.metric("Dollar Value", fmt_usd(total.get("total_usd_value") or total.get("usd_value") or 0))

        # Details: DeFi Positions + Token Holdings
        d1, d2 = st.columns(2)

        with d1:
            st.markdown("**DeFi Positions**")
            positions = safe_call("DeFi positions", api.get_complex_protocol_list, w["addr"]) or []
            st.dataframe(position_rows(positions), use_container_width=True)

        with d2:
            st.markdown("**Token Holdings**")
            tokens = safe_call("Coins in wallet", api.get_all_token_list, w["addr"], True) or []
            st.dataframe(token_rows(tokens)[:25], use_container_width=True)

else:
    if __name__ == "__main__":
        print("Install Streamlit and run:")
        print("  pip install streamlit requests urllib3")
        print("  export DEBANK_API_KEY='YOUR_ACCESSKEY'")
        print("  streamlit run shadow_nav_board.py")
