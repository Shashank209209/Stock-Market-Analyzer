# 📈 Dalal Street AI

A rule-based + AI-assisted equity research tool for Indian (NSE-listed) stocks.
Fetches real market and financial statement data, computes technical and
fundamental scores deterministically, and optionally layers an LLM
**Synthesizer Agent** on top to combine both views into a final call.

Available in two forms:
- **`app.py`** — interactive Streamlit dashboard with charts and decision cards
- **`stock_analyzer_v01.py`** — terminal script (same engine, plain text output)

> Built to explore how AI-assisted decision tools should be architected —
> separating **verifiable, computed signals** from **LLM-based reasoning**,
> instead of asking an LLM to guess numbers it was never given.

---

## Why this exists

Asking a general chatbot "should I buy stock X" is unreliable for one
specific reason: it has no live market data, so it can generate
plausible-sounding but fabricated numbers. This project instead:

1. **Fetches real data first** (price history, financial statements)
2. **Computes indicators and scores deterministically** in Python —
   auditable and reproducible, never guessed
3. **Uses an LLM only for synthesis** over that verified data — not for
   producing the numbers themselves

---

## Features

- **Technical Agent** — rule-based scoring on price trend (SMA20/EMA20),
  momentum (RSI14), and momentum direction (MACD vs Signal)
- **Fundamental Agent** — rule-based scoring on P/E, ROE, Debt/Equity,
  Revenue Growth, Profit Margin, and a full **Piotroski F-Score**
  (9-point financial health checklist from multi-year statements)
- **AI Synthesizer Agent** *(optional)* — an LLM reviews both agents'
  outputs, flags disagreement between them, and gives a final call with
  a conviction score
- **Interactive dashboard** — price/RSI/MACD charts, fundamentals table,
  Piotroski breakdown, color-coded decision cards
- **Graceful degradation** — every score only counts criteria it actually
  had data for; missing data is flagged, never faked

---

## Setup

```bash
pip install -r requirements.txt
```

### Run the dashboard (recommended)
```bash
python -m streamlit run app.py
```
Opens in your browser. Enter an NSE ticker (e.g. `TCS`, `INFY`) in the
sidebar and click **Analyze**.

### Run the terminal version
```bash
python stock_analyzer_v01.py
```

### Enabling the AI Synthesizer

- **In the app:** check "Enable AI Synthesizer Agent" in the sidebar and
  paste your API key directly there (used only for that session, never saved)
- **In the terminal script:** paste your key into the `ANTHROPIC_API_KEY`
  constant near the top of `stock_analyzer_v01.py`

Get a free key at [console.anthropic.com](https://console.anthropic.com).
**Never commit a real API key to this repo** — the terminal script's key
placeholder should stay as-is before pushing.

---

## Known limitations

- `yfinance` coverage for NSE stocks is inconsistent, especially for
  small/mid-caps and recent IPOs — some fields may be unavailable
- No qualitative analysis (management commentary, concall sentiment,
  promoter pledge activity, news) — deliberately excluded rather than faked
- Entry-focused (BUY/WAIT/DON'T BUY), not exit-focused
- Educational project only — **not financial advice**

---

## Roadmap

- [ ] Backtest rule-based agent decisions against historical prices
- [ ] Risk metrics (volatility, beta, drawdown)
- [ ] Watchlist / multi-stock comparison
- [ ] Sector-relative valuation comparison

---

## Tech stack

Python, Pandas, `yfinance`, Streamlit, Plotly, Anthropic API (optional)

---

## Disclaimer

For educational and portfolio purposes only. Not financial advice.

Python, Pandas, yfinance, Streamlit, Plotly, Anthropic API (optional)

Disclaimer

For educational and portfolio purposes only. Not financial advice.
