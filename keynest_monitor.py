# -*- coding: utf-8 -*-
"""
KeyNest Locker Monitor - Python 3 para Render.com
"""

import urllib.request
import urllib.parse
import http.cookiejar
import smtplib
import json
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# CONFIG — se leen desde variables de entorno en Render
ALERT_FROM    = os.environ.get("ALERT_FROM",    "bruno.moswalder@gmail.com")
ALERT_TO      = os.environ.get("ALERT_TO",      "bruno.moswalder@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SESSION_COOKIE = os.environ.get("SESSION_COOKIE", "")

LOCKERS_URL = "https://secure.keynest.com/PrivateLocker/List"
MY_LOCKER_IDS = ["21979", "21980"]


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
    for locker_id in MY_LOCKER_IDS:
        idx = html.find(locker_id)
        if idx == -1:
            log(f"ADVERTENCIA: No se encontro Locker ID {locker_id}")
            continue
        fragment = html[max(0, idx - 2000):min(len(html), idx + 500)]
        name_matches = re.findall(r"<h[2-5][^>]*>\s*([A-Z][^<]{5,80})\s*</h[2-5]>", fragment)
        name = name_matches[-1].strip() if name_matches else f"Locker {locker_id}"
        status_match = re.search(r"\b(ONLINE|OFFLINE)\b", fragment, re.IGNORECASE)
        status = status_match.group(1).upper() if status_match else "UNKNOWN"
        lockers.append({"id": locker_id, "name": name, "online": status == "ONLINE", "status": status})
    return lockers


def send_alert(offline_lockers):
    try:
        now = datetime.now().strftime("%H:%M:%S del %d/%m/%Y")
        subject = f"Alerta KeyNest — {len(offline_lockers)} locker(s) OFFLINE"
        body = f"<p>Detectado a las <strong>{now}</strong></p><ul>"
        for lk in offline_lockers:
            body += f"<li><strong>{lk['name']}</strong> (ID: {lk['id']})</li>"
        body += f'</ul><p><a href="{LOCKERS_URL}">Ver en KeyNest</a></p>'
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = ALERT_FROM
        msg["To"]      = ALERT_TO
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(ALERT_FROM, SMTP_PASSWORD)
            srv.sendmail(ALERT_FROM, ALERT_TO, msg.as_string())
        log(f"Alerta enviada a {ALERT_TO}")
    except Exception as e:
        log(f"ERROR enviando email: {e}")


STATE_FILE = "/tmp/keynest_state.json"

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"offline_ids": []}


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
        log("ERROR: SESSION_COOKIE no configurada en variables de entorno.")
        return
    try:
        html, final_url = fetch(LOCKERS_URL)
        if "Login" in final_url:
            log("SESION EXPIRADA: Actualizar SESSION_COOKIE en las variables de entorno de Render.")
            return
        log(f"Pagina obtenida. HTML: {len(html)} chars")
    except Exception as e:
        log(f"ERROR obteniendo pagina: {e}")
        return

    lockers = parse_lockers(html)
    if not lockers:
        log("ADVERTENCIA: No se encontraron lockers.")
        return

    offline = [lk for lk in lockers if not lk["online"]]
    online  = [lk for lk in lockers if lk["online"]]
    log(f"Estado: {len(online)} online | {len(offline)} offline | Total: {len(lockers)}")

    state = load_state()
    prev_offline_ids = set(state.get("offline_ids", []))
    curr_offline_ids = {lk["id"] for lk in offline}

    newly_offline = [lk for lk in offline if lk["id"] not in prev_offline_ids]
    for rid in prev_offline_ids - curr_offline_ids:
        log(f"Recuperado: ID {rid}")

    if newly_offline:
        log(f"Nuevos offline: {[lk['name'] for lk in newly_offline]}")
        send_alert(newly_offline)

    save_state({"offline_ids": list(curr_offline_ids)})
    log("--- Verificacion finalizada ---")


if __name__ == "__main__":
    main()
