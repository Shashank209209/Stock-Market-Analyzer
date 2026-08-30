"""
Dalal Street AI - Dashboard (Streamlit)

A visual wrapper around stock_analyzer_v01.py's rule-based Technical and
Fundamental agents, plus a backtest module. Run with:

    streamlit run app.py

This file does NOT duplicate any analysis logic - it imports and reuses
every function from stock_analyzer_v01.py, so the terminal script and
this dashboard always stay in sync. If you improve the engine, both
interfaces benefit automatically.
"""

import streamlit as st
import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objects as go

# Import the actual analysis engine - guarded by if __name__ == "__main__"
# in that file, so importing it here does NOT trigger its terminal input
# prompt. We're reusing its functions as a library.
from stock_analyzer_v01 import (
    fetch_data,
    add_indicators,
    summarize_latest,
    get_rule_based_recommendation,
    fetch_fundamentals,
    fetch_piotroski_inputs,
    compute_piotroski_score,
    summarize_fundamentals,
    get_rule_based_fundamental_recommendation,
    YFRateLimitError,
)
from backtest import run_technical_backtest


# ----------------------------------------------------------------------
# Cached data fetchers
#
# Yahoo Finance (via yfinance) rate-limits requests, and this hits harder
# on shared-IP hosting like Streamlit Community Cloud's free tier, where
# many unrelated apps' requests come from the same small pool of outbound
# IPs. Caching each ticker's data for a few minutes means re-running the
# analysis (or another visitor checking the same stock) doesn't trigger a
# fresh Yahoo request every time - directly reducing how often we hit the
# rate limit in the first place. stock_analyzer_v01.py's own retry logic
# then handles the requests that do go through.
# ----------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cached_fetch_data(ticker: str, period: str):
    return fetch_data(ticker, period)


@st.cache_data(ttl=600, show_spinner=False)
def cached_fetch_fundamentals(ticker: str):
    return fetch_fundamentals(ticker)


@st.cache_data(ttl=600, show_spinner=False)
def cached_fetch_piotroski_inputs(ticker: str):
    return fetch_piotroski_inputs(ticker)


@st.cache_data(ttl=600, show_spinner=False)
def cached_run_technical_backtest(ticker: str, period: str):
    return run_technical_backtest(ticker, period)


