# apps/bcservices/bcservices/api/group.py
"""Skupinove konverzacie.

Skupina vznikne pri konferencnom hovore a zostava v appke aj po nom.
Rovnaka zostava ludi = ta ista skupina (rozhoduje `kluc_clenov`).
"""

import hashlib
import json
import traceback

import frappe
from frappe.utils import now_datetime

from .utils import verify_bearer_and_get_email, get_actor_by_email


def _resolve_name(email):
    """Zobrazovane meno pouzivatela (Poradca ma 'meno', Klient 'username')."""
    poradca = frappe.db.get_value("Poradca", {"email": email}, ["name", "meno"], as_dict=True)
    if poradca:
        return poradca.get("meno") or poradca.get("name")
    klient = frappe.db.get_value("Klient", {"email": email}, ["name", "username"], as_dict=True)
    if klient:
        return klient.get("username") or klient.get("name")
    return None


def _member_key(emails):
    """Odtlacok zostavy clenov — nezavisly od poradia."""
    normalized = sorted({(e or "").strip().lower() for e in emails if e})
    return hashlib.sha1("|".join(normalized).encode("utf-8")).hexdigest()


def _group_payload(doc):
    return {
        "id": doc.name,
        "name": doc.nazov,
        "members": [
            {"email": r.email, "name": r.meno or r.email}
            for r in (doc.get("clenovia") or [])
        ],
    }


def _is_member(doc, email):
    target = (email or "").strip().lower()
    return any((r.email or "").strip().lower() == target for r in (doc.get("clenovia") or []))


def _auto_name(emails, limit=3):
    names = [(_resolve_name(e) or e) for e in emails]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" +{len(names) - limit}"


def _parse_list(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    return [e for e in raw if e] if isinstance(raw, list) else []


# ----------------------------------------------------------------------
# ENSURE — najdi alebo zaloz skupinu pre danu zostavu ludi
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def ensure():
    try:
        email, _ = verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        members = _parse_list(data.get("members"))
        members = list({(m or "").strip() for m in members if m})

        # Volajuci je clenom vzdy
        if not any((m or "").strip().lower() == email.strip().lower() for m in members):
            members.append(email)
        if len(members) < 3:
            return {"success": False, "error": "Skupina potrebuje aspoň troch účastníkov"}

        call_id = data.get("callId")
        key = _member_key(members)

        # Ucastnici konferencie otvaraju chat v roznom case a vtedy uz moze byt
        # v hovore niekto navyse -> podla samotnych clenov by kazdemu vysla INA
        # skupina a spravy by sa rozdelili. Preto sa v ramci jedneho hovoru
        # vsetci zidu v tej istej skupine a chybajuci sa do nej doplnia.
        if call_id:
            by_call = frappe.db.get_value("Skupina", {"hovor_id": call_id}, "name")
            if by_call:
                doc = frappe.get_doc("Skupina", by_call)
                added = False
                for m in members:
                    if not _is_member(doc, m):
                        doc.append("clenovia", {"email": m, "meno": _resolve_name(m) or m,
                                                "pridany": now_datetime()})
                        added = True
                if added:
                    doc.kluc_clenov = _member_key([r.email for r in doc.get("clenovia")])
                    doc.save(ignore_permissions=True)
                    frappe.db.commit()
                return {"success": True, "group": _group_payload(doc)}

        existing = frappe.db.get_value("Skupina", {"kluc_clenov": key}, "name")
        if existing:
            doc = frappe.get_doc("Skupina", existing)
            if call_id and doc.get("hovor_id") != call_id:
                doc.db_set("hovor_id", call_id)
            return {"success": True, "group": _group_payload(doc)}

        doc = frappe.get_doc({
            "doctype": "Skupina",
            "nazov": _auto_name(members),
            "kluc_clenov": key,
            "hovor_id": call_id,
        })
        now = now_datetime()
        for m in members:
            doc.append("clenovia", {"email": m, "meno": _resolve_name(m) or m, "pridany": now})
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "group": _group_payload(doc), "created": True}

    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Group Ensure Error")
        return {"success": False, "error": "Internal server error"}


