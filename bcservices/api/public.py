# apps/bcservices/bcservices/api/public.py
import frappe
from .utils import ensure_settings

@frappe.whitelist(methods=["GET"], allow_guest=True)
def supply(year: int = None):
    y = int(year or frappe.utils.now_datetime().year)
    settings = ensure_settings()
    # Treasury = tokeny bez držiteľa a active v danom roku
    treasury = frappe.get_all("BC Token",
                              filters={"aktualny_drzitel": ["is", "null"],
                                       "stav": "active",
                                       "vydany_rok": y},
                              pluck="name")
    return {
        "year": y,
        "priceEur": float(settings.aktualna_cena_eur or 0),
        "treasuryAvailable": len(treasury),
        "totalMinted": 0,
        "totalSold": 0
    }
