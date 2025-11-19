# apps/bcservices/bcservices/api/admin.py

import frappe
from frappe.utils import now_datetime
from .utils import verify_clerk_bearer_and_get_sub, clerk_api, ensure_settings, get_conf_int

# -----------------------------------------------------------------------------
# INTERNAL – CHECK ADMIN ROLE
# -----------------------------------------------------------------------------

def _require_admin():
    clerk_id, _ = verify_clerk_bearer_and_get_sub()
    u = clerk_api(f"/v1/users/{clerk_id}")
    role = (u.get("public_metadata") or {}).get("role")
    if role != "admin":
        frappe.throw("Forbidden", frappe.PermissionError)
    return clerk_id

# -----------------------------------------------------------------------------
# ADMIN – LIST ALL CLIENTS
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["GET"], allow_guest=True)
def list_clients():
    """
    iOS Admin app → /api/method/bcservices.api.admin.list_clients
    Overí Clerk JWT (musí mať role='admin'), a vráti zoznam klientov
    + ich zariadenia + ich tokeny.
    """
    _require_admin()

    users = frappe.get_all(
        "BC Pouzivatel",
        fields=["name", "clerk_id", "email"]
    )

    out = []
    for u in users:
        # devices
        devices = frappe.get_all(
            "BC Zariadenie",
            filters={"parent": u["name"]},
            fields=["voip_token", "apns_token", "modified"]
        )

        # tokens
        tokens = frappe.get_all(
            "BC Token",
            filters={"aktualny_drzitel": u["name"]},
            fields=["minuty_ostavajuce", "stav"]
        )

        # username from Clerk
        username = None
        try:
            cu = clerk_api(f"/v1/users/{u['clerk_id']}")
            username = (
                cu.get("username")
                or cu.get("first_name")
                or (cu.get("email_addresses")[0]["email_address"]
                    if cu.get("email_addresses") else None)
            )
        except Exception:
            pass

        out.append({
            **u,
            "devices": devices,
            "tokens": tokens,
            "username": username
        })

    return {
        "success": True,
        "clients": out
    }

# -----------------------------------------------------------------------------
# ADMIN – MINT TOKENS
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"], allow_guest=True)
def mint(quantity: int = None, priceEur: float = None, year: int = None):
    _require_admin()

    data = frappe.local.form_dict
    qty = int(quantity or data.get("quantity") or 0)
    price = float(priceEur or data.get("priceEur") or 0)
    y = int(year or data.get("year") or now_datetime().year)

    if qty <= 0 or price <= 0:
        frappe.throw("Invalid quantity/priceEur", frappe.ValidationError)

    settings = ensure_settings()

    for _ in range(qty):
        d = frappe.get_doc({
            "doctype": "BC Token",
            "minuty_ostavajuce": 60,
            "stav": "active",
            "povodna_cena_eur": price,
            "vydany_rok": y
        })
        d.insert(ignore_permissions=True)

    settings.aktualna_cena_eur = price
    settings.save(ignore_permissions=True)

    return {
        "success": True,
        "minted": qty,
        "priceEur": price,
        "year": y
    }

# -----------------------------------------------------------------------------
# ADMIN – CHANGE TOKEN PRICE
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"], allow_guest=True)
def set_price(newPrice: float = None, repriceTreasury: int = 0):
    _require_admin()

    data = frappe.local.form_dict
    price = float(newPrice or data.get("newPrice") or 0)
    reprice = int(repriceTreasury or data.get("repriceTreasury") or 0)

    if price <= 0:
        frappe.throw("Invalid newPrice", frappe.ValidationError)

    settings = ensure_settings()
    settings.aktualna_cena_eur = price
    settings.save(ignore_permissions=True)

    if reprice:
        # tokens without holder = treasury
        treasury = frappe.get_all(
            "BC Token",
            filters={"aktualny_drzitel": ["is", "null"], "stav": "active"},
            pluck="name"
        )
        for n in treasury:
            frappe.db.set_value("BC Token", n, "povodna_cena_eur", price)

    return {"success": True, "priceEur": price}
