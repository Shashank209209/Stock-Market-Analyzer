"""
Dalal Street AI - v0.1
A minimal end-to-end slice: fetch data -> compute indicators -> AI recommendation.

This is NOT the full product from the spec. It's step 1 of many.
Run it with: python stock_analyzer_v01.py
"""

import time
import yfinance as yf
import pandas as pd
from anthropic import Anthropic

try:
    from yfinance.exceptions import YFRateLimitError
except ImportError:
    # Older yfinance versions may not expose this exception class - fall
    # back to a placeholder that will simply never match, so the retry
    # helper below still works (just without special-casing rate limits).
    class YFRateLimitError(Exception):
        pass


def _fetch_with_retry(fetch_fn, max_retries: int = 3, base_delay: float = 2.0):
    """
    Retries a yfinance call with exponential backoff specifically on
    YFRateLimitError - Yahoo Finance's undocumented API rate-limits
    requests, and this happens especially often on shared cloud IPs
    (e.g. Streamlit Community Cloud's free tier shares outbound IPs
    across many apps). A short wait-and-retry often succeeds since the
    limit is usually temporary and per-time-window, not permanent.

    Other exceptions (bad ticker, network errors) are NOT retried - they
    propagate immediately, since retrying won't fix them.
    """
    for attempt in range(max_retries):
        try:
            return fetch_fn()
        except YFRateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))  # 2s, 4s, 8s...

# ----------------------------------------------------------------------
# AI Agent setup
# ----------------------------------------------------------------------
# Paste your key here once you have one (console.anthropic.com).
# Leave it as the placeholder if you don't have one yet - the script will
# automatically skip the AI agent and only show the rule-based result.
ANTHROPIC_API_KEY = "sk-ant-PASTE-YOUR-KEY-HERE"

AI_AGENT_ENABLED = not ANTHROPIC_API_KEY.startswith("sk-ant-PASTE")
client = Anthropic(api_key=ANTHROPIC_API_KEY) if AI_AGENT_ENABLED else None

# ----------------------------------------------------------------------
# STEP 1: Configuration
# ----------------------------------------------------------------------
# TICKER is no longer hardcoded here - the user is asked for it when the
# script runs (see get_user_ticker() below and the __main__ block).
PERIOD = "1y"   # how much history to pull: "6mo", "1y", "2y", etc.



# ----------------------------------------------------------------------
# STEP 2: Data Layer -- fetch OHLCV data
# ----------------------------------------------------------------------
def fetch_data(ticker: str, period: str) -> pd.DataFrame:
    """
    Fetch stock data from Yahoo Finance with retries.
    Handles temporary empty responses from Yahoo Finance.
    """

    last_df = pd.DataFrame()

    for attempt in range(3):
        try:
            df = yf.download(
                ticker,
                period=period,
                progress=False,
                auto_adjust=False
            )

            if df is not None and not df.empty:
                # Handle newer yfinance MultiIndex format
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df.dropna(inplace=True)

                if not df.empty:
                    return df

            last_df = df if df is not None else pd.DataFrame()

            # Yahoo returned nothing — wait before trying again
            time.sleep(2 ** attempt)

        except Exception as e:
            if attempt == 2:
                raise

            time.sleep(2 ** attempt)

    return last_df


def validate_data(df: pd.DataFrame, ticker: str) -> None:
    """
    Fails fast with a clear message if the ticker didn't return usable
    data - e.g. wrong symbol, delisted stock, or a typo. Without this
    check, a bad ticker crashes later inside add_indicators() with a
    confusing Pandas error instead of telling you what actually went wrong.
    """
    if df.empty:
        raise SystemExit(
            f"\nNo data found for '{ticker}'. This usually means the "
            f"ticker symbol is wrong or doesn't exist on NSE/BSE. "
            f"Double-check the exact symbol (e.g. on nseindia.com) and "
            f"try again - for example RELIANCE, TCS, INFY, RPOWER."
        )


