
# shadow_nav_demo.py
# Minimal Streamlit app showcasing key calls for your Shadow NAV board
import os
import json
from typing import List, Dict, Any, Optional

import streamlit as st
from debank_client import DebankClient, DebankError

st.set_page_config(page_title="Shadow NAV x DeBank", layout="wide")

st.sidebar.header("Setup")
default_key = os.getenv("DEBANK_API_KEY", "")
api_key = st.sidebar.text_input("DEBANK_API_KEY", value=default_key, type="password")
header_name = st.sidebar.text_input("Header Name", value=os.getenv("DEBANK_HEADER_NAME", "AccessKey"))
base_url = st.sidebar.text_input("Base URL", value=os.getenv("DEBANK_BASE_URL", "https://api.cloud.debank.com"))

st.sidebar.divider()
addrs = st.sidebar.text_area("Wallet addresses (one per line)", placeholder="0x123...\n0xabc...")
go = st.sidebar.button("Fetch")

st.title("Shadow NAV Board – DeBank Pro API Demo")
st.caption("Quick demo wired to the exact endpoints your UI needs.")

if go:
    if not api_key:
        st.error("Please provide DEBANK_API_KEY.")
        st.stop()
    api = DebankClient(api_key=api_key, header_name=header_name, base_url=base_url)
    addresses = [a.strip() for a in addrs.splitlines() if a.strip()]
    if not addresses:
        st.warning("Enter at least one address.")
        st.stop()

    tabs = st.tabs(["Overview", "Per Wallet", "Approvals", "Activity", "Curves"])

    with tabs[0]:
        st.subheader("Multi-wallet Overview")
        rows = []
        for addr in addresses:
            try:
                summary = api.summarize_wallet(addr)
                rows.append({
                    "address": addr,
                    "total_usd": summary.get("total_usd"),
                    "chains": len(summary.get("chains") or []),
                    "positions": len(summary.get("positions") or []),
                })
            except DebankError as e:
                rows.append({"address": addr, "total_usd": None, "chains": 0, "positions": 0})
                st.error(f"{addr}: {e}")
        st.dataframe(rows, use_container_width=True)

    with tabs[1]:
        st.subheader("Single Wallet Detail")
        if addresses:
            addr = st.selectbox("Select wallet", options=addresses)
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Total Balance (USD)**")
                st.json(api.get_total_balance(addr))
                st.markdown("**Used Chains**")
                chains = api.get_used_chains(addr)
                st.json(chains)
            with col2:
                st.markdown("**DeFi Positions (cached)**")
                positions = api.get_complex_protocol_list(addr)
                st.json(positions[:5] if positions else [])
                st.caption("Tip: For a clicked protocol row, call `get_protocol_detail(addr, protocol_id)` for live data.")

            st.markdown("---")
            st.markdown("**Coins in Wallet (all chains)**")
            tokens = api.get_all_token_list(addr, is_all=True)
            st.json(tokens[:25] if tokens else [])

    with tabs[2]:
        st.subheader("Approvals / Allowances")
        if addresses:
            addr = st.selectbox("Select wallet for approvals", options=addresses, key="appr_addr")
            chain_id = st.text_input("Chain ID (e.g., eth, op, arb, bsc, polygon)", value="eth")
            if st.button("Load Approvals"):
                st.markdown("**Token approvals**")
                st.json(api.get_token_approvals(addr, chain_id))
                st.markdown("**NFT approvals**")
                st.json(api.get_nft_approvals(addr, chain_id))

    with tabs[3]:
        st.subheader("History / Activity")
        if addresses:
            addr = st.selectbox("Select wallet for history", options=addresses, key="hist_addr")
            chain_id = st.text_input("Chain ID", value="eth", key="hist_chain")
            st.caption("Use start_time (unix seconds) for pagination if needed.")
            if st.button("Load History"):
                st.json(api.get_history_list(addr, chain_id, page_count=50))

    with tabs[4]:
        st.subheader("Curves (sparklines)")
        if addresses:
            addr = st.selectbox("Select wallet for curves", options=addresses, key="curve_addr")
            chain_id = st.text_input("Chain ID", value="eth", key="curve_chain")
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Total Net Curve**")
                st.json(api.get_total_net_curve(addr)[:30])
            with col2:
                st.markdown("**Chain Net Curve**")
                st.json(api.get_chain_net_curve(addr, chain_id)[:30])
