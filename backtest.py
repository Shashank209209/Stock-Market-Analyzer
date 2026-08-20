"""
Dalal Street AI - Backtest Module

Answers the question: "If I had followed the Technical Agent's BUY/WAIT/
DO NOT BUY calls every day over the past N months, would I have made
money - and how does that compare to just buying and holding?"

Run with: python backtest.py

HOW THIS AVOIDS LOOKAHEAD BIAS (the #1 way backtests fool people):
Every indicator here (SMA, EMA, RSI, MACD) is computed using pandas
.rolling() and .ewm(), which are inherently "causal" - the value on any
given day only ever looks BACKWARD at prior days, never forward. So it's
safe to compute indicators once across the full price history and then
replay day by day; each day's decision only ever used data that would
genuinely have been available on that day in real life.

The one thing we must NOT do (and don't) is use the CLOSE price of a day
to decide whether to buy/sell ON that same day before the market closed -
we treat "decision known at close" as "trade executes at that close",
which is a standard, honest simplification for a first-pass backtest.
"""

import pandas as pd

from stock_analyzer_v01 import fetch_data, add_indicators, score_technical_snapshot


def run_technical_backtest(ticker: str, period: str = "1y", starting_capital: float = 100000.0) -> dict:
    """
    Replays the Technical Agent's rules day by day across historical data
    and simulates a simple strategy:
        - BUY signal while flat  -> enter a position at that day's close
        - DO NOT BUY while holding -> exit the position at that day's close
        - WAIT -> do nothing
    Fully invests `starting_capital` on each entry (no partial position
    sizing - kept simple for a first version).

    Returns a dict with the trade log, equity curve, and summary metrics,
    including a buy-and-hold comparison over the same period.
    """
    df = fetch_data(ticker, period)
    if df.empty:
        raise ValueError(f"No data found for {ticker}")

    df = add_indicators(df)
    # Indicators need a warm-up window (e.g. SMA20/EMA20 need 20 days of
    # history) before they're valid - drop rows where they're still NaN,
    # so we never trade on incomplete/undefined indicator values.
    df = df.dropna(subset=["SMA20", "EMA20", "RSI14", "MACD", "MACD_Signal"])

    if len(df) < 5:
        raise ValueError(f"Not enough data to backtest {ticker} after indicator warm-up.")

    trades = []           # completed trades: entry/exit price, date, return
    equity_curve = []      # portfolio value over time, for plotting
    cash = starting_capital
    shares_held = 0
    entry_price = None
    entry_date = None

    for date, row in df.iterrows():
        result = score_technical_snapshot(row)
        decision = result["decision"]
        close = row["Close"]

        # --- Enter a position ---
        if decision == "BUY" and shares_held == 0:
            shares_held = cash / close   # fully invest available cash
            entry_price = close
            entry_date = date
            cash = 0.0

        # --- Exit a position ---
        elif decision == "DO NOT BUY" and shares_held > 0:
            proceeds = shares_held * close
            trade_return_pct = ((close - entry_price) / entry_price) * 100
            trades.append({
                "entry_date": entry_date, "entry_price": entry_price,
                "exit_date": date, "exit_price": close,
                "return_pct": trade_return_pct,
            })
            cash = proceeds
            shares_held = 0
            entry_price = None
            entry_date = None

        # --- Mark portfolio value for the equity curve (whether flat or holding) ---
        current_value = cash + (shares_held * close)
        equity_curve.append({"date": date, "value": current_value})

    # If still holding a position at the end of the backtest window, this
    # is an "open" position - not yet a completed trade, so it's tracked
    # separately from the closed-trade stats to avoid mixing realized and
    # unrealized results.
    open_position = None
    final_close = df["Close"].iloc[-1]
    if shares_held > 0:
        unrealized_pct = ((final_close - entry_price) / entry_price) * 100
        open_position = {
            "entry_date": entry_date, "entry_price": entry_price,
            "current_price": final_close, "unrealized_return_pct": unrealized_pct,
        }

    equity_df = pd.DataFrame(equity_curve).set_index("date")
    final_value = equity_df["value"].iloc[-1]
    strategy_return_pct = ((final_value - starting_capital) / starting_capital) * 100

    # --- Buy & Hold comparison: what if you'd just bought on day 1 and held? ---
    buy_hold_return_pct = ((df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0]) * 100

    # --- Win rate: what fraction of CLOSED trades were profitable ---
    win_rate = None
    if trades:
        wins = sum(1 for t in trades if t["return_pct"] > 0)
        win_rate = (wins / len(trades)) * 100

    # --- Max drawdown: worst peak-to-trough drop in the equity curve ---
    running_max = equity_df["value"].cummax()
    drawdown = (equity_df["value"] - running_max) / running_max * 100
    max_drawdown_pct = drawdown.min()

    return {
        "ticker": ticker,
        "period": period,
        "trades": trades,
        "open_position": open_position,
        "equity_curve": equity_df,
        "strategy_return_pct": strategy_return_pct,
        "buy_hold_return_pct": buy_hold_return_pct,
        "num_trades": len(trades),
        "win_rate_pct": win_rate,
        "max_drawdown_pct": max_drawdown_pct,
        "starting_capital": starting_capital,
        "final_value": final_value,
    }


