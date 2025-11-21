# apps/bcservices/bcservices/api/device.py

import frappe
from .utils import (
    verify_clerk_bearer_and_get_sub,
    ensure_bc_user_by_clerk,
    upsert_child_device_for_user
)

@frappe.whitelist(methods=["POST"], allow_guest=True)
def register_device():
    """
    Register iOS VoIP token for push notifications.
    TOTO JE SPRÁVNA VERZIA PRE TVOJU APLIKÁCIU.
    - Overí Clerk JWT
    - Nájde BC Pouzivatel podľa clerk_id
    - Uloží alebo aktualizuje záznam v child table 'BC Zariadenie'
    """
    
    clerk_id, payload = verify_clerk_bearer_and_get_sub()

    data = frappe.local.form_dict or {}

    # iOS môže poslať voip_token alebo voipToken
    voip_token = data.get("voip_token") or data.get("voipToken")

    if not voip_token:
        frappe.throw("Missing voip_token")

    # nájdi alebo vytvor BC Pouzivatel podľa Clerk ID
    user_doc = ensure_bc_user_by_clerk(clerk_id)

    # upsert zariadenia – zabezpečí:
    # - update tokenu ak existuje
    # - nový child record ak neexistuje
    # - odstránenie duplicít v ostatných useroch
    upsert_child_device_for_user(
        user_doc=user_doc,
        voip_token=voip_token
    )

    return {"success": True, "voip_token": voip_token}