def fetch_fundamentals(ticker: str) -> dict:
    """
    Pulls real fundamental ratios from yfinance's .info dict.

    IMPORTANT: .info coverage for NSE stocks is inconsistent - some fields
    are missing for some companies. We use .get() with None defaults so a
    missing field doesn't crash the script; downstream code must handle
    None values explicitly rather than guessing a number.
    """
    stock = yf.Ticker(ticker)
    info = _fetch_with_retry(lambda: stock.info)

    # yfinance's .info property sometimes silently returns None (or
    # something that isn't a dict) instead of raising a catchable
    # exception - this happens intermittently when Yahoo Finance's
    # undocumented API has a transient hiccup (rate limiting, timeout,
    # unexpected response shape). Since no exception was thrown, our
    # retry logic above never triggers for this specific failure mode.
    # Treating it as "no data available" here means the rest of this
    # function's info.get(...) calls degrade gracefully (returning None
    # for every field) instead of crashing with an AttributeError.
    if not isinstance(info, dict):
        info = {}

    return {
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "peg_ratio": info.get("pegRatio"),
        "roe": info.get("returnOnEquity"),          # as a decimal, e.g. 0.18 = 18%
        "debt_to_equity": info.get("debtToEquity"), # yfinance reports this as a %, e.g. 45.2
        "revenue_growth": info.get("revenueGrowth"),  # decimal, e.g. 0.12 = 12%
        "profit_margin": info.get("profitMargins"),   # decimal
        "promoter_or_insider_holding": info.get("heldPercentInsiders"),  # decimal, rough proxy
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }


def validate_fundamentals(fundamentals: dict, ticker: str) -> None:
    """
    Warns (doesn't crash) if most fundamental fields came back empty.
    This happens for some NSE small-caps where yfinance's data coverage
    is thin. We still proceed with whatever fields ARE available, but
    the user should know the fundamental picture may be incomplete.
    """
    available = sum(1 for v in fundamentals.values() if v is not None)
    total = len(fundamentals)
    if available <= 3:
        print(f"  Warning: yfinance returned limited fundamental data for "
              f"{ticker} ({available}/{total} fields available). The "
              f"Fundamental Agent's output below may be sparse.")



def _find_row(statement: pd.DataFrame, candidates: list):
    """
    yfinance labels financial statement rows inconsistently across
    versions/companies (e.g. 'Net Income' vs 'NetIncome'). This helper
    tries a list of possible row names and returns the first match found,
    or None if none exist - so missing line items degrade gracefully
    instead of crashing with a KeyError.
    """
    if statement is None or statement.empty:
        return None
    for name in candidates:
        if name in statement.index:
            return statement.loc[name]
    return None


