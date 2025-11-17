# apps/bcservices/bcservices/api/utils.py
import json, time
import frappe
import jwt
import requests
from jwt import PyJWKClient
from frappe.utils import now_datetime, cint, flt

# -------- Clerk helpers --------

def _clerk_issuer():
    url = frappe.conf.get("clerk_issuer")
    if not url:
        frappe.throw("Clerk issuer is not configured", frappe.ConfigurationError)
    return url.rstrip("/")

def _clerk_secret():
    key = frappe.conf.get("clerk_secret_key")
    if not key:
        frappe.throw("Clerk secret key is not configured", frappe.ConfigurationError)
    return key

def _jwks_client():
    cache_key = "bc_jwks_url"
    url = frappe.cache().get_value(cache_key)
    if not url:
        url = f"{_clerk_issuer()}/.well-known/jwks.json"
        frappe.cache().set_value(cache_key, url, expires_in_sec=3600)
    return PyJWKClient(url)

def verify_clerk_bearer_and_get_sub():
    auth = frappe.get_request_header("Authorization") or frappe.get_request_header("authorization")
    if not auth or not auth.startswith("Bearer "):
        frappe.throw("Missing Authorization header", frappe.PermissionError)
    token = auth.split(" ", 1)[1]
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=None,               # Clerk sessions nemusia mať aud
            issuer=_clerk_issuer(),
            options={"verify_aud": False}
        )
        return payload.get("sub"), payload
    except Exception as e:
        frappe.throw(f"Invalid Clerk token: {e}", frappe.PermissionError)

def clerk_api(path, method="GET", json_body=None):
    url = f"https://api.clerk.com{path}"
    headers = {
        "Authorization": f"Bearer {_clerk_secret()}",
        "Content-Type": "application/json"
    }
    resp = requests.request(method, url, headers=headers, json=json_body, timeout=30)
    if not (200 <= resp.status_code < 300):
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        frappe.throw(f"Clerk API error {resp.status_code}: {detail}")
    return resp.json()

def ensure_bc_user_by_clerk(clerk_id: str, email: str | None = None):
    """Upsert BC Pouzivatel podľa clerk_id. Vráti doc."""
    name = frappe.db.get_value("BC Pouzivatel", {"clerk_id": clerk_id}, "name")
    if name:
        doc = frappe.get_doc("BC Pouzivatel", name)
        # aktualizuj email, ak chýba
        if email and not doc.email:
            frappe.db.set_value("BC Pouzivatel", name, "email", email)
        return doc

    # ak nemáme email v tokene, skús dotiahnuť z Clerka
    if not email:
        try:
            u = clerk_api(f"/v1/users/{clerk_id}")
            email = (u.get("primary_email_address_id") and
                     next((e["email_address"] for e in u.get("email_addresses", [])
                           if e["id"] == u["primary_email_address_id"]), None)) or None
        except Exception:
            pass

    doc = frappe.get_doc({
        "doctype": "BC Pouzivatel",
        "clerk_id": clerk_id,
        "email": email
    })
    doc.insert(ignore_permissions=True)
    return doc

def ensure_settings():
    try:
        return frappe.get_single("BC Nastavenia")
    except Exception:
        # ak Single ešte neexistuje (prvýkrát), vytvor ho
        doc = frappe.new_doc("BC Nastavenia")
        doc.aktualna_cena_eur = 0
        doc.insert(ignore_permissions=True)
        return doc

def get_conf_int(key, default):
    val = frappe.conf.get(key)
    try:
        return int(val)
    except Exception:
        return int(default)

def get_conf_float(key, default):
    val = frappe.conf.get(key)
    try:
        return float(val)
    except Exception:
        return float(default)

# -------- APNs (VoIP) --------

# -------- APNs (VoIP) – priame volanie cez HTTP/2 --------
import httpx
from pathlib import Path

_apns_cached_token = {"token": None, "iat": 0}

def _build_apns_jwt():
    # Apple akceptuje JWT ~1 hodinu → cache-uj, nech nepodpísujeme pri každom pushe
    now = int(time.time())
    if _apns_cached_token["token"] and now - _apns_cached_token["iat"] < 50 * 60:
        return _apns_cached_token["token"]

    key_file = frappe.conf.get("apn_key_file")
    key_id = frappe.conf.get("apn_key_id")
    team_id = frappe.conf.get("apn_team_id")
    if not (key_file and key_id and team_id):
        frappe.throw("APNs config missing", frappe.ConfigurationError)

    with open(key_file, "rb") as f:
        p8 = f.read()

    token = jwt.encode(
        {"iss": team_id, "iat": now},
        p8,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    _apns_cached_token.update({"token": token, "iat": now})
    return token

def send_voip_push(device_token: str, payload: dict):
    bundle_id = frappe.conf.get("apn_bundle_id")
    prod = cint(frappe.conf.get("apn_production") or 0) == 1
    host = "https://api.push.apple.com" if prod else "https://api.sandbox.push.apple.com"
    url = f"{host}/3/device/{device_token}"

    jwt_token = _build_apns_jwt()
    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": f"{bundle_id}.voip",
        "apns-push-type": "voip",
        "apns-expiration": str(int(time.time()) + 30),
        "content-type": "application/json",
    }

    with httpx.Client(http2=True, timeout=10) as client:
        resp = client.post(url, headers=headers, content=json.dumps(payload))

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        frappe.log_error(f"APNs error {resp.status_code}: {detail}", "BC APNs")
        frappe.throw(f"APNs error {resp.status_code}: {detail}")

    return {"apns_id": resp.headers.get("apns-id")}



# -------- Helpers pre child table BC Zariadenie --------

def upsert_child_device_for_user(user_doc, voip_token: str = None, apns_token: str = None):
    """BC Zariadenie je child -> hľadáme v `user_doc.zariadenia` záznam s tým istým tokenom; ak nie je, append."""
    modified = False
    # odstráň duplicitu v iných rodičoch (query child tabuľky podľa tokenu)
    if voip_token:
        rows = frappe.get_all("BC Zariadenie", filters={"voip_token": voip_token},
                              fields=["name", "parent"])
        for r in rows:
            if r["parent"] != user_doc.name:
                # vymaž cudzí child záznam s týmto tokenom
                frappe.db.delete("BC Zariadenie", {"name": r["name"]})

    # nájdi existujúci v rámci usera
    found = None
    for ch in user_doc.get("zariadenie") or []:
        if voip_token and ch.voip_token == voip_token:
            found = ch
            break
        if apns_token and ch.apns_token == apns_token:
            found = ch
            break

    if found:
        # update
        if voip_token and found.voip_token != voip_token:
            found.voip_token = voip_token
            modified = True
        if apns_token and found.apns_token != apns_token:
            found.apns_token = apns_token
            modified = True
    else:
        user_doc.append("zariadenie", {
            "doctype": "BC Zariadenie",
            "voip_token": voip_token,
            "apns_token": apns_token
        })
        modified = True

    if modified:
        user_doc.save(ignore_permissions=True)
    return True
