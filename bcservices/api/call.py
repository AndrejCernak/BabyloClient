# apps/bcservices/bcservices/api/call.py

import frappe
from frappe.utils import now_datetime
from .utils import (
    verify_clerk_bearer_and_get_sub,
    ensure_bc_user_by_clerk,
    send_voip_push
)

# -----------------------------------------------------------------------------
# START CALL
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def start():
    """
    ADMIN → CLIENT
    Admin spustí hovor → backend vytvorí BC Call → pošle VoIP push klientovi.
    """
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    data = frappe.local.form_dict or {}
    caller = data.get("callerId")        # admin (volajúci)
    advisor = data.get("advisorId")      # klient (prijímateľ)

    if not caller or not advisor:
        frappe.throw("Missing callerId or advisorId")

    # Create call record
    call = frappe.new_doc("BC Call")
    call.caller = caller
    call.advisor = advisor
    call.status = "ringing"
    call.start_time = now_datetime()
    call.save(ignore_permissions=True)

    # Find device (BC Zariadenie) for the advisor
    devices = frappe.get_all(
        "BC Zariadenie",
        filters={"parent": ["in", frappe.get_all("BC Pouzivatel", filters={"clerk_id": advisor}, pluck="name")]},
        fields=["voip_token"]
    )

    # Send VoIP push (optional but needed for iOS)
    if devices:
        dev = devices[0]
        if dev.voip_token:
            send_voip_push(
                dev.voip_token,
                {
                    "callId": call.name,
                    "title": "Prichádzajúci hovor",
                    "body": "Volá poradca",
                }
            )

    return {"success": True, "callId": call.name}

# -----------------------------------------------------------------------------
# ACCEPT CALL (client accepts after VoIP push)
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def accept():
    """
    Klient prijme hovor po VoIP push.
    iOS → /api/method/bcservices.api.call.accept
    """
    clerk_id, payload = verify_clerk_bearer_and_get_sub()
    data = frappe.local.form_dict or {}
    call_id = data.get("callId")

    if not call_id:
        frappe.throw("Missing callId")

    doc = frappe.get_doc("BC Call", call_id)

    # Only advisor (client) can accept
    if doc.advisor != clerk_id:
        frappe.throw("You cannot accept someone else's call", frappe.PermissionError)

    doc.status = "ongoing"
    doc.answered_time = now_datetime()
    doc.save(ignore_permissions=True)

    return {"success": True}

# -----------------------------------------------------------------------------
# END CALL
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def end():
    """
    Ktorákoľvek strana ukončí hovor.
    """
    clerk_id, payload = verify_clerk_bearer_and_get_sub()
    data = frappe.local.form_dict or {}
    call_id = data.get("callId")

    if not call_id:
        frappe.throw("Missing callId")

    doc = frappe.get_doc("BC Call", call_id)

    doc.status = "ended"
    doc.end_time = now_datetime()
    doc.save(ignore_permissions=True)

    return {"success": True}

# -----------------------------------------------------------------------------
# CALL HISTORY
# -----------------------------------------------------------------------------
@frappe.whitelist(methods=["GET"], allow_guest=True)
def history(userId: str):
    """
    iOS: get call history for user.
    Security: user can view only their own history.
    """
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    if clerk_id != userId:
        frappe.throw("Forbidden", frappe.PermissionError)

    calls = frappe.get_all(
        "BC Call",
        filters={
            "caller": userId
        },
        fields=["name", "advisor", "status", "start_time", "end_time"],
        order_by="start_time desc",
    )

    return {"success": True, "calls": calls}
