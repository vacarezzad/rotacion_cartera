"""Manda reminder_whatsapp.txt (mensaje corto) por Telegram.
Secrets: TELEGRAM_TOKEN (de @BotFather) y TELEGRAM_CHAT_ID (tu chat con el bot)."""
import json
import os
import sys
import urllib.parse
import urllib.request

token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
if not token or not chat:
    print("Sin TELEGRAM_TOKEN/TELEGRAM_CHAT_ID: no se manda Telegram.")
    sys.exit(0)
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
text = open(os.path.join(root, "reminder_whatsapp.txt")).read()
data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=60) as r:
    ok = json.loads(r.read()).get("ok")
print("Telegram enviado." if ok else "Telegram respondió sin ok (revisar).")
sys.exit(0 if ok else 1)
