"""Manda reminder_whatsapp.txt por WhatsApp con CallMeBot (gratis, uso personal).
Secrets: WHATSAPP_PHONE (con código de país, ej. +549...) y WHATSAPP_APIKEY (lo da CallMeBot al activarlo)."""
import os
import sys
import urllib.parse
import urllib.request

phone, key = os.environ.get("WHATSAPP_PHONE"), os.environ.get("WHATSAPP_APIKEY")
if not phone or not key:
    print("Sin WHATSAPP_PHONE/WHATSAPP_APIKEY: no se manda WhatsApp.")
    sys.exit(0)
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
text = open(os.path.join(root, "reminder_whatsapp.txt")).read()
q = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
with urllib.request.urlopen(f"https://api.callmebot.com/whatsapp.php?{q}", timeout=60) as r:
    body = r.read().decode("utf-8", "replace")
ok = r.status == 200 and any(w in body.lower() for w in ("queued", "sent"))
print("WhatsApp enviado." if ok else "Respuesta de CallMeBot sin confirmación (revisar).")
