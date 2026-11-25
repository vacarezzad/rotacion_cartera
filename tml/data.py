"""Descarga y armado de paneles OHLCV (fecha x ticker) del subyacente en USD."""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

TICKER_FIX = {"BRK.B": "BRK-B", "BRKB": "BRK-B", "BF.B": "BF-B"}


def load_universe(xlsx_path: str) -> list[str]:
    df = pd.read_excel(xlsx_path)
    if "Selected" in df.columns:
        df = df[df["Selected"] == 1]
    syms = [TICKER_FIX.get(str(s).strip(), str(s).strip()) for s in df["Symbol"].dropna()]
    return sorted(set(syms))


def download(tickers, start, end=None, cache_dir="data_cache") -> dict[str, pd.DataFrame]:
    """Devuelve {campo: DataFrame fecha x ticker}. Fuentes: ver tml/sources.py (sin Yahoo)."""
    import time
    from . import sources
    os.makedirs(cache_dir, exist_ok=True)
    frames = {}
    for t in tickers:
        path = os.path.join(cache_dir, f"{t.replace('^', '_')}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            try:
                df = sources.get(t, start, end)
            except Exception as e:
                print(f"[data] sin datos: {t} ({e})")
                continue
            df.to_csv(path)
            time.sleep(1.0)   # cortesía con la API
        print(f"[data] {t}: {len(df)} filas desde {df.index.min().date()}")
        frames[t] = df
    return to_panels(frames)


def to_panels(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {}
    for f in ["Open", "High", "Low", "Close", "Volume"]:
        out[f.lower()] = pd.DataFrame({t: d[f] for t, d in frames.items()}).sort_index()
    # calendario de NYSE: descarta días donde solo cotiza un índice (p.ej. ^VIX en feriados de EE.UU.),
    # porque una fila vacía contamina las ventanas rolling de todos los activos por 252 días
    eq_cols = [t for t in out["close"].columns if not t.startswith("^")]
    if eq_cols:
        keep = out["close"][eq_cols].notna().any(axis=1)
        out = {k: v.loc[keep] for k, v in out.items()}
    return out


def synthetic_panels(n_assets=20, n_days=3000, seed=0) -> dict[str, pd.DataFrame]:
    """Random walk sin señal: sirve para verificar que el pipeline NO encuentra nada (AUC ~0,5)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2008-01-01", periods=n_days)
    tick = [f"A{i:02d}" for i in range(n_assets)] + ["SPY", "^VIX", "^TNX"]
    frames = {}
    mkt = rng.normal(0.0003, 0.011, n_days)
    for t in tick:
        r = 0.6 * mkt + rng.normal(0, 0.013, n_days)
        close = 100 * np.exp(np.cumsum(r))
        op = close * np.exp(rng.normal(0, 0.004, n_days))
        hi = np.maximum(op, close) * np.exp(np.abs(rng.normal(0, 0.007, n_days)))
        lo = np.minimum(op, close) * np.exp(-np.abs(rng.normal(0, 0.007, n_days)))
        vol = rng.lognormal(15, 0.3, n_days)
        frames[t] = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)
    return to_panels(frames)
