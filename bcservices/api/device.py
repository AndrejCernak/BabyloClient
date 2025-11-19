# apps/bcservices/bcservices/api/device.py
import frappe
from .utils import verify_clerk_bearer_and_get_sub, ensure_bc_user_by_clerk, upsert_child_device_for_user

@frappe.whitelist(methods=["POST"], allow_guest=True)
def register_device():
    """Register user VoIP token (iOS)."""
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    data = frappe.local.form_dict or {}
    voip_token = data.get("voip_token") or data.get("voipToken")

    if not voip_token:
        frappe.throw("Missing voip_token")

    existing = frappe.db.get_value("BC Device", {"clerk_id": clerk_id})

    if existing:
        doc = frappe.get_doc("BC Device", existing)
    else:
        doc = frappe.new_doc("BC Device")
        doc.clerk_id = clerk_id

    doc.voip_token = voip_token
    doc.save(ignore_permissions=True)

    return {"success": True}