def fetch_piotroski_inputs(ticker: str) -> dict:
    """
    Fetches the raw financial statement line items needed for the
    Piotroski F-Score, across the two most recent annual periods (needed
    to compute year-over-year changes like 'did ROA improve').

    Returns a dict of pandas Series (most recent period first) or None
    for any line item yfinance doesn't provide for this stock. NSE
    coverage for full financial statements can be thin, especially for
    small/mid-caps - this is a known, flagged limitation, not a bug.
    """
    stock = yf.Ticker(ticker)

    income = _fetch_with_retry(lambda: stock.financials)        # annual income statement
    balance = _fetch_with_retry(lambda: stock.balance_sheet)     # annual balance sheet
    cashflow = _fetch_with_retry(lambda: stock.cashflow)         # annual cash flow statement

    return {
        "net_income": _find_row(income, ["Net Income", "NetIncome", "Net Income Common Stockholders"]),
        "total_revenue": _find_row(income, ["Total Revenue", "TotalRevenue"]),
        "gross_profit": _find_row(income, ["Gross Profit", "GrossProfit"]),
        "total_assets": _find_row(balance, ["Total Assets", "TotalAssets"]),
        "current_assets": _find_row(balance, ["Current Assets", "CurrentAssets", "Total Current Assets"]),
        "current_liabilities": _find_row(balance, ["Current Liabilities", "CurrentLiabilities", "Total Current Liabilities"]),
        "long_term_debt": _find_row(balance, ["Long Term Debt", "LongTermDebt"]),
        "shares_outstanding": _find_row(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"]),
        "operating_cashflow": _find_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"]),
    }


def compute_piotroski_score(inputs: dict) -> dict:
    """
    Computes the Piotroski F-Score: a 9-point checklist (Piotroski, 2000)
    across Profitability, Leverage/Liquidity, and Operating Efficiency.
    Each criterion scores 1 point if met, 0 if not - but ONLY if the
    underlying data for that specific check is actually available. Missing
    data means that criterion is excluded from both the score AND the max
    possible score, not silently treated as a 0 (which would unfairly
    penalize the stock for a data gap, not a real financial weakness).

    Needs at least 2 annual periods of data (current year vs prior year)
    to compute the year-over-year checks. If fewer than 2 periods are
    available, returns a score of None with an explanation.
    """
    reasons = []
    score = 0
    max_possible = 0

    def has_two_periods(series):
        return series is not None and len(series) >= 2 and pd.notna(series.iloc[0]) and pd.notna(series.iloc[1])

    def has_one_period(series):
        return series is not None and len(series) >= 1 and pd.notna(series.iloc[0])

    ni = inputs["net_income"]
    ta = inputs["total_assets"]
    ocf = inputs["operating_cashflow"]
    ca = inputs["current_assets"]
    cl = inputs["current_liabilities"]
    ltd = inputs["long_term_debt"]
    shares = inputs["shares_outstanding"]
    revenue = inputs["total_revenue"]
    gross_profit = inputs["gross_profit"]

    # --- Criterion 1: Positive Net Income (profitability) ---
    if has_one_period(ni):
        max_possible += 1
        if ni.iloc[0] > 0:
            score += 1
            reasons.append("Positive net income (profitable).")
        else:
            reasons.append("Negative net income (unprofitable).")

    # --- Criterion 2: Positive Operating Cash Flow ---
    if has_one_period(ocf):
        max_possible += 1
        if ocf.iloc[0] > 0:
            score += 1
            reasons.append("Positive operating cash flow.")
        else:
            reasons.append("Negative operating cash flow.")

    # --- Criterion 3: ROA improved vs prior year ---
    if has_two_periods(ni) and has_two_periods(ta):
        max_possible += 1
        roa_now = ni.iloc[0] / ta.iloc[0]
        roa_prior = ni.iloc[1] / ta.iloc[1]
        if roa_now > roa_prior:
            score += 1
            reasons.append(f"ROA improved year-over-year ({roa_now*100:.1f}% vs {roa_prior*100:.1f}%).")
        else:
            reasons.append(f"ROA declined year-over-year ({roa_now*100:.1f}% vs {roa_prior*100:.1f}%).")

    # --- Criterion 4: Cash flow quality (OCF > Net Income = real earnings, not just accounting) ---
    if has_one_period(ocf) and has_one_period(ni):
        max_possible += 1
        if ocf.iloc[0] > ni.iloc[0]:
            score += 1
            reasons.append("Operating cash flow exceeds net income (high earnings quality).")
        else:
            reasons.append("Net income exceeds operating cash flow (possible earnings quality concern).")

    # --- Criterion 5: Leverage decreased (long-term debt ratio fell) ---
    if has_two_periods(ltd) and has_two_periods(ta):
        max_possible += 1
        debt_ratio_now = ltd.iloc[0] / ta.iloc[0]
        debt_ratio_prior = ltd.iloc[1] / ta.iloc[1]
        if debt_ratio_now < debt_ratio_prior:
            score += 1
            reasons.append("Long-term debt ratio decreased (deleveraging).")
        else:
            reasons.append("Long-term debt ratio increased or flat (leverage rising).")

    # --- Criterion 6: Liquidity improved (current ratio rose) ---
    if has_two_periods(ca) and has_two_periods(cl):
        max_possible += 1
        current_ratio_now = ca.iloc[0] / cl.iloc[0]
        current_ratio_prior = ca.iloc[1] / cl.iloc[1]
        if current_ratio_now > current_ratio_prior:
            score += 1
            reasons.append("Current ratio improved (better short-term liquidity).")
        else:
            reasons.append("Current ratio declined (weaker short-term liquidity).")

    # --- Criterion 7: No new share dilution ---
    if has_two_periods(shares):
        max_possible += 1
        if shares.iloc[0] <= shares.iloc[1]:
            score += 1
            reasons.append("No new share dilution.")
        else:
            reasons.append("Shares outstanding increased (existing holders diluted).")

    # --- Criterion 8: Gross margin improved ---
    if has_two_periods(gross_profit) and has_two_periods(revenue):
        max_possible += 1
        margin_now = gross_profit.iloc[0] / revenue.iloc[0]
        margin_prior = gross_profit.iloc[1] / revenue.iloc[1]
        if margin_now > margin_prior:
            score += 1
            reasons.append(f"Gross margin improved ({margin_now*100:.1f}% vs {margin_prior*100:.1f}%).")
        else:
            reasons.append(f"Gross margin declined ({margin_now*100:.1f}% vs {margin_prior*100:.1f}%).")

    # --- Criterion 9: Asset turnover improved (efficiency) ---
    if has_two_periods(revenue) and has_two_periods(ta):
        max_possible += 1
        turnover_now = revenue.iloc[0] / ta.iloc[0]
        turnover_prior = revenue.iloc[1] / ta.iloc[1]
        if turnover_now > turnover_prior:
            score += 1
            reasons.append("Asset turnover improved (more efficient use of assets).")
        else:
            reasons.append("Asset turnover declined (less efficient use of assets).")

    return {
        "score": score,
        "max_possible": max_possible,
        "reasons": reasons,
    }


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds SMA, EMA, RSI, and MACD columns to the DataFrame.
    Doing these manually (not via ta-lib) so you understand the math.
    """

    # --- Simple Moving Average (SMA): plain average of last N closes ---
    # .rolling(20) creates a "window" of the last 20 rows at each point,
    # .mean() averages them. This is the core Pandas pattern for indicators.
    df["SMA20"] = df["Close"].rolling(window=20).mean()

    # --- Exponential Moving Average (EMA): weights recent prices more ---
    # .ewm() = exponentially weighted mean. span=20 controls how much
    # weight decays going back in time.
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()

    # --- RSI (Relative Strength Index): momentum oscillator, 0-100 ---
    # Logic: average gain vs average loss over 14 days.
    delta = df["Close"].diff()  # day-over-day price change
    gain = delta.clip(lower=0)  # keep only positive changes, zero out the rest
    loss = -delta.clip(upper=0)  # keep only negative changes (as positive numbers)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df["RSI14"] = 100 - (100 / (1 + rs))

    # --- MACD (Moving Average Convergence Divergence) ---
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    return df


def summarize_latest(df: pd.DataFrame, ticker: str) -> str:
    """
    The AI agent doesn't need the whole DataFrame - just the latest
    snapshot in plain language. This is the 'prompt data' step.
    """
    latest = df.iloc[-1]  # last row = most recent trading day

    summary = f"""
Stock: {ticker}
Latest Close Price: {latest['Close']:.2f}
SMA20: {latest['SMA20']:.2f}
EMA20: {latest['EMA20']:.2f}
RSI14: {latest['RSI14']:.2f}
MACD: {latest['MACD']:.2f}
MACD Signal Line: {latest['MACD_Signal']:.2f}
"""
    return summary


def summarize_fundamentals(fundamentals: dict, ticker: str) -> str:
    """
    Turns the fundamentals dict into plain text for the AI agent - same
    pattern as summarize_latest() for technicals. Missing fields are
    shown as 'Not available' rather than silently omitted, so the AI
    agent knows explicitly what it does and doesn't have.
    """
    def fmt(key, as_percent=False, suffix=""):
        val = fundamentals.get(key)
        if val is None:
            return "Not available"
        if as_percent:
            return f"{val * 100:.1f}%"
        return f"{val:.2f}{suffix}"

    return f"""
Stock: {ticker}
Sector: {fundamentals.get('sector') or 'Not available'}
Industry: {fundamentals.get('industry') or 'Not available'}
Trailing P/E: {fmt('pe_ratio')}
Forward P/E: {fmt('forward_pe')}
PEG Ratio: {fmt('peg_ratio')}
Return on Equity (ROE): {fmt('roe', as_percent=True)}
Debt to Equity: {fmt('debt_to_equity')}
Revenue Growth (YoY): {fmt('revenue_growth', as_percent=True)}
Profit Margin: {fmt('profit_margin', as_percent=True)}
Insider/Promoter Holding (approx): {fmt('promoter_or_insider_holding', as_percent=True)}

Piotroski F-Score: {_format_piotroski(fundamentals.get('piotroski'))}
"""


def _format_piotroski(piotroski: dict) -> str:
    """Formats the Piotroski result dict into a short readable line + breakdown."""
    if piotroski is None:
        return "Not available"
    score = piotroski["score"]
    max_possible = piotroski["max_possible"]
    if max_possible == 0:
        return "Not available (insufficient financial statement history)"
    lines = [f"{score}/{max_possible}"]
    for reason in piotroski["reasons"]:
        lines.append(f"    - {reason}")
    return "\n".join(lines)


def get_rule_based_fundamental_recommendation(fundamentals: dict, ticker: str) -> str:
    """
    Rule-based 'Fundamental Agent' - Fundamentals + Valuation Reality
    framing (borrowed structure from the deep-dive prompt, but every line
    here is backed by a real fetched number, not fabricated qualitative
    judgment like management tone).

    Same scoring pattern as the Technical Agent: each rule casts a vote
    ONLY if the underlying data is actually available. Missing data does
    not silently count as neutral - it's excluded from scoring entirely
    and flagged, so the confidence level honestly reflects data coverage.
    """
    score = 0
    max_possible = 0   # tracks how many rules actually had data to fire
    reasons = []

    pe = fundamentals.get("pe_ratio")
    roe = fundamentals.get("roe")
    de = fundamentals.get("debt_to_equity")
    rev_growth = fundamentals.get("revenue_growth")
    margin = fundamentals.get("profit_margin")

    # --- Valuation Reality: P/E sanity check ---
    if pe is not None:
        max_possible += 1
        if pe < 15:
            score += 1
            reasons.append(f"P/E ({pe:.1f}) is relatively low - may indicate undervaluation or low growth expectations.")
        elif pe > 40:
            score -= 1
            reasons.append(f"P/E ({pe:.1f}) is high - market is pricing in significant future growth; downside risk if growth disappoints.")
        else:
            reasons.append(f"P/E ({pe:.1f}) is in a moderate range.")
    else:
        reasons.append("P/E: Not available - skipped in scoring.")

    # --- Fundamentals: profitability quality (ROE) ---
    if roe is not None:
        max_possible += 1
        roe_pct = roe * 100
        if roe_pct > 15:
            score += 1
            reasons.append(f"ROE ({roe_pct:.1f}%) is strong - efficient use of shareholder capital.")
        elif roe_pct < 5:
            score -= 1
            reasons.append(f"ROE ({roe_pct:.1f}%) is weak - capital efficiency may be a concern.")
        else:
            reasons.append(f"ROE ({roe_pct:.1f}%) is moderate.")
    else:
        reasons.append("ROE: Not available - skipped in scoring.")

    # --- Fundamentals: balance sheet strength (Debt/Equity) ---
    if de is not None:
        max_possible += 1
        if de < 50:
            score += 1
            reasons.append(f"Debt/Equity ({de:.1f}) is low - conservative balance sheet.")
        elif de > 150:
            score -= 1
            reasons.append(f"Debt/Equity ({de:.1f}) is high - elevated leverage risk.")
        else:
            reasons.append(f"Debt/Equity ({de:.1f}) is moderate.")
    else:
        reasons.append("Debt/Equity: Not available - skipped in scoring.")

    # --- Fundamentals: growth trajectory ---
    if rev_growth is not None:
        max_possible += 1
        growth_pct = rev_growth * 100
        if growth_pct > 15:
            score += 1
            reasons.append(f"Revenue growth ({growth_pct:.1f}%) is strong.")
        elif growth_pct < 0:
            score -= 1
            reasons.append(f"Revenue growth ({growth_pct:.1f}%) is negative - business is shrinking.")
        else:
            reasons.append(f"Revenue growth ({growth_pct:.1f}%) is modest/flat.")
    else:
        reasons.append("Revenue growth: Not available - skipped in scoring.")

    # --- Fundamentals: margin quality ---
    if margin is not None:
        max_possible += 1
        margin_pct = margin * 100
        if margin_pct > 15:
            score += 1
            reasons.append(f"Profit margin ({margin_pct:.1f}%) is healthy.")
        elif margin_pct < 5:
            score -= 1
            reasons.append(f"Profit margin ({margin_pct:.1f}%) is thin - limited pricing power or high costs.")
        else:
            reasons.append(f"Profit margin ({margin_pct:.1f}%) is moderate.")
    else:
        reasons.append("Profit margin: Not available - skipped in scoring.")

    # --- Piotroski F-Score: 9-point financial health checklist ---
    # This is scored on its own 0-9 (or fewer, if data was incomplete)
    # scale, so we convert it to the same +1/0/-1 vote style as the other
    # rules here, based on standard Piotroski interpretation bands:
    # 7-9 = strong, 3-6 = average, 0-2 = weak. We only fold it in if at
    # least half the 9 criteria had enough data to be computed - a score
    # built from only 2-3 available checks isn't reliable enough to vote.
    piotroski = fundamentals.get("piotroski")
    if piotroski is not None and piotroski["max_possible"] >= 5:
        max_possible += 1
        p_score = piotroski["score"]
        p_max = piotroski["max_possible"]
        p_ratio = p_score / p_max
        if p_ratio >= 0.75:
            score += 1
            reasons.append(f"Piotroski F-Score: {p_score}/{p_max} - strong financial health across profitability, leverage, and efficiency.")
        elif p_ratio <= 0.35:
            score -= 1
            reasons.append(f"Piotroski F-Score: {p_score}/{p_max} - weak financial health signals.")
        else:
            reasons.append(f"Piotroski F-Score: {p_score}/{p_max} - average/mixed financial health.")
    elif piotroski is not None:
        reasons.append(f"Piotroski F-Score: only {piotroski['max_possible']}/9 criteria had sufficient data - too incomplete to include in scoring.")
    else:
        reasons.append("Piotroski F-Score: Not available - skipped in scoring.")

    # --- Combine score into a decision, scaled to how much data was available ---
    if max_possible == 0:
        decision = "INSUFFICIENT DATA"
        confidence = "None"
    else:
        # Normalize: what fraction of available rules were bullish?
        ratio = score / max_possible
        if ratio >= 0.5:
            decision = "FUNDAMENTALLY STRONG"
            confidence = "High" if max_possible >= 4 else "Medium"
        elif ratio <= -0.5:
            decision = "FUNDAMENTALLY WEAK"
            confidence = "High" if max_possible >= 4 else "Medium"
        else:
            decision = "MIXED / NEUTRAL"
            confidence = "Medium" if max_possible >= 4 else "Low"

    output = f"""
DECISION: {decision}
CONFIDENCE: {confidence}
SCORE: {score} out of {max_possible} data points available (each rule only counted if data existed)

REASONING:
- """ + "\n- ".join(reasons) + f"""

NOTE: This is a rule-based fundamentals + valuation view only, for
{ticker}, using yfinance data. It does NOT include management commentary,
concall sentiment, or qualitative red flags (e.g. promoter pledging) -
those require documents this pipeline does not fetch. NOT financial advice.
"""
    return output



    """
    The AI agent doesn't need the whole DataFrame - just the latest
    snapshot in plain language. This is the 'prompt data' step.
    """
    latest = df.iloc[-1]  # last row = most recent trading day

    summary = f"""
Stock: {ticker}
Latest Close Price: {latest['Close']:.2f}
SMA20: {latest['SMA20']:.2f}
EMA20: {latest['EMA20']:.2f}
RSI14: {latest['RSI14']:.2f}
MACD: {latest['MACD']:.2f}
MACD Signal Line: {latest['MACD_Signal']:.2f}
"""
    return summary


# ----------------------------------------------------------------------
# STEP 5: AI Agent -- send indicators to Claude, get a reasoned opinion
# ----------------------------------------------------------------------
def score_technical_snapshot(row: pd.Series) -> dict:
    """
    The core Technical Agent rules, extracted to work on ANY single row of
    price+indicator data - not just the latest one. This is what makes the
    backtest module possible: it replays history day by day, calling this
    exact same function at each point, so the backtest uses IDENTICAL logic
    to the live agent - no risk of the two drifting out of sync.

    Returns a dict (not formatted text) so both the live agent's text
    formatter AND the backtest engine can consume it directly.
    """
    close = row["Close"]
    sma20 = row["SMA20"]
    ema20 = row["EMA20"]
    rsi = row["RSI14"]
    macd = row["MACD"]
    macd_signal = row["MACD_Signal"]

    score = 0
    reasons = []

    # --- Rule 1: Price vs moving averages (trend) ---
    if close > sma20 and close > ema20:
        score += 1
        reasons.append("Price is trading above both SMA20 and EMA20 (uptrend).")
    elif close < sma20 and close < ema20:
        score -= 1
        reasons.append("Price is trading below both SMA20 and EMA20 (downtrend).")
    else:
        reasons.append("Price is mixed relative to SMA20/EMA20 (no clear trend).")

    # --- Rule 2: RSI zones (momentum extremes) ---
    if rsi < 30:
        score += 1
        reasons.append(f"RSI ({rsi:.1f}) is below 30 - oversold, often a reversal signal.")
    elif rsi > 70:
        score -= 1
        reasons.append(f"RSI ({rsi:.1f}) is above 70 - overbought, risk of pullback.")
    else:
        reasons.append(f"RSI ({rsi:.1f}) is in the neutral 30-70 zone.")

    # --- Rule 3: MACD vs Signal line (momentum direction) ---
    if macd > macd_signal:
        score += 1
        reasons.append("MACD is above its Signal line (strengthening upward momentum).")
    else:
        score -= 1
        reasons.append("MACD is below its Signal line (weakening or downward momentum).")

    # --- Combine score into a decision ---
    if score >= 2:
        decision = "BUY"
        confidence = "High" if score == 3 else "Medium"
    elif score <= -2:
        decision = "DO NOT BUY"
        confidence = "High" if score == -3 else "Medium"
    else:
        decision = "WAIT"
        confidence = "Low"

    return {"decision": decision, "confidence": confidence, "score": score, "reasons": reasons}


def get_rule_based_recommendation(df: pd.DataFrame, ticker: str) -> str:
    """
    A rule-based 'Technical Agent' - no API key needed. This encodes the
    same kind of logic a human analyst applies by eye, as explicit rules.

    This is a legitimate starting point for the spec's Technical Agent.
    Later, you can swap this for (or combine it with) an LLM-based agent
    once you're ready to use the Anthropic API - the surrounding pipeline
    (data -> indicators -> agent -> decision) stays exactly the same either
    way. That's the real lesson here: the "AI agent" is a swappable piece,
    not the whole system.

    This function now delegates the actual rule-scoring to
    score_technical_snapshot() and just formats it as readable text -
    kept this way so nothing about the terminal script's output changes.
    """
    latest = df.iloc[-1]
    result = score_technical_snapshot(latest)
    decision = result["decision"]
    confidence = result["confidence"]
    score = result["score"]
    reasons = result["reasons"]

    output = f"""
DECISION: {decision}
CONFIDENCE: {confidence}
SCORE: {score} (range: -3 bearish to +3 bullish)

REASONING:
- """ + "\n- ".join(reasons) + f"""

NOTE: This is a rule-based technical view only (SMA/EMA/RSI/MACD), for

{ticker}, generated for a student project. It does NOT account for
fundamentals, news, sector trends, or broader market conditions, and is
NOT financial advice.
"""
    return output


def get_ai_synthesizer_recommendation(technical_output: str, fundamental_output: str,
                                        technical_summary: str, fundamental_summary: str,
                                        ticker: str, api_key: str = None) -> str:
    """
    The 'Synthesizer Agent' from the spec's Multi-Agent System.

    Now combines TWO independent rule-based agents (Technical + Fundamental)
    instead of just reviewing one. This is the real multi-agent pattern:
    each agent reasons independently over its own data, and the Synthesizer
    weighs them together - including flagging when they DISAGREE, which is
    often the most useful signal (e.g. cheap stock with weak technicals
    might be a value trap OR an entry opportunity - context decides which).

    api_key is accepted as a parameter (rather than relying only on the
    module-level client) so this function can be reused by the Streamlit
    app, where the user enters their key securely at runtime instead of
    it being hardcoded in the source file.
    """
    active_client = Anthropic(api_key=api_key) if api_key else client
    if active_client is None:
        raise ValueError("No API key available - pass api_key or set ANTHROPIC_API_KEY.")

    system_prompt = (
        "You are a Synthesizer Agent in a multi-agent stock analysis "
        "system. A rule-based Technical Agent and a rule-based "
        "Fundamental Agent have each independently analyzed the stock "
        "using only real fetched data. Your job is to weigh both views "
        "together into one final call. You explicitly call out when the "
        "two agents disagree and explain what that disagreement usually "
        "means. You are honest about uncertainty and do not manufacture "
        "false confidence when signals conflict or data is incomplete."
    )

    prompt = f"""Stock: {ticker}

--- Technical data ---
{technical_summary}

--- Technical Agent's output ---
{technical_output}

--- Fundamental data ---
{fundamental_summary}

--- Fundamental Agent's output ---
{fundamental_output}

Weigh both agents together. Respond in EXACTLY this structure:

FUNDAMENTALS: [1-2 sentences on business quality/valuation, referencing
  specific numbers]
VALUATION REALITY: [1-2 sentences - is the market pricing in fair value,
  a premium, or a discount, based on the P/E and growth data given]
TECHNICAL STRUCTURE: [1-2 sentences on price trend/momentum, referencing
  specific indicator values]
AGREEMENT CHECK: [Do the two agents point the same direction, or
  conflict? If they conflict, explain what that combination usually means]
FINAL DECISION: [BUY / DO NOT BUY / WAIT]
CONVICTION SCORE: [X out of 10]
CAVEAT: [what this analysis is missing - e.g. no management commentary,
  no concall sentiment, no sector-wide comparison]

This is for a student project only, not financial advice.
"""

    response = active_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=700,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def get_user_ticker() -> str:
    """
    Asks the user which stock to research and cleans up their input.
    Handles the common case where someone forgets the .NS suffix that
    yfinance needs for NSE-listed stocks.
    """
    while True:
        raw = input("Enter the NSE ticker SYMBOL you want to research (e.g. TCS, INFY, RPOWER - not the company's full name): ").strip().upper()

        # yfinance treats a space as separating MULTIPLE tickers, e.g.
        # "RELIANCE POWER" gets read as two different stocks: "RELIANCE"
        # and "POWER" - neither of which exists. Reject spaces early with
        # a clear message instead of letting yfinance silently misread it.
        if " " in raw:
            print(f"  -> '{raw}' looks like a company name, not a ticker symbol.")
            print("     Look up the exact NSE symbol first (e.g. on nseindia.com or")
            print("     moneycontrol.com) - for example 'Reliance Power' -> RPOWER.")
            continue

        # If they didn't add an exchange suffix, default to NSE (.NS).
        # BSE-only stocks would need ".BO" instead - not handled here yet,
        # that's a good thing to add later.
        if not raw.endswith(".NS") and not raw.endswith(".BO"):
            raw += ".NS"

        return raw


# ----------------------------------------------------------------------
# STEP 6: Run it all
# ----------------------------------------------------------------------
if __name__ == "__main__":
    TICKER = get_user_ticker()

    print(f"\nFetching data for {TICKER}...")
    data = fetch_data(TICKER, PERIOD)
    validate_data(data, TICKER)

    print("Computing indicators...")
    data = add_indicators(data)

    summary_text = summarize_latest(data, TICKER)
    print("\n--- Latest Snapshot ---")
    print(summary_text)

    print("Running rule-based prediction engine (Technical Agent)...\n")
    technical_recommendation = get_rule_based_recommendation(data, TICKER)

    print("--- Technical Agent (Rule-Based) ---")
    print(technical_recommendation)

    print("\nFetching fundamental data...")
    fundamentals = fetch_fundamentals(TICKER)

    print("Computing Piotroski F-Score (needs 2 years of financial statements)...")
    piotroski_inputs = fetch_piotroski_inputs(TICKER)
    fundamentals["piotroski"] = compute_piotroski_score(piotroski_inputs)

    validate_fundamentals(fundamentals, TICKER)
    fundamentals_summary = summarize_fundamentals(fundamentals, TICKER)

    print("\n--- Fundamentals Snapshot ---")
    print(fundamentals_summary)

    print("Running rule-based prediction engine (Fundamental Agent)...\n")
    fundamental_recommendation = get_rule_based_fundamental_recommendation(fundamentals, TICKER)

    print("--- Fundamental Agent (Rule-Based) ---")
    print(fundamental_recommendation)

    if AI_AGENT_ENABLED:
        print("\nRunning AI Synthesizer Agent...\n")
        ai_recommendation = get_ai_synthesizer_recommendation(
            technical_recommendation, fundamental_recommendation,
            summary_text, fundamentals_summary, TICKER
        )
        print("--- Synthesizer Agent (AI) ---")
        print(ai_recommendation)
    else:
        print("\n(AI Synthesizer Agent skipped - no API key set. Add one at")
        print(" the top of this file under ANTHROPIC_API_KEY to enable it.)")
