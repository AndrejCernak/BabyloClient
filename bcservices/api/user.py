# apps/bcservices/bcservices/api/user.py

import frappe
from .utils import (
    verify_clerk_bearer_and_get_sub,
    ensure_bc_user_by_clerk
)

@frappe.whitelist(methods=["GET"]), allow_guest=True)
def balance(userId: str = None):
    """
    Return total remaining minutes for a user.
    iOS volá: /api/method/bcservices.api.user.balance?userId=<clerk_id>

    Overí:
    - Clerk JWT
    - či si vypýtal balans pre seba (security)
    """

    # Over Clerk JWT
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    if not userId:
        frappe.throw("Missing userId", frappe.ValidationError)

    # Security – user môže zobraziť len svoj balans
    if userId != clerk_id:
        frappe.throw("Forbidden", frappe.PermissionError)

    # Najdi BC Pouzivatel záznam
    user_doc = ensure_bc_user_by_clerk(clerk_id)

    # Nájdeme všetky tokeny patriace userovi
    tokens = frappe.get_all(
        "BC Token",
        filters={
            "aktualny_drzitel": user_doc.name,
            "stav": "active"
        },
        fields=["minuty_ostavajuce"]
    )

    total = sum((t["minuty_ostavajuce"] or 0) for t in tokens)

    return {
        "success": True,
        "minutes": total,
        "tokenCount": len(tokens)
    }
