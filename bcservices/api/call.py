# apps/bcservices/bcservices/api/call.py
import frappe
from frappe.utils import now_datetime
from .utils import verify_clerk_bearer_and_get_sub, ensure_bc_user_by_clerk, send_voip_push

@frappe.whitelist(methods=["POST"], allow_guest=True)
def start():
    """Start outgoing call."""
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    data = frappe.local.form_dict or {}
    caller = data.get("callerId")
    advisor = data.get("advisorId")

    if not caller or not advisor:
        frappe.throw("Missing callerId or advisorId")

    call = frappe.new_doc("BC Call")
    call.caller = caller
    call.advisor = advisor
    call.status = "ringing"
    call.save(ignore_permissions=True)

    return {"success": True, "callId": call.name}


@frappe.whitelist(methods=["POST"], allow_guest=True)
def end():
    """End call."""
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    data = frappe.local.form_dict or {}
    call_id = data.get("callId")
    if not call_id:
        frappe.throw("Missing callId")

    doc = frappe.get_doc("BC Call", call_id)
    doc.status = "ended"
    doc.save(ignore_permissions=True)

    return {"success": True}


@frappe.whitelist(methods=["GET"], allow_guest=True)
def history(userId: str):
    """Return call history for a specific user."""
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    if clerk_id != userId:
        frappe.throw("Forbidden", frappe.PermissionError)

    calls = frappe.get_all(
        "BC Call",
        filters={"caller": userId},
        fields=["name", "advisor", "status", "start_time", "end_time"],
        order_by="start_time desc",
    )

    return {"success": True, "calls": calls}

