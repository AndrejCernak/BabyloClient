import math
import traceback
import frappe
from frappe.utils import now_datetime, getdate, get_time
from datetime import datetime, timedelta
from frappe import _

# Importujeme pomocné funkcie z utils
from .utils import (
    verify_bearer_and_get_email,
    send_voip_push,
)

# Importujeme Google Calendar logiku (z nového súboru google_calendar.py)
try:
    from .google_calendar import create_call_event, update_call_event_end
except ImportError:
    # Fallback ak súbor neexistuje, aby nezhavaroval celý server, len logne chybu
    frappe.log_error("Chýba súbor google_calendar.py", "BC Import Error")
    create_call_event = None
    update_call_event_end = None

# ----------------------------------------------------------------------
# Kontrola tokenov v piatok
# ----------------------------------------------------------------------
def check_friday_tokens(klient_name):
    """
    Kontroluje, či je piatok a či má klient dostatok tokenov.
    Returns: (bool, str) - (can_call, error_message)
    """
    today = now_datetime()  # ✅ rešpektuje system_settings timezone
    if today.weekday() != 4:
        return True, None

    try:
        tokens = frappe.get_all(
            "Token",
            filters={
                "aktualny_drzitel": klient_name,
                "stav": "active"
            },
            fields=["name", "minuty_ostavajuce"]
        )

        total_minutes = sum(t.get("minuty_ostavajuce", 0) for t in tokens)
        frappe.logger().info(f"🔍 Friday token check for {klient_name}: {total_minutes} min")

        if total_minutes <= 0:
            return False, "V piatok potrebujete aspoň 1 token na volanie."

        return True, None

    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Friday Token Check")
        return False, "Chyba pri overení tokenov."  # 🔥 fail-CLOSED — bezpečnejšie

def consume_tokens_after_call(klient_name, seconds_used, call_doc):
    """Odpočíta minúty z tokenov FIFO (najstarší token najprv). Round up na celé minúty."""
    minutes_used = math.ceil(seconds_used / 60)
    if minutes_used <= 0:
        return

    tokens = frappe.get_all(
        "Token",
        filters={
            "aktualny_drzitel": klient_name,
            "stav": "active"
        },
        fields=["name", "minuty_ostavajuce"],
        order_by="creation asc"  # FIFO — najstarší token najprv
    )

    remaining = minutes_used
    first_consumed_token = None

    for t in tokens:
        if remaining <= 0:
            break

        token_doc = frappe.get_doc("Token", t.name)
        if (token_doc.minuty_ostavajuce or 0) <= 0:
            continue

        if not first_consumed_token:
            first_consumed_token = token_doc.name

        if token_doc.minuty_ostavajuce >= remaining:
            token_doc.minuty_ostavajuce -= remaining
            remaining = 0
            if token_doc.minuty_ostavajuce == 0:
                token_doc.stav = "spent"
        else:
            remaining -= token_doc.minuty_ostavajuce
            token_doc.minuty_ostavajuce = 0
            token_doc.stav = "spent"

        token_doc.save(ignore_permissions=True)

    # Link prvý použitý token do Dennik hovorov
    if first_consumed_token:
        call_doc.db_set("pouzity_token", first_consumed_token)

    frappe.db.commit()
    frappe.logger().info(
        f"💰 Consumed {minutes_used}min from {klient_name}'s tokens "
        f"(remaining unfulfilled: {remaining}min)"
    )

