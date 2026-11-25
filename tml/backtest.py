"""Backtest diario por eventos. Señal al cierre de t, ejecución a la apertura de t+1,
salidas TP/SL/tiempo idénticas al etiquetado triple-barrier, costos por lado."""
from __future__ import annotations
import numpy as np
import pandas as pd


def run_backtest(pred: pd.DataFrame, labels: pd.DataFrame, p: dict, cfg, idle_mode=None, bench="SPY"):
    idle_mode = idle_mode or cfg.idle_mode
    c = cfg.cost_per_side
    dates = pred.index.get_level_values("date").unique().sort_values()
    allD = p["close"].index
    i0, i1 = allD.searchsorted(dates[0]), allD.searchsorted(dates[-1])
    D = allD[i0: i1 + 1]
    O, C = p["open"], p["close"]
    spyO, spyC = O[bench], C[bench]

    sig = pred[pred.prob >= pred.thr].join(labels[["ret_tb", "k_exit", "entry_px"]], how="left")
    sig = sig.dropna(subset=["ret_tb"])
    sig_by_date = {d: g.droplevel("date").sort_values("prob", ascending=False) for d, g in sig.groupby(level="date")}
    idx_of = {d: i for i, d in enumerate(allD)}

    cash = 1.0 if idle_mode == "cash" else 0.0
    spy_sh = 0.0 if idle_mode == "cash" else 1.0 / spyC.loc[D[0]]
    positions, pending, trades, curve = [], [], [], []

    for d in D:
        gi = idx_of[d]
        # 1) entradas a la apertura
        for tk, alloc, exit_gi, exit_px in pending:
            px = O.at[d, tk]
            if not np.isfinite(px):
                continue
            if idle_mode == "spy":
                alloc = min(alloc, spy_sh * spyO.at[d])
                spy_sh -= alloc / spyO.at[d]
                invest = alloc * (1 - c) * (1 - c)
            else:
                alloc = min(alloc, cash)
                cash -= alloc
                invest = alloc * (1 - c)
            if alloc <= 0:
                continue
            positions.append(dict(tk=tk, sh=invest / px, exit_gi=exit_gi, exit_px=exit_px,
                                  alloc=alloc, entry=d, entry_px=px))
        pending = []
        # 2) salidas
        keep = []
        for pos in positions:
            if pos["exit_gi"] == gi:
                proceeds = pos["sh"] * pos["exit_px"] * (1 - c)
                if idle_mode == "spy":
                    spy_sh += proceeds * (1 - c) / spyC.at[d]
                    net_back = proceeds * (1 - c)
                else:
                    cash += proceeds
                    net_back = proceeds
                trades.append(dict(ticker=pos["tk"], entry=pos["entry"], exit=d,
                                   gross=pos["exit_px"] / pos["entry_px"] - 1,
                                   net=net_back / pos["alloc"] - 1))
            else:
                keep.append(pos)
        positions = keep
        # 3) valuación al cierre
        eq = cash + spy_sh * spyC.at[d] + sum(pos["sh"] * C.at[d, pos["tk"]] for pos in positions)
        curve.append((d, eq, len(positions)))
        # 4) señales al cierre -> órdenes para mañana
        if d in sig_by_date and gi + 1 < len(allD):
            held = {pos["tk"] for pos in positions}
            free = cfg.max_positions - len(positions)
            for tk, row in sig_by_date[d].iterrows():
                if free <= 0:
                    break
                if tk in held:
                    continue
                pending.append((tk, eq / cfg.max_positions, gi + int(row.k_exit), row.entry_px * (1 + row.ret_tb)))
                free -= 1

    curve = pd.DataFrame(curve, columns=["date", "equity", "n_pos"]).set_index("date")
    return curve, pd.DataFrame(trades)


def buy_and_hold(p, tickers, start, end, rebalance="ME"):
    C = p["close"].loc[start:end, list(tickers)]
    r = C.pct_change().fillna(0)
    if len(tickers) == 1:
        return (1 + r.iloc[:, 0]).cumprod()
    # equiponderado con rebalanceo mensual (sin costos)
    eq, val, w = [], 1.0, None
    month = None
    for d, row in r.iterrows():
        avail = C.loc[d].notna()
        if month != d.month or w is None:
            w = avail / avail.sum()
            month = d.month
        val *= 1 + (w * row).sum()
        w = w * (1 + row)
        w = w / w.sum()
        eq.append(val)
    return pd.Series(eq, index=r.index)


def metrics(equity: pd.Series, bench: pd.Series | None = None, trades: pd.DataFrame | None = None) -> dict:
    e = equity.dropna()
    r = e.pct_change().dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    out = {
        "CAGR": e.iloc[-1] ** (1 / yrs) - 1,
        "Vol": r.std() * np.sqrt(252),
        "Sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
        "MaxDD": (e / e.cummax() - 1).min(),
    }
    out["Calmar"] = out["CAGR"] / abs(out["MaxDD"]) if out["MaxDD"] < 0 else np.nan
    if bench is not None:
        b = bench.reindex(e.index).pct_change().dropna()
        ex = (r - b).dropna()
        out["Exceso_anual"] = ex.mean() * 252
        out["IR"] = ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan
    if trades is not None and len(trades):
        out["Trades"] = len(trades)
        out["Trades_por_año"] = len(trades) / yrs
        out["Hit_rate"] = (trades.net > 0).mean()
        out["Ret_medio_trade_neto"] = trades.net.mean()
    return out
