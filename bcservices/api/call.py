# apps/bcservices/bcservices/api/call.py
import frappe
from frappe.utils import now_datetime
from .utils import verify_clerk_bearer_and_get_sub, ensure_bc_user_by_clerk, send_voip_push

@frappe.whitelist(methods=["POST"])
def start(callerId: str=None, callerName: str=None, advisorId: str=None):
    data = frappe.local.form_dict
    callerId = callerId or data.get("callerId")
    advisorId = advisorId or data.get("advisorId")
    callerName = callerName or data.get("callerName")

    if not callerId or not advisorId:
        frappe.throw("Missing callerId/advisorId", frappe.ValidationError)

    caller = ensure_bc_user_by_clerk(callerId)
    advisor = ensure_bc_user_by_clerk(advisorId)

    call = frappe.get_doc({
        "doctype": "BC Dennik hovorov",
        "volajuci": caller.name,
        "poradca": advisor.name,
        "zaciatok": now_datetime()
    })
    call.insert(ignore_permissions=True)

    # nájdi poradcu device (zober posledné)
    device = frappe.get_all("BC Zariadenie", filters={"parent": advisor.name},
                            fields=["voip_token"], order_by="modified desc", limit_page_length=1)
    if not device or not device[0].get("voip_token"):
        frappe.throw("Advisor not registered for VoIP", frappe.ValidationError)

    payload = {
        "type": "incoming_call",
        "callId": call.name,
        "callerId": callerId,
        "callerName": callerName or "Unknown"
    }
    try:
        send_voip_push(device[0]["voip_token"], payload)
    except Exception as e:
        frappe.log_error(f"VoIP push error: {e}", "BC VoIP")

    return {"success": True, "callId": call.name}

@frappe.whitelist(methods=["POST"])
def end(callId: str=None, trvanie_s: int=None, pouzity_token: str=None):
    data = frappe.local.form_dict
    callId = callId or data.get("callId")
    doc = frappe.get_doc("BC Dennik hovorov", callId)
    doc.koniec = now_datetime()
    if trvanie_s:
        doc.trvanie_s = int(trvanie_s)
    if pouzity_token:
        doc.pouzity_token = pouzity_token
    doc.save(ignore_permissions=True)
    return {"success": True, "call": {"name": doc.name, "trvanie_s": doc.trvanie_s}}

@frappe.whitelist(methods=["GET"])
def history(userId: str):
    """GET /calls/:userId → userId je Clerk ID"""
    user = ensure_bc_user_by_clerk(userId)
    calls = frappe.get_all("BC Dennik hovorov",
        filters={"or": [["volajuci","=",user.name], ["poradca","=",user.name]]},
        order_by="zaciatok desc",
        fields=["name","volajuci","poradca","zaciatok","koniec","trvanie_s","pouzity_token"])
    return {"success": True, "calls": calls}
