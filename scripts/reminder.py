"""Recordatorio de rebalanceo: corre todos los días hábiles y solo arma el aviso la víspera del rebalanceo.

Escribe en $GITHUB_OUTPUT  send=true|false  y, si corresponde, reminder_subject.txt y reminder_body.md.
Uso: python scripts/reminder.py [--force] [--today YYYY-MM-DD]
"""
import argparse
import glob
import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("GITHUB_REPOSITORY", "vacarezzad/rotacion_cartera")
SITE = os.environ.get("SITE_URL", "")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def next_rebalance(today):
    for m in range(0, 15):
        d = (today + pd.offsets.MonthEnd(m)).normalize()
        if d.month in (1, 4, 7, 10):
            bd = pd.bdate_range(d - pd.Timedelta(days=7), d)[-1]
            if bd >= today.normalize():
                return bd
    return today


def larga(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kv.items()]
    if path:
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print(*lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="arma el aviso aunque no sea la víspera (para probar)")
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    today = pd.Timestamp(a.today) if a.today else pd.Timestamp.now(tz="America/Argentina/Buenos_Aires").tz_localize(None).normalize()
    reb = next_rebalance(today)
    eve = reb - pd.offsets.BDay(1)
    is_eve = today.normalize() == eve.normalize()
    print(f"hoy {today.date()} · próximo rebalanceo {reb.date()} · víspera {eve.date()}")
    if not (is_eve or a.force):
        output(send="false")
        return

    files = sorted(f for f in glob.glob(os.path.join(ROOT, "signals", "*.csv")) if os.path.basename(f)[:4].isdigit())
    s = pd.read_csv(files[-1])
    sig_date = os.path.basename(files[-1])[:-4]
    held = json.load(open(os.path.join(ROOT, "portfolio.json"))).get("held", [])
    picks = s.loc[s.en_cartera.astype(str).str.contains("✅"), "ticker"].tolist()
    buy = [t for t in picks if t not in held]
    sell = [t for t in held if t not in picks]
    keep = [t for t in held if t in picks]
    execd = reb + pd.offsets.BDay(1)

    subject = f"Mañana se rota la cartera de CEDEARs ({larga(reb)})"
    body = [f"Hola Diego,", "",
            f"Mañana, **{larga(reb)}**, es el rebalanceo trimestral. La señal se toma al cierre de mañana y las órdenes "
            f"se ejecutan en la apertura del **{larga(execd)}**.", "",
            f"**Cartera objetivo según la señal de hoy ({sig_date})**, 20% cada uno:", ""]
    for t in picks:
        r = s[s.ticker == t].iloc[0]
        notes = "" if pd.isna(r.contexto) else f" · {r.contexto}"
        body.append(f"- **{t}** (#{int(r.puesto)}) {r.semaforo}{notes}")
    body.append("")
    if held:
        body += [f"**Qué hacer contra tu cartera anotada** ({', '.join(held)}):", "",
                 f"- Comprar: {', '.join(buy) if buy else 'nada'}",
                 f"- Vender: {', '.join(sell) if sell else 'nada'}",
                 f"- Mantener y re-equilibrar a 20%: {', '.join(keep) if keep else 'nada'}", ""]
    else:
        body += ["`portfolio.json` está vacío, así que la señal arma la cartera desde cero.", ""]
    body += ["Antes de operar, mirá la señal actualizada de mañana al cierre (puede cambiar algún puesto) y, "
             "después de comprar, anotá los tickers en `portfolio.json` para que el buffer funcione.", ""]
    if SITE:
        body.append(f"Web: {SITE}")
    body.append(f"Señal completa: https://github.com/{REPO}/blob/main/signals/latest.md")
    body += ["", "Esto no es asesoramiento financiero."]
    open(os.path.join(ROOT, "reminder_subject.txt"), "w").write(subject)
    open(os.path.join(ROOT, "reminder_body.md"), "w").write("\n".join(body) + "\n")
    print(subject)
    print("\n".join(body))
    output(send="true")


if __name__ == "__main__":
    main()
