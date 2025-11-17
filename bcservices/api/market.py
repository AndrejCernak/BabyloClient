# apps/bcservices/bcservices/api/market.py
import frappe
from frappe.utils import now_datetime
from .utils import ensure_bc_user_by_clerk, ensure_settings

@frappe.whitelist(methods=["POST"])
def purchase(userId: str=None, quantity: int=None, year: int=None):
    data = frappe.local.form_dict
    userId = userId or data.get("userId")
    quantity = int(quantity or data.get("quantity") or 0)
    year = int(year or data.get("year") or frappe.utils.now_datetime().year)

    if not userId or quantity <= 0:
        frappe.throw("Missing or invalid userId/quantity", frappe.ValidationError)

    user = ensure_bc_user_by_clerk(userId)
    settings = ensure_settings()
    unit_price = float(settings.aktualna_cena_eur or 0)
    if unit_price <= 0:
        frappe.throw("Treasury price not set", frappe.ValidationError)

    max_per_year = int(frappe.conf.get("max_primary_tokens_per_user") or 20)
    owned = frappe.db.count("BC Token", {
        "aktualny_drzitel": user.name,
        "vydany_rok": year,
        "stav": ["in", ["active","listed"]]
    })
    if owned + quantity > max_per_year:
        frappe.throw(f"Primary limit is {max_per_year} tokens per user for year {year}", frappe.ValidationError)

    available = frappe.get_all("BC Token",
        filters={"aktualny_drzitel": ["is","null"], "vydany_rok": year, "stav":"active"},
        fields=["name"], order_by="creation asc", limit_page_length=quantity)

    if len(available) < quantity:
        frappe.throw("Not enough tokens in treasury", frappe.ValidationError)

    purchased = [a["name"] for a in available]

    # transakcia (ledger)
    tr = frappe.get_doc({
        "doctype": "BC Transakcia",
        "pouzivatel": user.name,
        "typ": "friday_purchase",
        "suma_eur": unit_price * quantity,
        "zmena_sekund": 0,
        "poznamka": f"friday:{year}; qty:{quantity}; unit:{unit_price}"
    })
    tr.insert(ignore_permissions=True)
    tr.submit()

    # priraď tokeny userovi
    for n in purchased:
        frappe.db.set_value("BC Token", n, "aktualny_drzitel", user.name)

    # optional: vytvor Purchase Items (ak máš ako samostatný DocType alebo pripoj k BC Platba)
    for n in purchased:
        try:
            item = frappe.get_doc({
                "doctype": "BC Polozka Nakupu",
                "token": n,
                "jednotkova_cena_eur": unit_price,
                "rok": year
            })
            item.insert(ignore_permissions=True)
        except Exception:
            pass

    # response
    tokens = frappe.get_all("BC Token",
        filters={"aktualny_drzitel": user.name},
        fields=["name as id","vydany_rok as issuedYear","minuty_ostavajuce as minutesRemaining","stav as status"],
        order_by="vydany_rok asc, creation asc")
    total = sum([t["minutesRemaining"] for t in tokens if t["status"]=="active"])
    return {
        "success": True,
        "year": year,
        "unitPrice": unit_price,
        "quantity": quantity,
        "purchasedTokenIds": purchased,
        "totalMinutes": total,
        "tokens": tokens
    }

@frappe.whitelist(methods=["POST"])
def list_token(sellerId: str=None, tokenId: str=None, priceEur: float=None):
    data = frappe.local.form_dict
    sellerId = sellerId or data.get("sellerId")
    tokenId = tokenId or data.get("tokenId")
    price = float(priceEur or data.get("priceEur") or 0)

    if not sellerId or not tokenId or price <= 0:
        frappe.throw("Missing sellerId/tokenId/priceEur", frappe.ValidationError)
    seller = ensure_bc_user_by_clerk(sellerId)
    tok = frappe.get_doc("BC Token", tokenId)
    if tok.aktualny_drzitel != seller.name:
        frappe.throw("Token does not belong to seller", frappe.PermissionError)
    if tok.stav != "active":
        frappe.throw("Token not active", frappe.ValidationError)

    # existuje open listing?
    exist = frappe.get_all("BC Inzerat", filters={"token": tok.name, "stav":"open"}, pluck="name")
    if exist:
        frappe.throw("Token already listed", frappe.ValidationError)

    lst = frappe.get_doc({
        "doctype": "BC Inzerat",
        "token": tok.name,
        "predavajuci": seller.name,
        "cena_eur": price,
        "stav": "open"
    })
    lst.insert(ignore_permissions=True)
    frappe.db.set_value("BC Token", tok.name, "stav", "listed")
    return {"success": True, "listing": {"name": lst.name}}

