# apps/bcservices/bcservices/api/utils.py

import time
import secrets
import frappe
import jwt
from frappe.utils import cint
import httpx

# ---------------------------------------------------
# Settings loader
# ---------------------------------------------------

def get_settings():
    """Load SINGLE doctype 'Nastavenie'."""
    try:
        return frappe.get_single("Nastavenie")
    except Exception:
        frappe.throw("Doctype 'Nastavenie' neexistuje", frappe.ValidationError)

# ---------------------------------------------------
# Frappe-native auth (HS256 JWT, identita = email)
# ---------------------------------------------------

def _jwt_secret():
    """Tajny kluc na podpisovanie JWT. Ulozeny v Nastavenie, generuje sa pri prvom pouziti."""
    settings = get_settings()
    val = getattr(settings, "jwt_secret", None)
    if not val:
        val = secrets.token_urlsafe(48)
        frappe.db.set_value("Nastavenie", settings.name, "jwt_secret", val)
        frappe.db.commit()
    return val


def make_jwt(email: str, role: str) -> str:
    """Vytvori podpisany JWT bez expiracie (uzivatel ostava prihlaseny)."""
    token = jwt.encode(
        {"sub": email, "role": role, "iat": int(time.time())},
        _jwt_secret(),
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode()
    return token


def verify_bearer_and_get_email():
    """Overi nas HS256 JWT z auth headeru a vrati (email, payload)."""
    auth = (
        frappe.get_request_header("X-Clerk-Authorization")
        or frappe.get_request_header("x-clerk-authorization")
        or frappe.get_request_header("Authorization")
        or frappe.get_request_header("authorization")
    )

    if not auth:
        frappe.throw("Missing auth header", frappe.PermissionError)

    token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else auth.strip()

    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return payload.get("sub"), payload
    except Exception as e:
        frappe.throw(f"Invalid token: {e}", frappe.PermissionError)


# ---------------------------------------------------
# APNs / VoIP JWT
# ---------------------------------------------------

_apns_cached_token = {"token": None, "iat": 0}

import os

def _build_apns_jwt():
    now = int(time.time())

    if _apns_cached_token["token"] and now - _apns_cached_token["iat"] < 3000:
        return _apns_cached_token["token"]

    settings = get_settings()

    # RELATÍVNA cesta z DocType
    key_file = settings.apn_key_file

    # PREVEDIE NA ABSOLÚTNU CESTU
    site_path = frappe.get_site_path()
    full_path = os.path.join(site_path, key_file.lstrip("/"))

    key_id = settings.apn_key_id
    team_id = settings.apn_team_id

    if not (full_path and key_id and team_id):
        frappe.throw("APNs config missing in Nastavenie", frappe.ValidationError)

    try:
        with open(full_path, "rb") as f:
            p8 = f.read()
    except Exception as e:
        frappe.throw(f"APNs key file error: {e}", frappe.ValidationError)

    token = jwt.encode(
        {"iss": team_id, "iat": now},
        p8,
        algorithm="ES256",
        headers={"kid": key_id},
    )

    if isinstance(token, bytes):
        token = token.decode()

    _apns_cached_token.update({"token": token, "iat": now})
    return token


def _cleanup_dead_token_if_needed(resp, device_token: str, token_field: str):
    """
    Ak APNs vrati 400 BadDeviceToken / 410 Unregistered / DeviceTokenNotForTopic,
    token uz nikdy nebude fungovat -> vymazeme zariadenie z DB,
    aby sa nan neskusalo pushovat donekonecna.
    token_field: 'voip_token' alebo 'apns_token'
    """
    if resp.status_code not in (400, 410):
        return

    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = None

    if reason not in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
        return

    try:
        frappe.db.sql(
            f"DELETE FROM `tabZariadenie` WHERE `{token_field}`=%s",
            (device_token,),
        )
        frappe.db.commit()
        frappe.logger().info(
            f"🧹 Deleted dead device ({token_field}, reason={reason}): {device_token[:12]}…"
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "APNS Dead Token Cleanup Error")


def send_voip_push(device_token: str, payload: dict):
    """Odošle VoIP Push notifikáciu na zadaný Apple device token."""
    settings = get_settings()

    bundle_id = settings.apn_bundle_id
    prod = cint(settings.apn_production) == 1
    host = "https://api.push.apple.com" if prod else "https://api.sandbox.push.apple.com"
    url = f"{host}/3/device/{device_token}"

    jwt_token = _build_apns_jwt()

    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": f"{bundle_id}.voip",
        "apns-push-type": "voip",
        "content-type": "application/json",
    }

    with httpx.Client(http2=True, timeout=10) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code != 200:
        # V prípade chyby nevyhadzujeme throw, aby nezlyhal celý proces start_call, len zalogujeme
        frappe.log_error(f"APNs error {resp.status_code}: {resp.text}", "APNS Critical Error")
        _cleanup_dead_token_if_needed(resp, device_token, "voip_token")
        return None

    return {"apns_id": resp.headers.get("apns-id")}

