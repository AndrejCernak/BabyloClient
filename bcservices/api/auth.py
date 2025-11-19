# apps/bcservices/bcservices/api/auth.py
from __future__ import annotations

import re
import random
import jwt
import frappe
from frappe.utils.password import get_decrypted_password

from .utils import (
    verify_clerk_bearer_and_get_sub,
    clerk_api,
    ensure_bc_user_by_clerk,
    _jwks_client,
    _clerk_issuer,
)

# -----------------------------------------------------------------------------
# PUBLIC API – iOS / klient
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"], allow_guest=True)
def sync_user():
    """
    iOS -> /api/method/bcservices.api.auth.sync_user  (Authorization: Bearer <Clerk JWT>)
    - overí Clerk JWT
    - založí lokálneho BC Pouzivatela (ak neexistuje)
    - v Clerku nastaví public_metadata.role="client" (ak chýba)
    """
    clerk_id, payload = verify_clerk_bearer_and_get_sub()
    doc = ensure_bc_user_by_clerk(clerk_id)

    try:
        u = clerk_api(f"/v1/users/{clerk_id}")
        pub = (u.get("public_metadata") or {})
        if pub.get("role") != "client":
            pub["role"] = "client"
            clerk_api(f"/v1/users/{clerk_id}", method="PATCH", json_body={"public_metadata": pub})
    except Exception as e:
        frappe.log_error(f"Clerk role sync failed: {e}", "BC Clerk Sync")

    return {"ok": True}


@frappe.whitelist(methods=["GET"], allow_guest=True)
def sso(token: str | None = None):
    """
    /api/method/bcservices.api.auth.sso?token=<clerk_jwt>
    - overí jednorazový Clerk JWT
    - vytvorí sign-in token v Clerku
    - presmeruje na APP_URL/sso/callback?token=...
    """
    if not token:
        frappe.throw("Missing token", frappe.ValidationError)

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=_clerk_issuer(),
            options={"verify_aud": False},
        )
        clerk_id = payload.get("sub")
        if not clerk_id:
            frappe.throw("Invalid token (missing sub)", frappe.PermissionError)
    except Exception as e:
        frappe.throw(f"Invalid or expired token: {e}", frappe.PermissionError)

    res = clerk_api(
        "/v1/sign_in_tokens",
        method="POST",
        json_body={"user_id": clerk_id, "expires_in_seconds": 60},
    )
    sign_in_token = res.get("token")
    app_url = (frappe.conf.get("app_url") or "").rstrip("/")
    redirect_to = f"{app_url}/sso/callback?token={sign_in_token}"

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = redirect_to


# -----------------------------------------------------------------------------
# INTERNÉ – vytváranie / update užívateľa v Clerku (username support)
# -----------------------------------------------------------------------------

def _normalize_username_base(email_or_hint: str | None) -> str:
    """
    Z emailu / hintu vyrobí základ pre username (povolené: a-z 0-9 . _ -).
    """
    base = (email_or_hint or "user").split("@")[0].lower()
    base = re.sub(r"[^a-z0-9._-]", "", base).strip("._-")
    return base or "user"


def _create_clerk_user(email: str, password: str | None, preferred_username: str | None = None) -> dict:
    """
    Vytvorí používateľa v Clerku. Ak inštancia vyžaduje 'username',
    pokúsi sa ho poslať. Pri konflikte/validácii skúsi niekoľko variantov.
    """
    uname_base = _normalize_username_base(preferred_username or email)
    attempts = 6
    last_err: Exception | None = None

    for i in range(attempts):
        uname = uname_base if i == 0 else f"{uname_base}{random.randint(1000, 9999)}"
        body = {
            "email_address": [email],
            "public_metadata": {"role": "client"},
            # username posielame vždy – ak ho Clerk nepoužíva, ignoruje ho
            "username": uname,
        }
        if password:
            body["password"] = password

        try:
            return clerk_api("/v1/users", method="POST", json_body=body)
        except Exception as e:
            last_err = e
            # Clerk pri 422 typicky vráti informáciu o username v meta.param_names
            # – vtedy skúsime ďalší variant
            if "username" in str(e) and i < attempts - 1:
                continue
            # iná chyba alebo vyčerpané pokusy
            raise

    if last_err:
        raise last_err
    frappe.throw("Failed to create Clerk user", frappe.ValidationError)


def _patch_clerk_user(clerk_id: str, email: str | None, password: str | None, new_username: str | None = None) -> None:
    """
    PATCH na existujúceho Clerka. Username meníme len ak je zadaný.
    """
    patch: dict = {"public_metadata": {"role": "client"}}
    if email:
        patch["email_address"] = [email]
    if password:
        patch["password"] = password
    if new_username:
        patch["username"] = _normalize_username_base(new_username)

    try:
        clerk_api(f"/v1/users/{clerk_id}", method="PATCH", json_body=patch)
    except Exception as e:
        # Ak narazíme na 422 pre username, zalogujeme a nepokazíme update
        if new_username and "username" in str(e):
            frappe.log_error(f"Clerk username update failed: {e}", "BC Clerk Sync")
        else:
            raise


# -----------------------------------------------------------------------------
# HOOKY – DocType „BC Pouzivatel“
# -----------------------------------------------------------------------------

def after_insert_bc_pouzivatel(doc, method=None):
    """
    Po vytvorení záznamu vo Frappe:
      - ak nie je clerk_id a máme email → vytvoríme užívateľa v Clerku
      - uložíme clerk_id a (ak je k dispozícii) username z Clerka
    """
    if getattr(doc, "clerk_id", None):
        return
    if not getattr(doc, "email", None):
        return

    try:
        pw = None
        try:
            pw = get_decrypted_password("BC Pouzivatel", doc.name, "heslo")
        except Exception:
            pass

        res = _create_clerk_user(
            email=doc.email,
            password=pw,
            preferred_username=getattr(doc, "username", None),
        )

        cid = res.get("id")
        if cid:
            frappe.db.set_value("BC Pouzivatel", doc.name, "clerk_id", cid)

        # ak Clerk vrátil username, uložíme ho do poľa (ak ho máš vytvorené)
        if res.get("username") and hasattr(doc, "username"):
            frappe.db.set_value("BC Pouzivatel", doc.name, "username", res["username"])

    except Exception as e:
        # Nezastavuj ukladanie – len zaloguj chybu
        frappe.log_error(f"Clerk create failed: {e}", "BC Clerk Sync")


def on_update_bc_pouzivatel(doc, method=None):
    """
    Pri každom uložení:
      - ak máme clerk_id, zosynchronizujeme email/heslo/username do Clerka
      - ak clerk_id nie je, nič nerobíme
    """
    if not getattr(doc, "clerk_id", None):
        return

    # Ak admin upravil heslo/email/username, prepošleme do Clerka
    try:
        pw = None
        try:
            pw = get_decrypted_password("BC Pouzivatel", doc.name, "heslo")
        except Exception:
            pass

        _patch_clerk_user(
            clerk_id=doc.clerk_id,
            email=getattr(doc, "email", None),
            password=pw,
            new_username=getattr(doc, "username", None),
        )
    except Exception as e:
        frappe.log_error(f"Clerk update failed: {e}", "BC Clerk Sync")
