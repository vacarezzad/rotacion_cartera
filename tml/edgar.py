"""Features fundamentales point-in-time a partir de SEC EDGAR companyfacts (data_ml/edgar/<T>.csv).

Regla anti look-ahead: cada dato cuenta recién desde el día hábil SIGUIENTE a su fecha de presentación (`filed`),
y para cada período se usa la PRIMERA publicación (las re-expresiones posteriores se ignoran).
Flujos (ventas, márgenes, caja) se llevan a TTM: suma de los últimos 4 trimestres (Q4 = anual − 9 meses). Si el
emisor solo presenta anuales (20-F), se usa el último anual.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

CONCEPTS = {
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenuesNetOfInterestExpense", "Revenue"],
    "gp": ["GrossProfit"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "oi": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "ni": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
}
STOCKS = {"assets": ["Assets"], "liab": ["Liabilities"], "shares": ["EntityCommonStockSharesOutstanding"]}


def _load(path):
    d = pd.read_csv(path, parse_dates=["end", "start", "filed"])
    d = d.dropna(subset=["val", "end", "filed"])
    return d


def _pick_unit(d):
    if d.empty:
        return d
    u = "USD" if "USD" in set(d.unit) else d.unit.value_counts().index[0]
    return d[d.unit == u]


def _flow_events(d, tags):
    """Devuelve lista de (filed, ttm_value_series_by_end) — en la práctica, una tabla de trimestres con su
    primera fecha de publicación, incluyendo Q4 derivado."""
    sub = None
    for t in tags:                    # combina tags (algunas empresas cambian de tag en el tiempo)
        x = d[d.tag == t]
        sub = x if sub is None else pd.concat([sub, x[~x.end.isin(sub.end)]])
    if sub is None or sub.empty:
        return pd.DataFrame(columns=["end", "val", "filed", "kind"])
    sub = _pick_unit(sub)
    sub = sub.dropna(subset=["start"])
    dur = (sub.end - sub.start).dt.days
    first = lambda x: x.sort_values("filed").drop_duplicates("end", keep="first")
    q = first(sub[(dur >= 80) & (dur <= 100)])[["end", "val", "filed"]].assign(kind="Q")
    a = first(sub[(dur >= 350) & (dur <= 380)])[["end", "val", "filed"]].assign(kind="A")
    rows = []
    for _, r in a.iterrows():
        qs = q[(q.end > r.end - pd.Timedelta(days=360)) & (q.end <= r.end - pd.Timedelta(days=80))]
        if len(qs) == 3 and not (q.end == r.end).any():
            rows.append({"end": r.end, "val": r.val - qs.val.sum(), "filed": max(r.filed, qs.filed.max()), "kind": "Q"})
    q = pd.concat([q, pd.DataFrame(rows)], ignore_index=True) if rows else q
    return pd.concat([q, a], ignore_index=True).sort_values("end")


def _ttm_asof(ev, asof):
    """TTM y TTM de un año antes, con datos publicados hasta `asof`."""
    e = ev[ev.filed <= asof]
    if e.empty:
        return np.nan, np.nan
    q = e[e.kind == "Q"].sort_values("end").drop_duplicates("end", keep="first")
    if len(q) >= 4 and (asof - q.end.iloc[-1]).days < 200:
        last = q.end.iloc[-1]
        q4 = q[q.end > last - pd.Timedelta(days=350)]
        qprev = q[(q.end > last - pd.Timedelta(days=350 + 365)) & (q.end <= last - pd.Timedelta(days=350))]
        cur = q4.val.sum() if len(q4) == 4 else np.nan
        prev = qprev.val.sum() if len(qprev) == 4 else np.nan
        return cur, prev
    a = e[e.kind == "A"].sort_values("end")
    if len(a) and (asof - a.end.iloc[-1]).days < 500:
        prev = a.val.iloc[-2] if len(a) >= 2 else np.nan
        return a.val.iloc[-1], prev
    return np.nan, np.nan


def _stock_asof(d, tags, asof):
    x = d[d.tag.isin(tags)]
    x = _pick_unit(x)
    x = x[x.filed <= asof].sort_values(["end", "filed"])
    if x.empty or (asof - x.end.iloc[-1]).days > 400:
        return np.nan
    return x.val.iloc[-1]


def ticker_features(path):
    d = _load(path)
    if d.empty:
        return None
    usd_filer = d.form.astype(str).str.startswith("10-").any()
    ev = {k: _flow_events(d, tags) for k, tags in CONCEPTS.items()}
    dates = sorted(set(d.filed))
    rows = []
    for f in dates:
        v = {k: _ttm_asof(e, f) for k, e in ev.items()}
        rev, rev_p = v["rev"]
        gp = v["gp"][0]
        gp_p = v["gp"][1]
        if np.isnan(gp) and not np.isnan(v["cogs"][0]):
            gp, gp_p = rev - v["cogs"][0], rev_p - v["cogs"][1]
        oi, oi_p = v["oi"]
        ni = v["ni"][0]
        ocf = v["ocf"][0]
        capex = v["capex"][0]
        assets = _stock_asof(d, STOCKS["assets"], f)
        liab = _stock_asof(d, STOCKS["liab"], f)
        shares = _stock_asof(d, STOCKS["shares"], f)
        ok = rev is not None and rev > 0
        rows.append({
            "filed": f,
            "f_rev_g": rev / rev_p - 1 if ok and rev_p and rev_p > 0 else np.nan,
            "f_gm": gp / rev if ok else np.nan,
            "f_om": oi / rev if ok else np.nan,
            "f_om_chg": (oi / rev - oi_p / rev_p) if ok and rev_p and rev_p > 0 else np.nan,
            "f_fcf_margin": (ocf - (capex if not np.isnan(capex) else 0)) / rev if ok else np.nan,
            "f_accruals": (ni - ocf) / assets if assets and assets > 0 else np.nan,
            "f_lev": liab / assets if assets and assets > 0 else np.nan,
            "_ni": ni, "_fcf": ocf - (capex if not np.isnan(capex) else 0), "_shares": shares if usd_filer else np.nan,
        })
    out = pd.DataFrame(rows).set_index("filed").sort_index()
    for c in ["f_rev_g", "f_om_chg"]:
        out[c] = out[c].clip(-1, 3)
    for c in ["f_gm", "f_om", "f_fcf_margin"]:
        out[c] = out[c].clip(-2, 1)
    out["f_accruals"] = out["f_accruals"].clip(-0.5, 0.5)
    return out


def edgar_panel(edgar_dir, assets, close: pd.DataFrame):
    """Panel (date, ticker) con features EDGAR disponibles al cierre de cada día + valuación con precio diario."""
    idx = close.index
    parts = {}
    for t in assets:
        path = os.path.join(edgar_dir, f"{t}.csv")
        if not os.path.exists(path):
            continue
        f = ticker_features(path)
        if f is None or f.empty:
            continue
        pos = idx.searchsorted(f.index.values, side="right")   # disponible desde el día hábil siguiente
        keep = pos < len(idx)
        f = f[keep]
        f.index = idx[pos[keep]]
        f = f[~f.index.duplicated(keep="last")].reindex(idx).ffill(limit=400)
        mcap = close[t] * f["_shares"]
        f["f_ey"] = (f["_ni"] / mcap).clip(-0.5, 0.5)
        f["f_fcf_yield"] = (f["_fcf"] / mcap).clip(-0.5, 0.5)
        parts[t] = f.drop(columns=["_ni", "_fcf", "_shares"])
    panel = pd.concat(parts, names=["ticker", "date"]).swaplevel().sort_index()
    return panel
