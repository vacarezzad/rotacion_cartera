"""Manda reminder_body.md por SMTP (Gmail por defecto). Credenciales solo desde secrets del repo:
MAIL_USERNAME (cuenta que envía), MAIL_PASSWORD (contraseña de aplicación), MAIL_TO (opcional, destino)."""
import os
import smtplib
import sys
from email.message import EmailMessage

user, pwd = os.environ.get("MAIL_USERNAME"), os.environ.get("MAIL_PASSWORD")
if not user or not pwd:
    print("Sin MAIL_USERNAME/MAIL_PASSWORD: no se manda mail (queda el issue de GitHub).")
    sys.exit(0)
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
msg = EmailMessage()
msg["Subject"] = open(os.path.join(root, "reminder_subject.txt")).read().strip()
msg["From"] = user
msg["To"] = os.environ.get("MAIL_TO") or user
msg.set_content(open(os.path.join(root, "reminder_body.md")).read().replace("**", ""))
with smtplib.SMTP_SSL(os.environ.get("MAIL_SERVER", "smtp.gmail.com"), 465) as s:
    s.login(user, pwd)
    s.send_message(msg)
print("Mail enviado.")
