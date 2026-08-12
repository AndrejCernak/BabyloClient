import frappe
from frappe.utils import now_datetime
from .utils import verify_bearer_and_get_email
from bcservices.api.me import _require_authenticated_user_and_get_email


# -----------------------------------------------------------------------------
# INTERNAL – CHECK ADMIN ROLE (admin = Poradca)
# -----------------------------------------------------------------------------
def _require_admin():
    email, _ = verify_bearer_and_get_email()
    poradca = frappe.db.get_value("Poradca", {"email": email}, "name")
    if not poradca:
        frappe.throw("Forbidden", frappe.PermissionError)
    return email


# -----------------------------------------------------------------------------
# ADMIN – LIST ALL CLIENTS
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"], allow_guest=True)
def list_clients():
    email = _require_authenticated_user_and_get_email()

    # Nájdi Poradca podľa emailu
    poradca = frappe.get_all(
        "Poradca",
        filters={"email": email},
        fields=["name"],
        limit_page_length=1
    )
    if not poradca:
        return {"success": True, "clients": []}
    
    poradca_name = poradca[0]["name"]
    
    # Nájdi klientov kde je tento poradca priradený
    linked = frappe.get_all(
        "Poradca Klienta",
        filters={"uzivatel_link": poradca_name, "typ_uzivatela": "Poradca", "parenttype": "Klient"},
        fields=["parent"]
    )
    
    klient_names = [r["parent"] for r in linked]
    if not klient_names:
        return {"success": True, "clients": []}
    
    users = frappe.get_all(
        "Klient",
        filters={"name": ["in", klient_names]},
        fields=["name", "email"]
    )
    
    out = []
    for u in users:
        devices = frappe.get_all(
            "Zariadenie",
            filters={"parent": u["name"]},
            fields=["voip_token", "apns_token", "modified"]
        )
        tokens = frappe.get_all(
            "Token",
            filters={"aktualny_drzitel": u["name"]},
            fields=["minuty_ostavajuce", "stav"]
        )
        username = frappe.db.get_value("Klient", u["name"], "username") or u.get("email")
        out.append({
            **u,
            "devices": devices,
            "tokens": tokens,
            "username": username
        })
    
    return {"success": True, "clients": out}


# -----------------------------------------------------------------------------
# ADMIN – MINT TOKENS
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def admin_mint():
    _require_admin()

    data = frappe.request.get_json() or {}

    qty = int(data.get("quantity") or 0)
    price = float(data.get("priceEur") or 0)
    year = int(data.get("year") or now_datetime().year)

    if qty <= 0 or price <= 0:
        frappe.throw("Invalid quantity or priceEur", frappe.ValidationError)

    created = []

    for _ in range(qty):
        doc = frappe.get_doc({
            "doctype": "Token",
            "minuty_ostavajuce": 60,
            "stav": "active",
            "povodna_cena_eur": price,
            "vydany_rok": year,
            "aktualny_drzitel": None,
        })
        doc.insert(ignore_permissions=True)
        created.append(doc.name)

    return {
        "success": True,
        "minted": qty,
        "token_names": created,
        "priceEur": price,
        "year": year
    }


# -----------------------------------------------------------------------------
# ADMIN – CHANGE TOKEN PRICE
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def admin_set_price():
    _require_admin()

    data = frappe.request.get_json() or {}

    new_price = float(data.get("newPrice") or 0)
    reprice = bool(data.get("repriceTreasury") or False)

    if new_price <= 0:
        frappe.throw("Invalid newPrice", frappe.ValidationError)

    updated = []

    if reprice:
        treasury_tokens = frappe.get_all(
            "Token",
            filters={
                "stav": "active",
                "aktualny_drzitel": ["is", "null"],
            },
            pluck="name"
        )
        for token_name in treasury_tokens:
            frappe.db.set_value(
                "Token", token_name,
                "povodna_cena_eur", new_price
            )
        updated = treasury_tokens

    return {
        "success": True,
        "priceEur": new_price,
        "updatedCount": len(updated),
        "updatedTokens": updated
    }