# ----------------------------------------------------------------------
# START CALL (Obojstranný) - ✅ S KONTROLOU TOKENOV
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def start():
    debug_id = frappe.generate_hash(length=4)
    log_tag = f"BC DEBUG [{debug_id}]"
    data = frappe.local.form_dict or {}

    c1_id = data.get("callerId")
    c2_id = data.get("advisorId")

    try:
        auth_email, _ = verify_bearer_and_get_email()

        p1_klient = frappe.db.get_value("Klient", {"email": c1_id}, "name")
        p1_poradca = frappe.db.get_value("Poradca", {"email": c1_id}, "name")

        p2_klient = frappe.db.get_value("Klient", {"email": c2_id}, "name")
        p2_poradca = frappe.db.get_value("Poradca", {"email": c2_id}, "name")

        real_klient = p1_klient or p2_klient
        real_poradca = p1_poradca or p2_poradca

        is_poradca_to_poradca = bool(p1_poradca and p2_poradca)

        if not is_poradca_to_poradca and (not real_klient or not real_poradca):
            return {"success": False, "error": "Participants not found"}

        # Piatok token check — len pre hovory kde je klient
        if real_klient:
            can_call, error_msg = check_friday_tokens(real_klient)
            if not can_call:
                return {
                    "success": False,
                    "error": "insufficient_tokens_friday",
                    "message": error_msg or "V piatok potrebujete tokeny na volanie.",
                    "errorCode": "FRIDAY_NO_TOKENS"
                }

        kto_volal = "Poradca" if p1_poradca else "Klient"

        if auth_email == c1_id:
            target_doctype = "Poradca" if p2_poradca else "Klient"
            target_id = p2_poradca or p2_klient
            caller_poradca, caller_klient = p1_poradca, p1_klient
        else:
            target_doctype = "Poradca" if p1_poradca else "Klient"
            target_id = p1_poradca or p1_klient
            caller_poradca, caller_klient = p2_poradca, p2_klient

        # Skutočné meno volajúceho (nie docname, ktorý môže byť hash)
        if caller_poradca:
            display_name = frappe.db.get_value("Poradca", caller_poradca, "meno") or caller_poradca
        else:
            display_name = frappe.db.get_value("Klient", caller_klient, "username") or caller_klient

        now = now_datetime()

        if is_poradca_to_poradca:
            call_doc = frappe.get_doc({
                "doctype": "Dennik hovorov",
                "poradca": p1_poradca,
                "poradca2": p2_poradca,
                "kto_volal": "Poradca",
                "zaciatok_datum": now.date(),
                "zaciatok_cas": now.strftime("%H:%M:%S"),
            })
        else:
            call_doc = frappe.get_doc({
                "doctype": "Dennik hovorov",
                "klient": real_klient,
                "poradca": real_poradca,
                "kto_volal": kto_volal,
                "zaciatok_datum": now.date(),
                "zaciatok_cas": now.strftime("%H:%M:%S"),
            })

        call_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        if create_call_event:
            try:
                display_title = real_klient if real_klient else f"{p1_poradca} ↔ {p2_poradca}"
                event_id = create_call_event(call_doc, display_title=display_title)
                if event_id:
                    call_doc.db_set("google_event_id", event_id)
            except Exception as e:
                frappe.log_error(f"Failed to create Google Event: {e}", log_tag)

        target_doc = frappe.get_doc(target_doctype, target_id)
        devices = target_doc.get("zariadenie") or []
        sent_count = 0
        for d in devices:
            token = getattr(d, "voip_token", None) or getattr(d, "voipToken", None)
            if token:
                payload = {
                    "callId": call_doc.name,
                    "callerId": auth_email,
                    "callerName": display_name,
                    "title": "Prichádzajúci hovor"
                }
                if send_voip_push(token, payload):
                    sent_count += 1

        frappe.logger().info(f"✅ Call started: {call_doc.name}, sent_to: {sent_count}")
        return {"success": True, "callId": call_doc.name, "sent_to": sent_count}

    except Exception:
        frappe.log_error(traceback.format_exc(), log_tag)
        return {"success": False, "error": "Internal server error"}
# ----------------------------------------------------------------------
# ACCEPT CALL
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def accept():
    try:
        email, _ = verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        call_id = data.get("callId")

        if not call_id or call_id == "PENDING":
            return {"success": True, "note": "Call not in DB"}

        doc = frappe.get_doc("Dennik hovorov", call_id)

        # OVERENIE: Je ten, kto klikol "Prijať", jeden z účastníkov hovoru?
        is_valid = False
        klient_name = frappe.db.get_value("Klient", {"email": email}, "name")
        advisor_name = frappe.db.get_value("Poradca", {"email": email}, "name")

        # Oprava: Kontrolujeme polia 'klient' a 'poradca', nie 'volajuci'
        if (klient_name and doc.klient == klient_name) or (advisor_name and doc.poradca == advisor_name):
            is_valid = True

        if not is_valid:
            frappe.throw(_("Unauthorized to accept this call"), frappe.PermissionError)

        doc.prijaty = 1
        doc.prijaty_cas = now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {"success": True, "callId": call_id}
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "BC Accept Error")
        return {"success": False, "error": str(e)}

# ----------------------------------------------------------------------
# END CALL
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def end():
    try:
        verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        call_id = data.get("callId")

        if not call_id or call_id == "PENDING":
            return {"success": True}

        # Načítame dokument
        doc = frappe.get_doc("Dennik hovorov", call_id)
        now = now_datetime()

        # Nastavíme koniec
        doc.koniec_datum = now.date()
        doc.koniec_cas = now.strftime("%H:%M:%S")

        # Vypočítame trvanie
        duration = 0
        try:
            start_dt = datetime.combine(getdate(doc.zaciatok_datum), get_time(doc.zaciatok_cas))
            duration = max(0, int((now - start_dt).total_seconds()))
            doc.trvanie_s = duration
        except:
            doc.trvanie_s = 0

        # Uložíme do DB
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        # 🔥 Čerpanie tokenov ak bol piatok (podľa zaciatok_datum)
        try:
            if doc.klient and duration > 0:
                start_dt = datetime.combine(getdate(doc.zaciatok_datum), get_time(doc.zaciatok_cas))
                was_friday = start_dt.weekday() == 4
                if was_friday:
                    consume_tokens_after_call(doc.klient, duration, doc)
        except Exception:
            frappe.log_error(traceback.format_exc(), "BC Token Consume Error")

        # --- GOOGLE CALENDAR UPDATE ---
        if update_call_event_end:
            try:
                doc.reload()
                update_call_event_end(doc, display_title=doc.klient)
            except Exception as e:
                frappe.log_error(f"Failed to update Google Event: {e}", "BC End Error")

        return {"success": True}

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "BC End Error")
        return {"success": False}
