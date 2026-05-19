# -*- coding: utf-8 -*-
"""
KeyNest Locker Monitor - Python 3 para Render.com
Con alerta por email y llamada telefonica via Twilio
"""

import urllib.request
import urllib.parse
import smtplib
import json
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import base64

# CONFIG — variables de entorno en Render
ALERT_FROM     = os.environ.get("ALERT_FROM",    "bruno.moswalder@gmail.com")
ALERT_TO       = os.environ.get("ALERT_TO",      "bruno.moswalder@gmail.com")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD", "")
SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "")
TWILIO_SID     = os.environ.get("TWILIO_SID",    "")
TWILIO_TOKEN   = os.environ.get("TWILIO_TOKEN",  "")
TWILIO_FROM    = os.environ.get("TWILIO_FROM",   "")
TWILIO_TO      = os.environ.get("TWILIO_TO",     "")

LOCKERS_URL = "https://secure.keynest.com/PrivateLocker/List"

MY_LOCKERS = {
    "21979": "KIOSCO LAS HERAS",
    "21980": "KIOSCO SERRANO",
}

# Si el HTML es menor a este valor, la sesion probablemente expiro
HTML_MIN_LENGTH = 150000


def fetch(url):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
    req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    req.add_header("Accept-Language", "es-ES,es;q=0.7")
    req.add_header("Cookie", SESSION_COOKIE)
    resp = urllib.request.urlopen(req, timeout=20)
    return resp.read().decode("utf-8", errors="replace"), resp.geturl()


def parse_lockers(html):
    lockers = []
    for locker_id, locker_name in MY_LOCKERS.items():
        idx = html.find(locker_id)
        if idx == -1:
            continue  # No marcar offline si no se encuentra — puede ser sesion expirada
        fragment = html[max(0, idx - 2000):min(len(html), idx + 500)]
        status_match = re.search(r"\b(ONLINE|OFFLINE)\b", fragment, re.IGNORECASE)
        status = status_match.group(1).upper() if status_match else "UNKNOWN"
        lockers.append({"id": locker_id, "name": locker_name, "online": status == "ONLINE", "status": status})
    return lockers


def send_email(subject, body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = ALERT_FROM
        msg["To"]      = ALERT_TO
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(ALERT_FROM, SMTP_PASSWORD)
            srv.sendmail(ALERT_FROM, ALERT_TO, msg.as_string())
        log(f"Email enviado a {ALERT_TO}")
    except Exception as e:
        log(f"ERROR enviando email: {e}")


def send_alert(offline_lockers):
    now = datetime.now().strftime("%H:%M:%S del %d/%m/%Y")
    subject = " | ".join([f"{lk['name']} OFFLINE" for lk in offline_lockers])
    body = f"<p>Detectado a las <strong>{now}</strong></p><ul>"
    for lk in offline_lockers:
        body += f"<li><strong>{lk['name']} OFFLINE</strong> (ID: {lk['id']})</li>"
    body += f'</ul><p><a href="{LOCKERS_URL}">Ver en KeyNest</a></p>'
    send_email(subject, body)


def send_session_expired_alert():
    now = datetime.now().strftime("%H:%M:%S del %d/%m/%Y")
    subject = "⚠️ KeyNest Monitor — Sesion expirada"
    body = f"""
    <p>Detectado a las <strong>{now}</strong></p>
    <p>La cookie de sesion de KeyNest <strong>expiro</strong>.</p>
    <p>El monitor no puede verificar el estado de los lockers hasta que actualices la cookie.</p>
    <p><strong>Pasos:</strong></p>
    <ol>
        <li>Abri Chrome en secure.keynest.com (logueado)</li>
        <li>F12 → Application → Cookies → copia el valor de <code>.AspNet.ApplicationCookie</code></li>
        <li>En Render → Environment → actualiza <code>SESSION_COOKIE</code></li>
    </ol>
    """
    send_email(subject, body)


def make_call(offline_lockers):
    try:
        names = " y ".join([lk["name"] for lk in offline_lockers])
        message = f"Alerta KeyNest. {names} esta offline. Por favor verificar inmediatamente."
        twiml = f'<Response><Say language="es-MX">{message}</Say><Pause length="1"/><Say language="es-MX">{message}</Say></Response>'
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
        data = urllib.parse.urlencode({
            "To":    TWILIO_TO,
            "From":  TWILIO_FROM,
            "Twiml": twiml,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        credentials = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        req.add_header("Authorization", f"Basic {credentials}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = urllib.request.urlopen(req, timeout=20)
        result = json.loads(resp.read().decode())
        log(f"Llamada iniciada a {TWILIO_TO} — SID: {result.get('sid', 'N/A')}")
    except Exception as e:
        log(f"ERROR en llamada Twilio: {e}")


STATE_FILE = "/tmp/keynest_state.json"

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"offline_ids": [], "session_alert_sent": False}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"ERROR guardando estado: {e}")


def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def main():
    log("--- Verificacion iniciada ---")
    if not SESSION_COOKIE:
        log("ERROR: SESSION_COOKIE no configurada.")
        return
    try:
        html, final_url = fetch(LOCKERS_URL)
        if "Login" in final_url:
            log("SESION EXPIRADA: redirigido al login.")
            send_session_expired_alert()
            return
        log(f"Pagina obtenida. HTML: {len(html)} chars")
    except Exception as e:
        log(f"ERROR obteniendo pagina: {e}")
        return

    # Verificar que el HTML tenga el tamaño esperado (sesion valida)
    if len(html) < HTML_MIN_LENGTH:
        log(f"ADVERTENCIA: HTML demasiado pequeño ({len(html)} chars) — posible sesion expirada. No se envia alerta de offline.")
        state = load_state()
        if not state.get("session_alert_sent"):
            send_session_expired_alert()
            state["session_alert_sent"] = True
            save_state(state)
        return

    lockers = parse_lockers(html)
    if not lockers:
        log("ADVERTENCIA: No se encontraron lockers.")
        return

    # Sesion valida — resetear flag
    state = load_state()
    state["session_alert_sent"] = False

    offline = [lk for lk in lockers if not lk["online"]]
    online  = [lk for lk in lockers if lk["online"]]
    log(f"Estado: {len(online)} online | {len(offline)} offline | Total: {len(lockers)}")

    prev_offline_ids = set(state.get("offline_ids", []))
    curr_offline_ids = {lk["id"] for lk in offline}

    newly_offline = [lk for lk in offline if lk["id"] not in prev_offline_ids]
    for rid in prev_offline_ids - curr_offline_ids:
        log(f"Recuperado: {MY_LOCKERS.get(rid, rid)}")

    if newly_offline:
        log(f"Nuevos offline: {[lk['name'] for lk in newly_offline]}")
        send_alert(newly_offline)
        make_call(newly_offline)

    state["offline_ids"] = list(curr_offline_ids)
    save_state(state)
    log("--- Verificacion finalizada ---")


if __name__ == "__main__":
    main()
