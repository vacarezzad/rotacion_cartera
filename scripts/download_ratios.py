"""Baja de Supabase los ratios de los CEDEARs (cedear_ccl_diario -> data/cedear_ratios.csv)
y el dólar CCL diario (ccl_history -> data/ccl_history.csv).

Guarda una fila por ticker y por cada cambio de ratio (ticker,fecha,ratio), así una operación
vieja usa el ratio vigente en su fecha aunque después haya un split.
Necesita SUPABASE_ANON_KEY (lectura pública por RLS). Sin la clave, deja el archivo como está.
Uso: python scripts/download_ratios.py
"""
import json
import os
import sys
import urllib.request

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "cedear_ratios.csv")
OUT_CCL = os.path.join(ROOT, "data", "ccl_history.csv")
URL = os.environ.get("SUPABASE_URL") or "https://phhpiqwryhqlierztmky.supabase.co"
KEY = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()


def fetch(path="cedear_ccl_diario?select=ticker,fecha,ratio&ratio=not.is.null&order=ticker.asc,fecha.asc"):
    rows, step = [], 1000
    while True:
        q = f"{URL}/rest/v1/{path}&offset={len(rows)}&limit={step}"
        req = urllib.request.Request(q, headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        rows += page
        if len(page) < step:
            return rows


def main():
    if not KEY:
        print("Sin SUPABASE_ANON_KEY: dejo data/cedear_ratios.csv como está")
        return
    df = pd.DataFrame(fetch())
    if df.empty:
        print("Supabase no devolvió ratios; dejo el archivo como está")
        return
    df["ticker"] = df.ticker.str.upper()
    df["ratio"] = df.ratio.astype(float)
    df = df.sort_values(["ticker", "fecha"])
    df = df[df.ratio != df.groupby("ticker").ratio.shift()]
    df.to_csv(OUT, index=False)
    print(f"{OUT}: {df.ticker.nunique()} tickers, {len(df)} filas")
    c = pd.DataFrame(fetch("ccl_history?select=fecha,ccl_venta&ccl_venta=not.is.null&order=fecha.asc"))
    if not c.empty:
        c.rename(columns={"ccl_venta": "ccl"}).to_csv(OUT_CCL, index=False)
        print(f"{OUT_CCL}: {len(c)} días, último {c.fecha.iloc[-1]}")


if __name__ == "__main__":
    sys.exit(main())