def print_backtest_report(result: dict) -> None:
    """Prints a plain-text summary of a backtest result to the terminal."""
    print(f"\n{'='*60}")
    print(f"BACKTEST REPORT: {result['ticker']}  (period: {result['period']})")
    print(f"{'='*60}")
    print(f"Starting capital:      Rs {result['starting_capital']:,.2f}")
    print(f"Final portfolio value: Rs {result['final_value']:,.2f}")
    print(f"Strategy return:       {result['strategy_return_pct']:+.2f}%")
    print(f"Buy & Hold return:     {result['buy_hold_return_pct']:+.2f}%")

    diff = result["strategy_return_pct"] - result["buy_hold_return_pct"]
    if diff > 0:
        print(f"-> Strategy OUTPERFORMED buy-and-hold by {diff:.2f} percentage points.")
    else:
        print(f"-> Strategy UNDERPERFORMED buy-and-hold by {abs(diff):.2f} percentage points.")

    print(f"\nNumber of completed trades: {result['num_trades']}")
    if result["win_rate_pct"] is not None:
        print(f"Win rate:                    {result['win_rate_pct']:.1f}%")
    else:
        print("Win rate:                    N/A (no completed trades)")
    print(f"Max drawdown:                {result['max_drawdown_pct']:.2f}%")

    if result["open_position"]:
        op = result["open_position"]
        print(f"\nStill holding an open position entered {op['entry_date'].date()} "
              f"at Rs {op['entry_price']:.2f} "
              f"(unrealized: {op['unrealized_return_pct']:+.2f}%)")

    if result["trades"]:
        print(f"\nTrade log:")
        for i, t in enumerate(result["trades"], 1):
            print(f"  {i}. {t['entry_date'].date()} @ Rs{t['entry_price']:.2f}  ->  "
                  f"{t['exit_date'].date()} @ Rs{t['exit_price']:.2f}   "
                  f"({t['return_pct']:+.2f}%)")

    print(f"\nNOTE: This backtests the Technical Agent's rules ONLY (SMA/EMA/")
    print(f"RSI/MACD). It ignores brokerage fees, taxes, and slippage, and")
    print(f"assumes trades execute exactly at the closing price. Past")
    print(f"performance does not guarantee future results. Educational")
    print(f"project only - not financial advice.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ticker_input = input("Enter NSE ticker to backtest (e.g. TCS, INFY): ").strip().upper()
    if not ticker_input.endswith((".NS", ".BO")):
        ticker_input += ".NS"

    period_input = input("Period to backtest [6mo/1y/2y] (default 1y): ").strip() or "1y"

    try:
        result = run_technical_backtest(ticker_input, period_input)
        print_backtest_report(result)
    except ValueError as e:
        print(f"\nError: {e}")