@frappe.whitelist(methods=["POST"])
def cancel_listing(sellerId: str=None, listingId: str=None):
    data = frappe.local.form_dict
    sellerId = sellerId or data.get("sellerId")
    listingId = listingId or data.get("listingId")
    if not sellerId or not listingId:
        frappe.throw("Missing sellerId/listingId", frappe.ValidationError)

    seller = ensure_bc_user_by_clerk(sellerId)
    lst = frappe.get_doc("BC Inzerat", listingId)
    if lst.predavajuci != seller.name:
        frappe.throw("Unauthorized", frappe.PermissionError)
    if lst.stav != "open":
        frappe.throw("Listing is not open", frappe.ValidationError)

    tok = frappe.get_doc("BC Token", lst.token)
    lst.stav = "cancelled"
    lst.uzavrete_kedy = now_datetime()
    lst.save(ignore_permissions=True)
    frappe.db.set_value("BC Token", tok.name, "stav", "active")
    return {"success": True}

@frappe.whitelist(methods=["POST"])
def buy_listing(buyerId: str=None, listingId: str=None):
    data = frappe.local.form_dict
    buyerId = buyerId or data.get("buyerId")
    listingId = listingId or data.get("listingId")
    if not buyerId or not listingId:
        frappe.throw("Missing buyerId/listingId", frappe.ValidationError)

    buyer = ensure_bc_user_by_clerk(buyerId)
    lst = frappe.get_doc("BC Inzerat", listingId)
    if lst.stav != "open":
        frappe.throw("Listing nie je dostupný", frappe.ValidationError)
    if lst.predavajuci == buyer.name:
        frappe.throw("Nemôžeš kúpiť vlastný listing", frappe.ValidationError)

    tok = frappe.get_doc("BC Token", lst.token)
    # (voliteľne) limit 20/rok aj pre sekundárny
    owned = frappe.db.count("BC Token", {
        "aktualny_drzitel": buyer.name,
        "vydany_rok": tok.vydany_rok,
        "stav": ["in", ["active","listed"]]
    })
    if owned >= 20:
        frappe.throw(f"Limit 20 tokenov pre rok {tok.vydany_rok} dosiahnutý", frappe.ValidationError)

    # lock + presuny
    lst.stav = "sold"
    lst.uzavrete_kedy = now_datetime()
    lst.save(ignore_permissions=True)

    if not (tok.aktualny_drzitel == lst.predavajuci and tok.stav == "listed" and (tok.minuty_ostavajuce or 0) > 0):
        frappe.throw("Token nie je možné kúpiť", frappe.ValidationError)

    frappe.db.set_value("BC Token", tok.name, {"aktualny_drzitel": buyer.name, "stav":"active"})

    trade = frappe.get_doc({
        "doctype": "BC Obchod",
        "inzerat": lst.name,
        "token": tok.name,
        "predavajuci": lst.predavajuci,
        "kupujuci": buyer.name,
        "cena_eur": lst.cena_eur
    })
    trade.insert(ignore_permissions=True)

    # transakcie
    for (u, typ) in [(buyer.name,"friday_trade_buy"), (lst.predavajuci,"friday_trade_sell")]:
        tx = frappe.get_doc({
            "doctype": "BC Transakcia",
            "pouzivatel": u,
            "typ": typ,
            "suma_eur": lst.cena_eur,
            "zmena_sekund": 0,
            "poznamka": f"listing:{lst.name}; token:{tok.name}"
        })
        tx.insert(ignore_permissions=True)
        tx.submit()

    return {"success": True, "tradeId": trade.name, "tokenId": tok.name, "priceEur": float(lst.cena_eur)}

@frappe.whitelist(methods=["GET"], allow_guest=True)
def listings():
    items = frappe.get_all("BC Inzerat",
        filters={"stav":"open"},
        order_by="creation desc",
        fields=["name","token","predavajuci","cena_eur","creation"])
    # ak chceš pridať token detail:
    return {"items": items}
