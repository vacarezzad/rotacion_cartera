"""Snapshot de fundamentals para el pipeline ML (yfinance desde GitHub Actions). Escribe data_ml/fundamentals.csv
y data_ml/fund_detail/<TICKER>.json (sorpresas de earnings, cambios de recomendación, crecimiento trimestral)."""
import json
import os
import time

import pandas as pd
import yfinance as yf

TICKERS = ["NVDA", "GOOGL", "AMD", "MELI", "VIST", "META", "MSFT", "AMZN", "AVGO", "MCD", "TSM", "ORCL", "TSLA",
           "MRNA", "GLOB", "AAPL", "KO", "BABA", "INTC", "PEP", "PBR", "V", "IBM", "WMT", "JPM", "ASML", "MRVL", "UNH"]
KEYS = ["shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "pegRatio", "priceToSalesTrailing12Months",
        "enterpriseToEbitda", "priceToBook", "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
        "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity", "freeCashflow", "totalCash", "totalDebt",
        "debtToEquity", "currentPrice", "targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions",
        "recommendationMean", "recommendationKey", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "beta", "shortPercentOfFloat",
        "heldPercentInstitutions", "dividendYield"]
OUT = "data"


def safe(f):
    try:
        return f()
    except Exception as e:
        return f"error: {e}"


def df_json(df, n=8):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    d = df.head(n).copy()
    d.index = d.index.astype(str)
    d.columns = d.columns.astype(str)
    return json.loads(d.to_json(orient="index", date_format="iso", default_handler=str))


def main():
    os.makedirs(f"{OUT}/fund_detail", exist_ok=True)
    rows = []
    for t in TICKERS:
        tk = yf.Ticker(t)
        info = safe(lambda: tk.info) or {}
        if not isinstance(info, dict):
            info = {}
        rows.append({"ticker": t, **{k: info.get(k) for k in KEYS}})
        detail = {
            "earnings_dates": df_json(safe(lambda: tk.get_earnings_dates(limit=8)), 8),
            "upgrades_downgrades": df_json(safe(lambda: tk.upgrades_downgrades.sort_index(ascending=False)), 15),
            "quarterly_income": df_json(safe(lambda: tk.quarterly_income_stmt.loc[
                [r for r in ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "Diluted EPS"]
                 if r in tk.quarterly_income_stmt.index]].T), 6),
            "eps_trend": df_json(safe(lambda: tk.eps_trend), 6),
            "growth_estimates": df_json(safe(lambda: tk.growth_estimates), 6),
        }
        ed = safe(lambda: tk.get_earnings_dates(limit=60))
        if isinstance(ed, pd.DataFrame) and not ed.empty:
            os.makedirs(f"{OUT}/earnings", exist_ok=True)
            ed.to_csv(f"{OUT}/earnings/{t}.csv")
        with open(f"{OUT}/fund_detail/{t}.json", "w") as fh:
            json.dump(detail, fh, default=str, indent=1)
        print(t, "ok" if info else "sin info")
        time.sleep(1.5)
    pd.DataFrame(rows).to_csv(f"{OUT}/fundamentals.csv", index=False)


if __name__ == "__main__":
    main()
