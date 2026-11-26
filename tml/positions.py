"""Registro de operaciones reales (operaciones.csv) -> posiciones abiertas.

Columnas de operaciones.csv (una fila por orden ejecutada en el broker):
  fecha         YYYY-MM-DD
  ticker        subyacente (ej. NVDA)
  operacion     compra | venta
  cantidad      cantidad de CEDEARs
  precio_ars    precio por CEDEAR en pesos
  ccl           dólar CCL (opcional: si falta, se usa el CCL implícito de la operación =
                precio_ars × ratio / cierre del subyacente en USD ese día)
  ratio         CEDEARs por acción (opcional: si falta, se toma el vigente ese día de la tabla
                cedear_ccl_diario de Supabase, copiada en data/cedear_ratios.csv)
  comision_ars  comisión + derechos + IVA en pesos (opcional)
  nota          texto libre (opcional)
Se carga todo en pesos; los resultados se expresan en dólares.
Costo por precio promedio ponderado; una venta descuenta al costo promedio.
"""
from __future__ import annotations
import os
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "operaciones.csv")


RATIOS = os.path.join(ROOT, "data", "cedear_ratios.csv")


def _ratios() -> pd.DataFrame:
    """Ratios por fecha (bajados de Supabase, tabla cedear_ccl_diario, por scripts/download_ratios.py)."""
    if not os.path.exists(RATIOS):
        return pd.DataFrame(columns=["ticker", "fecha", "ratio"])
    r = pd.read_csv(RATIOS)
    r["ticker"] = r.ticker.str.upper()
    r["fecha"] = pd.to_datetime(r.fecha if "fecha" in r else "1900-01-01")
    return r.sort_values("fecha")


def _ratio_on(rat: pd.DataFrame, ticker: str, fecha):
    """Ratio vigente en la fecha de la operación; si es anterior al primer dato, el más viejo."""
    r = rat[rat.ticker == ticker]
    if r.empty:
        return None
    prev = r[r.fecha <= pd.Timestamp(fecha)]
    return float((prev if len(prev) else r).ratio.iloc[-1 if len(prev) else 0])


def _close_on(ticker: str, fecha) -> float | None:
    fp = os.path.join(ROOT, "data", "prices", f"{ticker}.csv")
    if not os.path.exists(fp):
        return None
    c = pd.read_csv(fp, parse_dates=["Date"]).set_index("Date")["Close"].dropna().sort_index()
    c = c.loc[:pd.Timestamp(fecha)]
    return float(c.iloc[-1]) if len(c) else None


def load_trades(path: str = PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    t = pd.read_csv(path, comment="#", skip_blank_lines=True)
    if t.empty:
        return t
    t["fecha"] = pd.to_datetime(t["fecha"])
    t["ticker"] = t["ticker"].str.strip().str.upper()
    t["operacion"] = t["operacion"].str.strip().str.lower()
    for c in ["cantidad", "precio_ars", "ccl", "ratio", "comision_ars"]:
        t[c] = pd.to_numeric(t.get(c), errors="coerce")
    t["comision_ars"] = t["comision_ars"].fillna(0)
    rat = _ratios()
    t["ratio"] = [r if pd.notna(r) else _ratio_on(rat, tk, f) for tk, r, f in zip(t.ticker, t.ratio, t.fecha)]
    t["ccl_implicito"] = False
    for i, r in t.iterrows():
        if (pd.isna(r.ccl) or r.ccl <= 0) and r.ratio:
            px = _close_on(r.ticker, r.fecha)
            if px:
                t.at[i, "ccl"] = r.precio_ars * r.ratio / px
                t.at[i, "ccl_implicito"] = True
    return t.sort_values("fecha").reset_index(drop=True)


def positions(trades: pd.DataFrame) -> pd.DataFrame:
    """Posiciones abiertas: cantidad, costo total ARS y USD (promedio ponderado), ratio, primera compra."""
    rows = {}
    for _, r in trades.iterrows():
        p = rows.setdefault(r.ticker, {"cantidad": 0.0, "costo_ars": 0.0, "costo_usd": 0.0, "usd_ok": True,
                                       "ratio": None, "desde": r.fecha, "realizado_ars": 0.0,
                                       "realizado_usd": 0.0})
        if pd.notna(r.ratio):
            p["ratio"] = float(r.ratio)
        if r.operacion.startswith("c"):
            if p["cantidad"] == 0:
                p["desde"] = r.fecha
            monto = r.cantidad * r.precio_ars + r.comision_ars
            p["cantidad"] += r.cantidad
            p["costo_ars"] += monto
            if pd.notna(r.ccl) and r.ccl > 0:
                p["costo_usd"] += monto / r.ccl
            else:
                p["usd_ok"] = False
        else:
            q = min(r.cantidad, p["cantidad"]) if p["cantidad"] > 0 else 0
            if q > 0:
                frac = q / p["cantidad"]
                p["realizado_ars"] += q * r.precio_ars - r.comision_ars - p["costo_ars"] * frac
                if pd.notna(r.ccl) and r.ccl > 0:
                    p["realizado_usd"] += (q * r.precio_ars - r.comision_ars) / r.ccl - p["costo_usd"] * frac
                p["costo_ars"] *= 1 - frac
                p["costo_usd"] *= 1 - frac
                p["cantidad"] -= q
    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return df
    df.index.name = "ticker"
    return df


def held_tickers(fallback_json: str | None = None) -> list[str]:
    """Tickers en cartera: de operaciones.csv si tiene filas; si no, de portfolio.json."""
    t = load_trades()
    if not t.empty:
        p = positions(t)
        return list(p.index[p.cantidad > 0])
    import json
    fp = fallback_json or os.path.join(ROOT, "portfolio.json")
    return json.load(open(fp)).get("held", []) if os.path.exists(fp) else []
