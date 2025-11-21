# apps/bcservices/bcservices/api/public.py

import frappe
from frappe.utils import now_datetime
from .utils import ensure_settings

@frappe.whitelist(methods=["GET"], allow_guest=True)
def supply(year: int = None):
    """
    Public supply endpoint – iOS aj web môže volať bez autentifikácie.

    Vráti:
    - aktuálnu cenu
    - koľko tokenov je v treasury (voľné)
    - koľko tokenov bolo vydaných pre daný rok
    - koľko z nich je už predaných / držaných používateľmi
    """

    y = int(year or now_datetime().year)
    settings = ensure_settings()

    # Treasury (voľné tokeny)
    treasury_tokens = frappe.get_all(
        "BC Token",
        filters={
            "aktualny_drzitel": ["is", "null"],
            "stav": "active",
            "vydany_rok": y,
        },
        pluck="name"
    )

    # Total minted (všetky tokeny pre daný rok)
    minted = frappe.db.count(
        "BC Token",
        {
            "vydany_rok": y,
        }
    )

    # Total sold (tokeny, ktoré už majú držiteľa)
    sold = frappe.db.count(
        "BC Token",
        {
            "vydany_rok": y,
            "aktualny_drzitel": ["is", "set"],
        }
    )

    return {
        "year": y,
        "priceEur": float(settings.aktualna_cena_eur or 0),
        "treasuryAvailable": len(treasury_tokens),
        "totalMinted": minted,
        "totalSold": sold
    }
