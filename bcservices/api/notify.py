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

    if not target_email:
        return {"success": False, "error": "Missing target_email"}

    # -------------------------------------------------------------------------
    # 🔥 OPRAVA: Zistíme reálne meno odosielateľa z databázy
    # -------------------------------------------------------------------------
    real_sender_name = raw_sender_name # Default hodnota

    if sender_email:
        try:
            # Použijeme tú istú funkciu na hľadanie odosielateľa v DB
            _, sender_doc = get_actor_by_email(sender_email)

            if sender_doc:
                # Skúsime nájsť najlepšie dostupné meno v poradí: username -> full_name -> name
                real_sender_name = (
                    sender_doc.get("username") or
                    sender_doc.get("full_name") or
                    sender_doc.get("name") or
                    raw_sender_name
                )
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

    for d in devices:
        # Hľadáme 'apns_token' (nie voip_token!)
        if d.apns_token:
            success = send_chat_push(
                device_token=d.apns_token,
                title=real_sender_name,  # 🔥 TU použijeme pekné meno z databázy
                body=content,            # Text správy
                custom_data={
                    "email_from": sender_email, # Aby iOS vedel otvoriť chat (používame email)
                    "type": "chat"
                },
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