# ----------------------------------------------------------------------
# Helpers: parsing structured fields out of the agents' text output, and
# mapping decisions to a display color. The rule engines return
# human-readable text (so they still work standalone in the terminal
# script) - these helpers extract the key fields for the dashboard's
# colored cards without changing anything in the engine itself.
# ----------------------------------------------------------------------
def extract_field(text: str, label: str) -> str:
    """Pulls the value after 'LABEL:' from the first matching line."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(label.upper()):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return "N/A"


def decision_style(decision: str):
    """Maps a decision string to a Streamlit alert function + emoji."""
    d = (decision or "").upper()
    if d == "BUY" or "STRONG" in d:
        return st.success, "🟢"
    if "DO NOT BUY" in d or "WEAK" in d:
        return st.error, "🔴"
    return st.warning, "🟡"  # WAIT, MIXED, INSUFFICIENT DATA, N/A


# ----------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------
st.set_page_config(page_title="Dalal Street AI", page_icon="📈", layout="wide")

# ----------------------------------------------------------------------
# Theme
#
# Color language is deliberate, not decorative: GOLD marks anything
# rule-based / computed from real fetched data (Technical Agent,
# Fundamental Agent, Backtest) - evoking a ticker/trading-floor feel.
# (The violet accent class below is unused for now - kept in the CSS in
# case an AI-reasoning section is reintroduced later.)
#
# NOTE: this styles Streamlit's internal component classes (data-testid
# selectors), which can shift slightly between Streamlit versions. If an
# upgrade changes how something looks, that's why - these selectors may
# need small updates.
# ----------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

:root {
    --bg: #06090d;
    --surface: rgba(255,255,255,0.045);
    --border: rgba(255,255,255,0.09);
    --text: #f2f4f6;
    --muted: #8b95a3;
    --gold: #e0b64a;
    --gold-dim: rgba(224,182,74,0.35);
    --violet: #8b7cf6;
    --violet-dim: rgba(139,124,246,0.35);
}

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: var(--bg); color: var(--text); }
.block-container { padding-top: 1.5rem; }

/* Sharp corners everywhere, per the fintech/terminal aesthetic direction */
.stButton>button, [data-testid="stMetric"], [data-testid="stExpander"],
[data-testid="stAlert"], .stTextInput input, .stSelectbox div[data-baseweb="select"],
[data-testid="stDataFrame"] { border-radius: 2px !important; }

/* ---------------- Hero ---------------- */
.hero {
    position: relative;
    background: linear-gradient(180deg, #0a0f16 0%, #06090d 100%);
    border: 1px solid var(--border);
    padding: clamp(32px, 5vw, 56px) clamp(24px, 4vw, 48px);
    margin-bottom: 28px;
    overflow: hidden;
}
.hero__candles { position: absolute; inset: 0; opacity: 0.22; z-index: 0;
    animation: driftCandles 40s linear infinite; }
@keyframes driftCandles { from { transform: translateX(0); } to { transform: translateX(-120px); } }

.hero__content { position: relative; z-index: 1; }

.hero__badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--surface); border: 1px solid var(--gold-dim);
    backdrop-filter: blur(10px); padding: 7px 16px;
    font-size: 13px; font-weight: 500; color: var(--text);
    letter-spacing: -0.01em;
    animation: riseIn 0.7s cubic-bezier(0.16,1,0.3,1) backwards;
    animation-delay: 0.05s;
}
.hero__badge-dot { width: 6px; height: 6px; background: var(--gold); }

.hero__headline {
    font-size: clamp(2.2rem, 4.2vw, 3.4rem);
    font-weight: 700; line-height: 1.12; letter-spacing: -0.03em;
    margin: 20px 0 0 0; color: var(--text);
}
.hero__headline .rise { display: block; overflow: hidden; }
.hero__headline .rise span {
    display: inline-block; animation: riseIn 0.8s cubic-bezier(0.16,1,0.3,1) backwards;
}
.hero__headline .rise:nth-child(1) span { animation-delay: 0.15s; }
.hero__headline .rise:nth-child(2) span { animation-delay: 0.28s; }
.hero__accent { color: var(--gold); }

@keyframes riseIn { from { opacity: 0; transform: translate3d(0, 60%, 0); } to { opacity: 1; transform: translate3d(0,0,0); } }

.hero__sub {
    max-width: 640px; margin-top: 18px; color: var(--muted);
    font-size: clamp(15px, 1.3vw, 17px); font-weight: 300; line-height: 1.55;
    animation: riseIn 0.8s cubic-bezier(0.16,1,0.3,1) backwards; animation-delay: 0.42s;
}

/* Sidebar - darker panel, glass border */
[data-testid="stSidebar"] { background: #0d1219; border-right: 1px solid var(--border); }

/* Metric cards -> glass tiles with a thin gold accent (verified/computed data) */
[data-testid="stMetric"] {
    background: var(--surface); border: 1px solid var(--border);
    border-left: 3px solid var(--gold); backdrop-filter: blur(12px); padding: 14px 16px;
}
[data-testid="stMetricLabel"] { color: var(--muted) !important; font-weight: 500; }

.stButton>button[kind="primary"] { background: var(--gold); color: #14110a; font-weight: 600; border: none; }
.stButton>button[kind="primary"]:hover { background: #f0c95f; }

[data-testid="stAlert"] { background: var(--surface); backdrop-filter: blur(10px); border: 1px solid var(--border); }
[data-testid="stExpander"] { background: var(--surface); border: 1px solid var(--border); }

.section-accent { height: 3px; width: 42px; margin: 22px 0 6px 0; }
.section-accent.gold { background: var(--gold); }
.section-accent.violet { background: var(--violet); }

@media (prefers-reduced-motion: reduce) {
    .hero__candles, .hero__badge, .hero__headline .rise span, .hero__sub, h1 { animation: none !important; opacity: 1 !important; transform: none !important; }
}
</style>

<div class="hero">
    <svg class="hero__candles" viewBox="0 0 900 200" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <g stroke="#e0b64a" fill="none">
            <line x1="20" y1="60" x2="20" y2="150" stroke-width="1.5"/><rect x="14" y="90" width="12" height="40" fill="#e0b64a"/>
            <line x1="55" y1="40" x2="55" y2="120" stroke-width="1.5"/><rect x="49" y="55" width="12" height="35" fill="#e0b64a"/>
            <line x1="90" y1="80" x2="90" y2="170" stroke-width="1.5"/><rect x="84" y="100" width="12" height="45" fill="none" stroke="#e0b64a"/>
            <line x1="125" y1="30" x2="125" y2="110" stroke-width="1.5"/><rect x="119" y="50" width="12" height="30" fill="#e0b64a"/>
            <line x1="160" y1="70" x2="160" y2="160" stroke-width="1.5"/><rect x="154" y="95" width="12" height="42" fill="none" stroke="#e0b64a"/>
            <line x1="195" y1="20" x2="195" y2="100" stroke-width="1.5"/><rect x="189" y="35" width="12" height="38" fill="#e0b64a"/>
            <line x1="230" y1="55" x2="230" y2="140" stroke-width="1.5"/><rect x="224" y="75" width="12" height="40" fill="none" stroke="#e0b64a"/>
            <line x1="265" y1="45" x2="265" y2="130" stroke-width="1.5"/><rect x="259" y="60" width="12" height="36" fill="#e0b64a"/>
            <line x1="300" y1="15" x2="300" y2="95" stroke-width="1.5"/><rect x="294" y="30" width="12" height="32" fill="#e0b64a"/>
            <line x1="335" y1="60" x2="335" y2="150" stroke-width="1.5"/><rect x="329" y="85" width="12" height="40" fill="none" stroke="#e0b64a"/>
            <line x1="370" y1="35" x2="370" y2="115" stroke-width="1.5"/><rect x="364" y="55" width="12" height="34" fill="#e0b64a"/>
            <line x1="405" y1="10" x2="405" y2="85" stroke-width="1.5"/><rect x="399" y="25" width="12" height="30" fill="#e0b64a"/>
            <line x1="440" y1="50" x2="440" y2="135" stroke-width="1.5"/><rect x="434" y="70" width="12" height="38" fill="none" stroke="#e0b64a"/>
            <line x1="475" y1="25" x2="475" y2="105" stroke-width="1.5"/><rect x="469" y="45" width="12" height="32" fill="#e0b64a"/>
        </g>
    </svg>
    <div class="hero__content">
        <div class="hero__badge"><span class="hero__badge-dot"></span>RULE-BASED + AI EQUITY RESEARCH</div>
        <h1 class="hero__headline">
            <span class="rise"><span>Real data first.</span></span>
            <span class="rise"><span class="hero__accent">AI reasoning second.</span></span>
        </h1>
        <p class="hero__sub">
            Dalal Street AI fetches live NSE price and financial data, scores it with
            transparent, auditable rules, and only then brings in an LLM to weigh the
            evidence — never to guess the numbers.
        </p>
    </div>
</div>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# Sidebar controls
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    raw_ticker = st.text_input("NSE ticker symbol", placeholder="e.g. TCS, INFY, RELIANCE").strip().upper()
    period = st.selectbox("History period", ["6mo", "1y", "2y"], index=1)

    st.divider()
    enable_backtest = st.checkbox("Include Technical Agent backtest", value=True)
    backtest_period = st.selectbox("Backtest period", ["6mo", "1y", "2y"], index=1) if enable_backtest else "1y"

    analyze_clicked = st.button("Analyze", type="primary", use_container_width=True)


# ----------------------------------------------------------------------
# Main analysis flow
# ----------------------------------------------------------------------
if analyze_clicked:
    if not raw_ticker:
        st.warning("Enter a ticker symbol in the sidebar first.")
        st.stop()

    if " " in raw_ticker:
        st.error(
            f"'{raw_ticker}' looks like a company name, not a ticker symbol. "
            f"Look up the exact NSE symbol (e.g. on nseindia.com) - "
            f"for example 'Reliance Power' → RPOWER."
        )
        st.stop()

    ticker = raw_ticker if raw_ticker.endswith((".NS", ".BO")) else raw_ticker + ".NS"

    try:
        # --- Fetch price data ---
        with st.spinner(f"Fetching price data for {ticker}..."):
            data = cached_fetch_data(ticker, period)

        if data.empty:
            st.error(f"⚠️ Yahoo Finance did not return data for {ticker}.")
            st.info("Yahoo Finance may be temporarily rate-limiting this app. " "Please wait a few minutes and try again.")
            st.stop()

        data = add_indicators(data)
        technical_summary = summarize_latest(data, ticker)
        technical_recommendation = get_rule_based_recommendation(data, ticker)

        # --- Fetch fundamentals + Piotroski ---
        with st.spinner("Fetching fundamentals and computing Piotroski F-Score..."):
            fundamentals = cached_fetch_fundamentals(ticker)
            piotroski_inputs = cached_fetch_piotroski_inputs(ticker)
            fundamentals["piotroski"] = compute_piotroski_score(piotroski_inputs)

        fundamentals_summary = summarize_fundamentals(fundamentals, ticker)
        fundamental_recommendation = get_rule_based_fundamental_recommendation(fundamentals, ticker)

    except YFRateLimitError:
        st.error(
            "⚠️ Yahoo Finance is rate-limiting requests from this server right now. "
            "This is a known issue on shared cloud hosting (many free-tier apps share "
            "the same outbound IPs) and is usually temporary."
        )
        st.info(
            "Try again in a minute or two. If you're testing locally instead of on "
            "Streamlit Cloud, this happens far less often since you're not sharing "
            "an IP with many other apps."
        )
        st.stop()

    st.success(f"Analysis complete for **{ticker}**")

    # ------------------------------------------------------------------
    # Price + indicator charts
    # ------------------------------------------------------------------
    st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
    st.subheader("Price & Technical Indicators")

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.25, 0.25],
        vertical_spacing=0.04,
        subplot_titles=("Price with SMA20 / EMA20", "RSI (14)", "MACD"),
    )

    fig.add_trace(go.Scatter(x=data.index, y=data["Close"], name="Close", line=dict(color="#2563eb")), row=1, col=1)
    fig.add_trace(go.Scatter(x=data.index, y=data["SMA20"], name="SMA20", line=dict(color="#f59e0b", dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=data.index, y=data["EMA20"], name="EMA20", line=dict(color="#10b981", dash="dot")), row=1, col=1)

    fig.add_trace(go.Scatter(x=data.index, y=data["RSI14"], name="RSI14", line=dict(color="#8b5cf6")), row=2, col=1)
    fig.add_hline(y=70, line=dict(color="red", dash="dash", width=1), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="green", dash="dash", width=1), row=2, col=1)

    fig.add_trace(go.Scatter(x=data.index, y=data["MACD"], name="MACD", line=dict(color="#2563eb")), row=3, col=1)
    fig.add_trace(go.Scatter(x=data.index, y=data["MACD_Signal"], name="Signal", line=dict(color="#f59e0b")), row=3, col=1)

    fig.update_layout(height=700, showlegend=True, margin=dict(t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Latest snapshot - the exact numbers behind the chart above, shown
    # as quick-glance metric tiles so a user doesn't have to eyeball
    # values off the chart if they want the precise figures.
    # ------------------------------------------------------------------
    st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
    st.subheader("Latest Snapshot")
    latest_row = data.iloc[-1]

    snap1, snap2, snap3, snap4 = st.columns(4)
    snap1.metric("Close Price", f"₹{latest_row['Close']:.2f}")
    snap2.metric("SMA20", f"₹{latest_row['SMA20']:.2f}")
    snap3.metric("EMA20", f"₹{latest_row['EMA20']:.2f}")
    snap4.metric("RSI14", f"{latest_row['RSI14']:.2f}")

    snap5, snap6, _, _ = st.columns(4)
    snap5.metric("MACD", f"{latest_row['MACD']:.2f}")
    snap6.metric("MACD Signal", f"{latest_row['MACD_Signal']:.2f}")

    st.caption(f"As of {data.index[-1].strftime('%d %b %Y')} · {ticker}")

    # ------------------------------------------------------------------
    # Technical Agent card
    # ------------------------------------------------------------------
    st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
    st.subheader("Technical Agent (Rule-Based)")
    tech_decision = extract_field(technical_recommendation, "DECISION:")
    tech_confidence = extract_field(technical_recommendation, "CONFIDENCE:")
    alert_fn, emoji = decision_style(tech_decision)
    alert_fn(f"{emoji} **{tech_decision}**  •  Confidence: {tech_confidence}")
    with st.expander("Full reasoning"):
        st.text(technical_recommendation)

    # ------------------------------------------------------------------
    # Fundamentals table + Piotroski + Fundamental Agent card
    # ------------------------------------------------------------------
    st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
    st.subheader("Fundamentals")

    def fmt_pct(v):
        return f"{v * 100:.1f}%" if v is not None else "Not available"

    def fmt_num(v):
        return f"{v:.2f}" if v is not None else "Not available"

    fundamentals_table = pd.DataFrame({
        "Metric": [
            "Sector", "Industry", "Trailing P/E", "Forward P/E", "PEG Ratio",
            "ROE", "Debt/Equity", "Revenue Growth (YoY)", "Profit Margin",
            "Insider/Promoter Holding (approx)",
        ],
        "Value": [
            fundamentals.get("sector") or "Not available",
            fundamentals.get("industry") or "Not available",
            fmt_num(fundamentals.get("pe_ratio")),
            fmt_num(fundamentals.get("forward_pe")),
            fmt_num(fundamentals.get("peg_ratio")),
            fmt_pct(fundamentals.get("roe")),
            fmt_num(fundamentals.get("debt_to_equity")),
            fmt_pct(fundamentals.get("revenue_growth")),
            fmt_pct(fundamentals.get("profit_margin")),
            fmt_pct(fundamentals.get("promoter_or_insider_holding")),
        ],
    })
    st.dataframe(fundamentals_table, hide_index=True, use_container_width=True)

    piotroski = fundamentals["piotroski"]
    col1, col2 = st.columns([1, 3])
    with col1:
        if piotroski["max_possible"] > 0:
            st.metric("Piotroski F-Score", f"{piotroski['score']} / {piotroski['max_possible']}")
        else:
            st.metric("Piotroski F-Score", "N/A")
    with col2:
        with st.expander("Piotroski F-Score breakdown"):
            if piotroski["reasons"]:
                for reason in piotroski["reasons"]:
                    st.markdown(f"- {reason}")
            else:
                st.write("Insufficient financial statement history to compute this.")

    st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
    st.subheader("Fundamental Agent (Rule-Based)")
    fund_decision = extract_field(fundamental_recommendation, "DECISION:")
    fund_confidence = extract_field(fundamental_recommendation, "CONFIDENCE:")
    alert_fn, emoji = decision_style(fund_decision)
    alert_fn(f"{emoji} **{fund_decision}**  •  Confidence: {fund_confidence}")
    with st.expander("Full reasoning"):
        st.text(fundamental_recommendation)

    # ------------------------------------------------------------------
    # Backtest (optional) - reuses the SAME ticker already entered above,
    # so there's no separate command or re-entering anything. It replays
    # the Technical Agent's exact rules through historical data and
    # compares the result to simply buying and holding.
    # ------------------------------------------------------------------
    if enable_backtest:
        st.markdown('<div class="section-accent gold"></div>', unsafe_allow_html=True)
        st.subheader("Technical Agent Backtest")
        st.caption(
            f"Simulates following the Technical Agent's BUY/WAIT/DO NOT BUY "
            f"signals day-by-day over the past {backtest_period}, compared "
            f"to simply buying and holding."
        )
        with st.spinner(f"Running backtest over {backtest_period}..."):
            try:
                bt = cached_run_technical_backtest(ticker, backtest_period)

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Strategy Return", f"{bt['strategy_return_pct']:+.2f}%")
                col2.metric("Buy & Hold Return", f"{bt['buy_hold_return_pct']:+.2f}%")
                col3.metric("Win Rate", f"{bt['win_rate_pct']:.1f}%" if bt["win_rate_pct"] is not None else "N/A")
                col4.metric("Max Drawdown", f"{bt['max_drawdown_pct']:.2f}%")

                diff = bt["strategy_return_pct"] - bt["buy_hold_return_pct"]
                if diff > 0:
                    st.success(f"Strategy outperformed buy-and-hold by {diff:.2f} percentage points over this period.")
                else:
                    st.warning(f"Strategy underperformed buy-and-hold by {abs(diff):.2f} percentage points over this period.")

                # Equity curve: strategy value over time vs. a simple
                # buy-and-hold line starting from the same capital.
                equity_df = bt["equity_curve"]
                buy_hold_curve = bt["starting_capital"] * (data.loc[equity_df.index, "Close"] / data.loc[equity_df.index, "Close"].iloc[0])

                eq_fig = go.Figure()
                eq_fig.add_trace(go.Scatter(x=equity_df.index, y=equity_df["value"], name="Technical Agent Strategy", line=dict(color="#2563eb")))
                eq_fig.add_trace(go.Scatter(x=equity_df.index, y=buy_hold_curve, name="Buy & Hold", line=dict(color="#94a3b8", dash="dot")))
                eq_fig.update_layout(height=350, margin=dict(t=20, b=20), yaxis_title="Portfolio Value (Rs)")
                st.plotly_chart(eq_fig, use_container_width=True)

                with st.expander(f"Trade log ({bt['num_trades']} completed trades)"):
                    if bt["trades"]:
                        trade_log_df = pd.DataFrame(bt["trades"])
                        trade_log_df["return_pct"] = trade_log_df["return_pct"].map(lambda x: f"{x:+.2f}%")
                        st.dataframe(trade_log_df, hide_index=True, use_container_width=True)
                    else:
                        st.write("No completed trades in this period.")
                    if bt["open_position"]:
                        op = bt["open_position"]
                        st.caption(
                            f"Still holding a position entered {op['entry_date'].date()} at "
                            f"Rs{op['entry_price']:.2f} (unrealized: {op['unrealized_return_pct']:+.2f}%)"
                        )

                st.caption(
                    "⚠️ Backtest ignores brokerage fees, taxes, and slippage, and assumes "
                    "trades execute exactly at the closing price. Past performance does not "
                    "guarantee future results."
                )
            except YFRateLimitError:
                st.warning(
                    "⚠️ Yahoo Finance rate-limited the backtest's data request. "
                    "This is usually temporary - try again in a minute."
                )
            except ValueError as e:
                st.warning(f"Could not run backtest: {e}")

    st.divider()
    st.caption(
        "⚠️ Educational project only. Technical and fundamental scoring here is "
        "simplified and does not account for sector context, macro conditions, "
        "management commentary, or market-wide risk. Not financial advice."
    )
else:
    st.info("Enter a ticker in the sidebar and click **Analyze** to get started.")
