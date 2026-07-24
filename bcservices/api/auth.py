# apps/bcservices/bcservices/api/auth.py
from __future__ import annotations

import frappe

from .utils import (
    verify_bearer_and_get_email,
    get_actor_by_email,
    make_jwt,
)


# -----------------------------------------------------------------------------
# LOGIN – email + heslo → JWT
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"], allow_guest=True)
def login(email: str | None = None, password: str | None = None):
    """
    iOS volá: /api/method/bcservices.api.auth.login
    Telo: { "email": "...", "password": "..." }
    Vráti JWT, ktorý sa posiela v hlavičke Authorization: Bearer <token>.
    """
    data = frappe.local.form_dict or {}
    email = email or data.get("email")
    password = password or data.get("password")

    if not email or not password:
        frappe.throw("Chýba email alebo heslo", frappe.ValidationError)

    email = email.strip()

    doctype, doc = get_actor_by_email(email)
    if not doc or (doc.heslo or "") != password:
        frappe.throw("Nesprávny email alebo heslo", frappe.AuthenticationError)

    role = "advisor" if doctype == "Poradca" else "client"
    full_name = (doc.meno if doctype == "Poradca" else doc.username) or email

    return {
        "success": True,
        "token": make_jwt(email, role),
        "email": email,
        "role": role,
        "full_name": full_name,
    }


# -----------------------------------------------------------------------------
# Obojsmerné prepojenie Poradca/Klient (DocType hooky)
# -----------------------------------------------------------------------------

def _sync_connections(doc):
    """Zabezpečí obojsmerné prepojenie medzi Poradca/Klient."""
    for row in doc.get("poradcovia") or []:
        if not row.uzivatel_link or not row.typ_uzivatela:
            continue

        try:
            linked_doc = frappe.get_doc(row.typ_uzivatela, row.uzivatel_link)
        except frappe.DoesNotExistError:
            continue

        already_linked = any(
            r.uzivatel_link == doc.name and r.typ_uzivatela == doc.doctype
            for r in (linked_doc.get("poradcovia") or [])
        )

        if not already_linked:
            linked_doc.flags.in_sync = True  # zabraňuje rekurzii
            linked_doc.flags.ignore_permissions = True
            linked_doc.append("poradcovia", {
                "typ_uzivatela": doc.doctype,
                "uzivatel_link": doc.name
            })
            linked_doc.save()


def on_update_bc_pouzivatel(doc, method=None):
    if not doc.flags.get("in_sync"):
        _sync_connections(doc)


def on_update_bc_poradca(doc, method=None):
    if not doc.flags.get("in_sync"):
        _sync_connections(doc)


# -----------------------------------------------------------------------------
# Kaskádové mazanie používateľa (Klient / Poradca)
# -----------------------------------------------------------------------------

def on_trash_bc_user(doc, method=None):
    """
    Pred zmazaním Klienta/Poradcu zmaže VŠETKY doctypy, ktoré naň odkazujú
    (hovory, tokeny, transakcie, inzeráty, správy chatu) a zrkadlené
    prepojenia (Poradca Klienta) u druhej strany.

    on_trash beží PRED Frappe kontrolou linkov, takže po vyčistení už
    mazanie samotného používateľa neprejde cez LinkExistsError.

    Riešenie je generické: nájde všetky Link polia (aj Custom Field), ktoré
    ukazujú na tento doctype, takže funguje aj keď v budúcnosti pribudne
    nový doctype s väzbou na Klienta/Poradcu.
    """
    doctype = doc.doctype
    name = doc.name

    # 1. Nájdi všetky Link polia mieriace na tento doctype (štandardné + custom).
    targets = []
    for r in frappe.get_all(
        "DocField",
        filters={"fieldtype": "Link", "options": doctype},
        fields=["parent", "fieldname"],
    ):
        targets.append((r.parent, r.fieldname))
    for r in frappe.get_all(
        "Custom Field",
        filters={"fieldtype": "Link", "options": doctype},
        fields=["dt", "fieldname"],
    ):
        targets.append((r.dt, r.fieldname))

    # 2. Zmaž nalinkované záznamy. force=True obíde poradie závislostí medzi nimi.
    for linked_dt, fieldname in targets:
        if not linked_dt or linked_dt == doctype:
            continue
        # child (istable) doctypy sa mažú spolu s rodičom, nemažeme ich samostatne
        if frappe.get_meta(linked_dt).istable:
            continue
        for rec in frappe.get_all(linked_dt, filters={fieldname: name}, pluck="name"):
            frappe.delete_doc(
                linked_dt, rec,
                force=True, ignore_permissions=True, delete_permanently=True,
            )

    # 3. Zrkadlené prepojenia: Poradca Klienta je Dynamic Link child table
    #    aj u druhej strany (napr. riadok u poradcu ukazujúci na tohto klienta).
    #    Vlastné child riadky používateľa sa zmažú s ním; tieto sú u iných rodičov.
    frappe.db.delete("Poradca Klienta", {"typ_uzivatela": doctype, "uzivatel_link": name})


