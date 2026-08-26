import json
import frappe
from .utils import get_actor_by_email, send_chat_push


def _load_unread_map(user_doc) -> dict:
    """Neprečítané správy rozpísané per odosielateľ (email -> počet). Uložené ako JSON."""
    raw = user_doc.get("unread_by_sender")
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _store_unread_map(doctype, name, unread_map: dict) -> int:
    """Uloží mapu + zosynchronizuje celkový počet (badge). Vráti celkový počet."""
    total = sum(int(v) for v in unread_map.values())
    frappe.db.set_value(doctype, name, {
        "unread_by_sender": json.dumps(unread_map),
        "unread_push_count": total,
    })
    frappe.db.commit()
    return total

@frappe.whitelist(methods=["POST"], allow_guest=True)
def send_notification():
    """
    Tento endpoint bude volať Node.js server, keď je používateľ offline.
    """
    # 1. Získame dáta z requestu
    data = frappe.local.form_dict
    target_email = data.get("to_user")    # Komu (email)
    sender_email = data.get("from_user")  # 🔥 Od koho (email) - pre vyhľadanie mena

    # Pôvodné meno z Node.js (často len ID alebo 'Niekto')
    raw_sender_name = data.get("from_name", "Neznámy")

    content = data.get("content", "Máte novú správu")

    # Skupinová správa: v notifikácii je názov skupiny a v tele „Meno: text",
    # aby bolo na prvý pohľad jasné, kam správa patrí.
    group_id = data.get("group_id")
    group_name = data.get("group_name")

    if not target_email:
        return {"success": False, "error": "Missing target_email"}

    # -------------------------------------------------------------------------
    # 🔥 OPRAVA: Zistíme reálne meno odosielateľa z databázy
    # -------------------------------------------------------------------------
    real_sender_name = raw_sender_name # Default hodnota

    if sender_email:
        try:
            # Použijeme tú istú funkciu na hľadanie odosielateľa v DB
            sender_doctype, sender_doc = get_actor_by_email(sender_email)

            if sender_doc:
                # POZOR: Poradca ma meno v poli "meno", Klient v "username".
                # Bez tohto rozlisenia padal fallback az na docname (napr. "s1r5svmgbk")
                # a to sa zobrazovalo ako nadpis push notifikacie.
                if sender_doctype == "Poradca":
                    real_sender_name = sender_doc.get("meno") or raw_sender_name
                else:
                    real_sender_name = sender_doc.get("username") or raw_sender_name
        except Exception:
            # Ak nastane chyba pri hľadaní, nevadí, použijeme pôvodné raw meno
            pass

    # -------------------------------------------------------------------------

    # 2. Nájdeme PRÍJEMCU v DB (Poradca alebo Klient)
    doctype, user_doc = get_actor_by_email(target_email)

    if not user_doc:
        return {"success": False, "error": "User not found"}

    # 3. Zvýšime počítadlo neprečítaných PER ODOSIELATEĽ. Badge = súčet všetkých.
    #    Appka pri otvorení konkrétneho chatu zavolá mark_chat_read(from_user),
    #    čím sa odpočíta len tá jedna konverzácia (badge klesne presne o toľko).
    unread_map = _load_unread_map(user_doc)
    key = sender_email or "unknown"
    unread_map[key] = int(unread_map.get(key, 0)) + 1
    new_badge = _store_unread_map(doctype, user_doc.name, unread_map)

    # 4. Získame jeho APNs tokeny zo child table 'Zariadenie'
    devices = user_doc.get("zariadenie") or []
    sent_count = 0

    push_title = group_name or real_sender_name
    push_body = f"{real_sender_name}: {content}" if group_id else content
    custom_data = {
        "email_from": sender_email,  # Aby iOS vedel otvoriť chat (používame email)
        "type": "chat",
    }
    if group_id:
        custom_data["group_id"] = group_id
        custom_data["type"] = "group_chat"

    for d in devices:
        # Hľadáme 'apns_token' (nie voip_token!)
        if d.apns_token:
            success = send_chat_push(
                device_token=d.apns_token,
                title=push_title,
                body=push_body,
                custom_data=custom_data,
                badge=new_badge          # Počet neprečítaných → ikona appky
            )
            if success:
                sent_count += 1

    return {"success": True, "sent_to": sent_count}


@frappe.whitelist(methods=["POST"], allow_guest=True)
def mark_chat_read():
    """
    Appka volá keď používateľ OTVORÍ konkrétny chat — vynuluje neprečítané len
    pre daného odosielateľa (from_user). Vráti nový celkový badge, ktorý si appka
    nastaví na ikonu. Autentifikované cez náš JWT (email).
    """
    from .utils import verify_bearer_and_get_email

    email, _ = verify_bearer_and_get_email()
    if not email:
        frappe.throw("Invalid token", frappe.PermissionError)

    from_user = frappe.local.form_dict.get("from_user")
    if not from_user:
        return {"success": False, "error": "Missing from_user"}

    doctype, user_doc = get_actor_by_email(email)
    if not user_doc:
        return {"success": False, "error": "User not found"}

    unread_map = _load_unread_map(user_doc)
    unread_map.pop(from_user, None)
    total = _store_unread_map(doctype, user_doc.name, unread_map)

    return {"success": True, "badge": total}


@frappe.whitelist(methods=["POST"], allow_guest=True)
def reset_badge():
    """
    Vynuluje VŠETKY neprečítané (fallback / úplný reset). Autentifikované cez JWT.
    """
    from .utils import verify_bearer_and_get_email

    email, _ = verify_bearer_and_get_email()
    if not email:
        frappe.throw("Invalid token", frappe.PermissionError)

    doctype, user_doc = get_actor_by_email(email)
    if not user_doc:
        return {"success": False, "error": "User not found"}

    _store_unread_map(doctype, user_doc.name, {})

    return {"success": True}
