"""Backfill de OHLCV diario (ajustado) para el pipeline de ML de trading.

Corre en GitHub Actions (mismo método que technical_indicators.py: yfinance en lote)
y deja un CSV por ticker en data_ml/. No toca Supabase ni nada de la app.
"""
import os
import sys
import time

import pandas as pd
import yfinance as yf

TICKERS = ["NVDA", "GOOGL", "AMD", "QQQ", "MELI", "VIST", "META", "MSFT", "AMZN", "GLD",
           "AVGO", "MCD", "TSM", "ORCL", "TSLA", "SPY", "AAPL", "^VIX", "^TNX",
           # ETFs con CEDEAR en BYMA (rotación multi-activo) + bonos USA solo como referencia
           "SLV", "SMH", "DIA", "IWM", "XLV", "XLP", "XLU", "XLE", "XLK", "XLC", "XLI", "XLY",
           "XLB", "XLF", "VEA", "EFA", "EEM", "FXI", "GDX", "EWZ", "USO", "TLT", "IEF", "BIL",
           "^VIX3M", "^VIX9D",
           # universo ampliado a 30 CEDEARs líquidos (Supabase cedear_ccl_diario, nov-2026)
           "MRNA", "GLOB", "KO", "BABA", "INTC", "PEP", "PBR", "V", "IBM", "WMT", "JPM", "ASML", "MRVL", "UNH"]
import os as _os
TICKERS = TICKERS + [t for t in _os.environ.get("EXTRA_TICKERS", "").replace(",", " ").split() if t not in TICKERS]

START = os.environ.get("ML_START", "1998-01-01")
OUT = "data/prices"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    missing = list(TICKERS)
    for attempt in range(4):
        if not missing:
            break
        raw = yf.download(missing, start=START, auto_adjust=True, progress=False,
                          group_by="ticker", threads=True)
        for t in list(missing):
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(df):
                df.round(6).to_csv(os.path.join(OUT, f"{t.replace('^', '_')}.csv"))
                print(f"{t}: {len(df)} filas desde {df.index.min().date()}")
                missing.remove(t)
        if missing:
            print(f"faltan {missing}, reintento en {30 * (attempt + 1)}s")
            time.sleep(30 * (attempt + 1))
    if missing:
        print(f"SIN DATOS: {missing}")
    return 0 if len(missing) < len(TICKERS) else 1


if __name__ == "__main__":
    sys.exit(main())
