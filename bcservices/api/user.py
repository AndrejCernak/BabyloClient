# apps/bcservices/bcservices/api/user.py
import frappe
from .utils import ensure_bc_user_by_clerk

@frappe.whitelist(methods=["GET"], allow_guest=True)
def balance(userId: str):
    """Return remaining minutes for a user."""
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    if clerk_id != userId:
        frappe.throw("Forbidden", frappe.PermissionError)

    tokens = frappe.get_all(
        "BC Token",
        filters={"client": userId, "stav": "active"},
        fields=["minuty_ostavajuce"],
    )

    total = sum(t.minuty_ostavajuce for t in tokens if t.minuty_ostavajuce)

    return {"success": True, "minutes": total}
