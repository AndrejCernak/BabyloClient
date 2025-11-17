# apps/bcservices/bcservices/api/misc.py
import frappe

@frappe.whitelist(methods=["POST"], allow_guest=True)
def debug_log(msg: str=None, time: str=None, userId: str=None):
    data = frappe.local.form_dict
    msg = msg or data.get("msg")
    time = time or data.get("time")
    userId = userId or data.get("userId")
    frappe.logger().info(f"iOS DEBUG: {time} {userId or '-'} {msg}")
    return {"ok": True}