# -----------------------------------------------------------------------------
# Zoznamy prepojených používateľov
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST", "GET"], allow_guest=True)
def get_my_connected_users():
    """
    Vráti zoznam pripojených používateľov (klientov + poradcov) pre prihláseného poradcu.
    """
    try:
        email, _ = verify_bearer_and_get_email()
    except Exception as e:
        frappe.throw(f"Neautorizovaný prístup: {e}", frappe.PermissionError)

    poradca_name = frappe.db.get_value("Poradca", {"email": email}, "name")

    if not poradca_name:
        return {
            "success": False,
            "error": "V systéme neexistuje poradca s týmto emailom."
        }

    doc = frappe.get_doc("Poradca", poradca_name)

    users_list = []

    for row in doc.get("poradcovia") or []:
        if not row.uzivatel_link or not row.typ_uzivatela:
            continue

        try:
            user_doc = frappe.get_doc(row.typ_uzivatela, row.uzivatel_link)

            devices = user_doc.get("zariadenie") or []
            has_voip = any(d.voip_token for d in devices)

            if row.typ_uzivatela == "Poradca":
                name = user_doc.meno
                user_type = "advisor"
            else:  # Klient
                name = user_doc.username
                user_type = "client"

            users_list.append({
                "name": name,
                "email": user_doc.email,
                "has_voip": has_voip,
                "type": user_type
            })

        except frappe.DoesNotExistError:
            continue

    return {
        "success": True,
        "users": users_list
    }


@frappe.whitelist(methods=["POST", "GET"], allow_guest=True)
def get_my_advisors():
    """
    Vráti zoznam poradcov (a voliteľne klientov) priradených k prihlásenému klientovi.
    """
    try:
        email, _ = verify_bearer_and_get_email()
    except Exception as e:
        frappe.throw(f"Neautorizovaný prístup: {e}", frappe.PermissionError)

    klient_name = frappe.db.get_value("Klient", {"email": email}, "name")

    if not klient_name:
        return {
            "success": False,
            "error": "V systéme neexistuje klient s týmto emailom."
        }

    doc = frappe.get_doc("Klient", klient_name)

    advisors_list = []

    for row in doc.get("poradcovia") or []:
        if hasattr(row, 'uzivatel_link') and row.uzivatel_link:
            if not row.typ_uzivatela:
                continue

            try:
                user_doc = frappe.get_doc(row.typ_uzivatela, row.uzivatel_link)

                devices = user_doc.get("zariadenie") or []
                has_voip = any(d.voip_token for d in devices)

                if row.typ_uzivatela == "Poradca":
                    name = user_doc.meno
                else:
                    name = user_doc.username

                advisors_list.append({
                    "name": name,
                    "email": user_doc.email,
                    "has_voip": has_voip
                })
            except frappe.DoesNotExistError:
                continue

        elif hasattr(row, 'poradca_link') and row.poradca_link:
            # STARÝ FORMÁT (spätná kompatibilita)
            try:
                p = frappe.get_doc("Poradca", row.poradca_link)

                devices = p.get("zariadenie") or []
                has_voip = any(d.voip_token for d in devices)

                advisors_list.append({
                    "name": p.meno,
                    "email": p.email,
                    "has_voip": has_voip
                })
            except frappe.DoesNotExistError:
                continue

    return {
        "success": True,
        "advisors": advisors_list
    }
