"""Baja de Dataroma (superinversores, 13F) la página de cada acción del universo y guarda el HTML crudo.
El parseo vive en scripts/parse_dataroma.py. Corre en GitHub Actions (el container no llega a Dataroma)."""
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from make_signal import UNIVERSE  # noqa: E402

OUT = os.path.join(ROOT, "data", "dataroma", "raw")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
os.makedirs(OUT, exist_ok=True)
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


for t in UNIVERSE:
    try:
        open(os.path.join(OUT, f"{t}_act.html"), "w").write(get(f"https://www.dataroma.com/m/activity.php?sym={t}&typ=a"))
        time.sleep(1.5)
    except Exception as e:
        print(t, "actividad ERROR", e)
    url = f"https://www.dataroma.com/m/stock.php?sym={t}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=40) as r:
            html = r.read().decode("utf-8", "replace")
        open(os.path.join(OUT, f"{t}.html"), "w").write(html)
        print(t, "OK", len(html))
    except Exception as e:
        print(t, "ERROR", e)
    time.sleep(2)
