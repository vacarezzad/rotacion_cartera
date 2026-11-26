"""Registro de operaciones reales (operaciones.csv) -> posiciones abiertas.

Columnas de operaciones.csv (una fila por orden ejecutada en el broker):
  fecha         YYYY-MM-DD
  ticker        subyacente (ej. NVDA)
  operacion     compra | venta
  cantidad      cantidad de CEDEARs
  precio_ars    precio por CEDEAR en pesos
  ccl           dólar CCL del día (opcional; sin él no se calcula el costo en USD)
  ratio         CEDEARs por acción (ej. 24 para NVDA; opcional, para valuar con el precio del subyacente)
  comision_ars  comisión + derechos + IVA en pesos (opcional)
  nota          texto libre (opcional)
Costo por precio promedio ponderado; una venta descuenta al costo promedio.
"""
from __future__ import annotations
import os
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "operaciones.csv")


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
    return t.sort_values("fecha").reset_index(drop=True)


def positions(trades: pd.DataFrame) -> pd.DataFrame:
    """Posiciones abiertas: cantidad, costo total ARS y USD (promedio ponderado), ratio, primera compra."""
    rows = {}
    for _, r in trades.iterrows():
        p = rows.setdefault(r.ticker, {"cantidad": 0.0, "costo_ars": 0.0, "costo_usd": 0.0, "usd_ok": True,
                                       "ratio": None, "desde": r.fecha, "realizado_ars": 0.0})
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
                p["costo_ars"] *= 1 - frac
                p["costo_usd"] *= 1 - frac
                p["cantidad"] -= q
    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return df
    df.index.name = "ticker"
    return df[df.cantidad > 0]


def held_tickers(fallback_json: str | None = None) -> list[str]:
    """Tickers en cartera: de operaciones.csv si tiene filas; si no, de portfolio.json."""
    t = load_trades()
    if not t.empty:
        return list(positions(t).index)
    import json
    fp = fallback_json or os.path.join(ROOT, "portfolio.json")
    return json.load(open(fp)).get("held", []) if os.path.exists(fp) else []
