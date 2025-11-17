# apps/bcservices/bcservices/api/user.py
import frappe
from .utils import ensure_bc_user_by_clerk

@frappe.whitelist(methods=["GET"])
def balance(userId: str):
    user = ensure_bc_user_by_clerk(userId)
    tokens = frappe.get_all("BC Token",
        filters={"aktualny_drzitel": user.name},
        fields=["name","vydany_rok","minuty_ostavajuce","stav"],
        order_by="vydany_rok asc, creation asc")

    total = sum([t["minuty_ostavajuce"] for t in tokens if t["stav"]=="active"])
    return {"userId": userId, "totalMinutes": total, "tokens": tokens}