def get_actor_by_email(email: str):
    """
    Vráti tuple: (doctype, doc) podľa emailu.
    """
    poradca = frappe.db.get_value("Poradca", {"email": email}, "name")
    if poradca:
        return "Poradca", frappe.get_doc("Poradca", poradca)

    klient = frappe.db.get_value("Klient", {"email": email}, "name")
    if klient:
        return "Klient", frappe.get_doc("Klient", klient)

    return None, None


def send_chat_push(device_token: str, title: str, body: str, custom_data: dict = None):
    """
    Odošle štandardnú (Chat) Push notifikáciu.
    Rozdiel oproti VoIP: iný topic, iný push-type, iný payload.
    """
    settings = get_settings()

    bundle_id = settings.apn_bundle_id
    # POZOR: Pre chat notifikácie sa NEPOUŽÍVA prípona .voip!
    # Topic musí byť presne tvoje Bundle ID (napr. com.firma.app)
    topic = bundle_id 
    
    prod = cint(settings.apn_production) == 1
    host = "https://api.push.apple.com" if prod else "https://api.sandbox.push.apple.com"
    url = f"{host}/3/device/{device_token}"

    jwt_token = _build_apns_jwt() # Použijeme tú istú funkciu na JWT ako pri VoIP

    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": topic,
        "apns-push-type": "alert",    # ZMENA: VoIP má 'voip', Chat má 'alert'
        "apns-priority": "10",        # 10 = poslať ihneď
        "content-type": "application/json",
    }

    # Štandardný Apple payload pre správy
    payload = {
        "aps": {
            "alert": {
                "title": title,
                "body": body
            },
            "sound": "default",
            "badge": 1,
            "content-available": 1 # Umožní zobudiť appku na pozadí
        }
    }
    
    # Pridáme vlastné dáta (napr. kto poslal správu, aby sa otvoril správny chat)
    if custom_data:
        payload.update(custom_data)

    # Odoslanie requestu (rovnako ako pri VoIP)
    try:
        with httpx.Client(http2=True, timeout=10) as client:
            resp = client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            frappe.log_error(f"APNs Chat Error {resp.status_code}: {resp.text}", "APNS Chat Error")
            _cleanup_dead_token_if_needed(resp, device_token, "apns_token")
            return False
            
        return True
    except Exception as e:
        frappe.log_error(f"APNs Connection Error: {str(e)}", "APNS Exception")
        return False
# ---------------------------------------------------
# Device helper
# ---------------------------------------------------

def upsert_child_device_for_user(user_doc, voip_token=None, apns_token=None):
    # odstráň rovnaký token inde (OK, to máš správne)
    if voip_token:
        frappe.db.sql("""
            DELETE FROM `tabZariadenie`
            WHERE voip_token=%s
              AND NOT (parent=%s AND parenttype=%s)
        """, (voip_token, user_doc.name, user_doc.doctype))

    user_doc.reload()

    devices = user_doc.get("zariadenie") or []

    # update existujúce
    for d in devices:
        if voip_token and d.voip_token == voip_token:
            if apns_token:
                d.apns_token = apns_token
            user_doc.save(ignore_permissions=True)
            frappe.db.commit()
            return True

    # append nové zariadenie
    row = user_doc.append("zariadenie", {})
    row.voip_token = voip_token
    row.apns_token = apns_token

    user_doc.save(ignore_permissions=True)
    frappe.db.commit()
    return True
