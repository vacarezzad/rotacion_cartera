"""Fuentes de datos sin Yahoo Finance.

- Acciones/ETFs: Tiingo si existe la variable de entorno TIINGO_API_KEY (precios ajustados por splits y
  dividendos, desde 1990s; clave gratis en tiingo.com). Si no, API pública de Nasdaq (sin clave; ajustada
  por splits según Nasdaq, NO por dividendos; se agrega un control de splits por las dudas).
- ^VIX: CSV público de CBOE.
- ^TNX: tasa a 10 años de FRED (DGS10), sin clave.
"""
from __future__ import annotations
import io
import os
import time
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
ETFS = {"SPY", "QQQ", "DIA", "IWM", "EWZ", "XLE", "XLF", "XLK", "GLD"}
COLS = ["Open", "High", "Low", "Close", "Volume"]


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[$,]", "", regex=True).replace({"N/A": np.nan, "": np.nan}),
                         errors="coerce")


def nasdaq(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    cls = "etf" if ticker in ETFS else "stocks"
    variants = [ticker, ticker.replace("-", "."), ticker.replace("-", "/")] if "-" in ticker else [ticker]
    last_err = None
    for sym in variants:
        url = f"https://api.nasdaq.com/api/quote/{sym}/historical"
        params = {"assetclass": cls, "fromdate": start, "todate": end, "limit": 99999}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, headers=UA, timeout=30)
                r.raise_for_status()
                js = r.json()
                rows = (((js or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
                if not rows:
                    last_err = f"sin filas ({sym})"
                    break
                df = pd.DataFrame(rows)
                df.index = pd.to_datetime(df["date"], format="%m/%d/%Y")
                df = df.rename(columns=str.capitalize)
                df = pd.DataFrame({c: _num(df[c]) for c in COLS}, index=df.index).sort_index()
                return fix_splits(df.dropna(subset=["Close"]))
            except Exception as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"nasdaq {ticker}: {last_err}")


def tiingo(ticker: str, start: str, end: str | None, key: str) -> pd.DataFrame:
    url = f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices"
    params = {"startDate": start, "token": key, "format": "json"}
    if end:
        params["endDate"] = end
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df.index = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.rename(columns={"adjOpen": "Open", "adjHigh": "High", "adjLow": "Low",
                              "adjClose": "Close", "adjVolume": "Volume"})[COLS].sort_index()


def cboe_vix() -> pd.DataFrame:
    r = requests.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
                     headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.index = pd.to_datetime(df["DATE"])
    df = df.rename(columns=str.capitalize)[["Open", "High", "Low", "Close"]]
    df["Volume"] = 0.0
    return df.sort_index()


def fred(series="DGS10") -> pd.DataFrame:
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}",
                     headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.index = pd.to_datetime(df.iloc[:, 0])
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce").ffill()
    return pd.DataFrame({"Open": s, "High": s, "Low": s, "Close": s, "Volume": 0.0}).sort_index()


def fix_splits(df: pd.DataFrame) -> pd.DataFrame:
    """Si la serie trae un split sin ajustar (salto overnight ~1/k), ajusta la historia previa."""
    df = df.astype(float).copy()
    ratio = (df["Open"] / df["Close"].shift(1)).values
    for i in np.where(np.isfinite(ratio))[0]:
        rt = ratio[i]
        for k in (2, 3, 4, 5, 8, 10, 15, 20, 1 / 2, 1 / 3, 1 / 5, 1 / 10):
            if abs(rt * k - 1) < 0.04:
                idx = df.index[:i]
                df.loc[idx, ["Open", "High", "Low", "Close"]] /= k
                df.loc[idx, "Volume"] *= k
                print(f"[sources] split {k:g}:1 detectado y ajustado el {df.index[i].date()}")
                break
    return df


def get(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    if ticker == "^VIX":
        df = cboe_vix()
    elif ticker == "^TNX":
        df = fred("DGS10")
    elif os.environ.get("TIINGO_API_KEY"):
        df = tiingo(ticker, start, end, os.environ["TIINGO_API_KEY"])
    else:
        df = nasdaq(ticker, start, end)
    df = df.loc[pd.Timestamp(start):] if end is None else df.loc[pd.Timestamp(start):pd.Timestamp(end)]
    return df[COLS]