# ----------------------------------------------------------------------
# MY GROUPS — zoznam mojich skupin
# ----------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def my_groups():
    try:
        email, _ = verify_bearer_and_get_email()
        rows = frappe.get_all(
            "Clen skupiny",
            filters={"email": email, "parenttype": "Skupina"},
            fields=["parent"],
        )
        groups = []
        for r in rows:
            try:
                groups.append(_group_payload(frappe.get_doc("Skupina", r["parent"])))
            except Exception:
                continue
        return {"success": True, "groups": groups}
    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Group List Error")
        return {"success": False, "error": "Internal server error"}


# ----------------------------------------------------------------------
# RENAME
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def rename():
    try:
        email, _ = verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        group_id = data.get("groupId")
        new_name = (data.get("name") or "").strip()
        if not group_id or not new_name:
            return {"success": False, "error": "Chýba skupina alebo názov"}

        doc = frappe.get_doc("Skupina", group_id)
        if not _is_member(doc, email):
            return {"success": False, "error": "Nie ste členom skupiny"}

        doc.nazov = new_name[:140]
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "group": _group_payload(doc)}
    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Group Rename Error")
        return {"success": False, "error": "Internal server error"}


# ----------------------------------------------------------------------
# ADD MEMBER — len clovek, ktoreho ma pridavajuci priradeneho
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def add_member():
    try:
        email, _ = verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        group_id = data.get("groupId")
        new_email = (data.get("email") or "").strip()
        if not group_id or not new_email:
            return {"success": False, "error": "Chýba skupina alebo používateľ"}

        doc = frappe.get_doc("Skupina", group_id)
        if not _is_member(doc, email):
            return {"success": False, "error": "Nie ste členom skupiny"}
        if _is_member(doc, new_email):
            return {"success": True, "group": _group_payload(doc)}
        if len(doc.get("clenovia") or []) >= 20:
            return {"success": False, "error": "Skupina je plná"}

        target_docname = (
            frappe.db.get_value("Poradca", {"email": new_email}, "name")
            or frappe.db.get_value("Klient", {"email": new_email}, "name")
        )
        if not target_docname:
            return {"success": False, "error": "Používateľ neexistuje"}

        _, adder_doc = get_actor_by_email(email)
        if not adder_doc:
            return {"success": False, "error": "Unauthorized"}
        linked = any(
            row.uzivatel_link == target_docname
            for row in (adder_doc.get("poradcovia") or [])
        )
        if not linked:
            return {"success": False, "error": "Tohto používateľa nemáte priradeného"}

        doc.append("clenovia", {
            "email": new_email,
            "meno": _resolve_name(new_email) or new_email,
            "pridany": now_datetime(),
        })
        doc.kluc_clenov = _member_key([r.email for r in doc.get("clenovia")])
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "group": _group_payload(doc)}
    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Group Add Error")
        return {"success": False, "error": "Internal server error"}


# ----------------------------------------------------------------------
# LEAVE — odchod zo skupiny (prazdna skupina sa zmaze)
# ----------------------------------------------------------------------
@frappe.whitelist(methods=["POST"], allow_guest=True)
def leave():
    try:
        email, _ = verify_bearer_and_get_email()
        data = frappe.local.form_dict or {}
        group_id = data.get("groupId")
        if not group_id:
            return {"success": False, "error": "Chýba skupina"}

        doc = frappe.get_doc("Skupina", group_id)
        if not _is_member(doc, email):
            return {"success": True}

        target = email.strip().lower()
        doc.clenovia = [r for r in (doc.get("clenovia") or [])
                        if (r.email or "").strip().lower() != target]

        if len(doc.clenovia) < 2:
            frappe.delete_doc("Skupina", doc.name, ignore_permissions=True, force=True)
            frappe.db.commit()
            return {"success": True, "deleted": True}

        doc.kluc_clenov = _member_key([r.email for r in doc.clenovia])
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True}
    except Exception:
        frappe.log_error(traceback.format_exc(), "BC Group Leave Error")
        return {"success": False, "error": "Internal server error"}
