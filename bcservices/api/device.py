# apps/bcservices/bcservices/api/device.py
import frappe
from .utils import verify_clerk_bearer_and_get_sub, ensure_bc_user_by_clerk, upsert_child_device_for_user

@frappe.whitelist(methods=["POST"])
def register_device(userId: str = None, voipToken: str = None, apnsToken: str = None):
    """Body: { userId, voipToken [, apnsToken] } – userId je Clerk ID (ako v tvojom Node)."""
    data = frappe.local.form_dict
    userId = userId or data.get("userId")
    voipToken = voipToken or data.get("voipToken")
    apnsToken = apnsToken or data.get("apnsToken")

    if not userId or not voipToken:
        frappe.throw("Missing userId or voipToken", frappe.ValidationError)

    user = ensure_bc_user_by_clerk(userId)
    upsert_child_device_for_user(user, voip_token=voipToken, apns_token=apnsToken)

    # vráť posledný záznam device (ak chceš)
    devices = frappe.get_all("BC Zariadenie", filters={"parent": user.name},
                             fields=["voip_token","apns_token","modified"], order_by="modified desc")
    return {"ok": True, "device": devices[0] if devices else None}
