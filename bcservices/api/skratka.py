# apps/bcservices/bcservices/api/skratka.py
"""
Endpointy pre iPhone skratku „Výkaz“.

Obsahuje:
  • get_my_clients  – zoznam klientov prihláseného poradcu (značka + meno),
                      s voliteľným vyhľadávaním na strane servera,
  • import_klientov – hromadné založenie klientov podľa zoznamu značiek.

Zámerne v samostatnom module, aby sa nezasahovalo do endpointov,
ktoré používa mobilná aplikácia.
"""
from __future__ import annotations

import json
import unicodedata

import frappe
from frappe.utils import cint

from .utils import verify_bearer_and_get_email

# najviac značiek v jednej dávke – väčší import by prekročil časový limit
MAX_ZNACIEK = 200


# -----------------------------------------------------------------------------
# POMOCNÉ FUNKCIE
# -----------------------------------------------------------------------------

def _normalizuj(hodnota) -> str:
    """Očistí značku – odstráni medzery a prípadnú úvodnú mriežku."""
    if not hodnota:
        return ""
    if not isinstance(hodnota, str):
        hodnota = str(hodnota)
    return hodnota.strip().lstrip("#").strip()


def _bez_diakritiky(text: str) -> str:
    """Malé písmená bez diakritiky – aby „zilina“ našla aj „Žilina“."""
    return (
        unicodedata.normalize("NFKD", (text or "").lower())
        .encode("ascii", "ignore")
        .decode()
    )


def _pravda(hodnota, predvolene: bool = True) -> bool:
    """Tolerantné čítanie prepínača – znesie 1/0, true/false, áno/nie."""
    if hodnota is None or hodnota == "":
        return predvolene
    if isinstance(hodnota, bool):
        return hodnota
    if isinstance(hodnota, str):
        return hodnota.strip().lower() not in ("0", "false", "no", "nie", "off")
    return bool(cint(hodnota))


def _popis_chyby(e: Exception) -> str:
    """Zrozumiteľná hláška – bez vnútorných detailov databázy."""
    if isinstance(e, (frappe.DuplicateEntryError, frappe.UniqueValidationError)):
        return "Klient s týmto menom už existuje."
    return "Klienta sa nepodarilo vytvoriť."


def _poradca_prihlaseneho() -> tuple[str | None, str | None]:
    """Vráti (name dokumentu Poradca, chyba). Nikdy nehádže výnimku."""
    try:
        email, _ = verify_bearer_and_get_email()
    except Exception:
        return None, "Chýbajúci alebo neplatný token."

    if not email:
        return None, "Chýbajúci alebo neplatný token."

    name = frappe.db.get_value("Poradca", {"email": email}, "name")
    if not name:
        return None, "V systéme neexistuje poradca s týmto emailom."

    return name, None


def _klienti_poradcu(poradca_name: str) -> list[str]:
    """Vráti mená dokumentov Klient prepojených na daného poradcu."""
    poradca = frappe.get_doc("Poradca", poradca_name)
    return [
        row.uzivatel_link
        for row in (poradca.get("poradcovia") or [])
        if row.typ_uzivatela == "Klient" and row.uzivatel_link
    ]


# -----------------------------------------------------------------------------
# ZOZNAM KLIENTOV – pre výber v skratke, s vyhľadávaním podľa značky
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["GET", "POST"], allow_guest=True)
def get_my_clients(q: str | None = None):
    """
    Vráti klientov prihláseného poradcu ako [{"kod": ..., "meno": ...}],
    zoradených podľa značky.

    Voliteľný parameter q filtruje na strane servera – bez ohľadu na veľkosť
    písmen aj diakritiku, podľa značky aj mena. Prázdne q vráti všetkých.

    Volanie: GET /api/method/bcservices.api.skratka.get_my_clients?q=agro
    Hlavička: X-Clerk-Authorization: Bearer <JWT>
    """
    poradca_name, chyba = _poradca_prihlaseneho()
    if not poradca_name:
        return {"success": False, "error": chyba, "clients": []}

    hladanie = _bez_diakritiky(_normalizuj(q))

    mena = _klienti_poradcu(poradca_name)
    if not mena:
        return {"success": True, "clients": []}

    # jeden dotaz namiesto get_doc v cykle
    zaznamy = frappe.get_all(
        "Klient",
        filters={"name": ["in", mena]},
        fields=["name", "username", "znacka_klienta"],
    )

    klienti = []
    for z in zaznamy:
        # bez značky by klient v skratke chýbal – použijeme meno ako náhradu
        kod = (
            _normalizuj(z.get("znacka_klienta"))
            or _normalizuj(z.get("username"))
            or _normalizuj(z.get("name"))
        )
        meno = z.get("username") or z.get("name") or ""

        if not kod:
            continue

        if hladanie and hladanie not in _bez_diakritiky(kod) and hladanie not in _bez_diakritiky(meno):
            continue

        klienti.append({"kod": kod, "meno": meno})

    klienti.sort(key=lambda k: _bez_diakritiky(k["kod"]))

    return {"success": True, "clients": klienti}


# -----------------------------------------------------------------------------
# HROMADNÝ IMPORT KLIENTOV PODĽA ZNAČIEK
# -----------------------------------------------------------------------------

def _nacitaj_znacky(tags):
    """
    Prijme pole, JSON reťazec alebo surové telo požiadavky.
    Vráti zoznam značiek, alebo None ak sa vstup nedá prečítať.
    """
    if tags is None:
        data = frappe.local.form_dict or {}
        # „data“ = sem Frappe ukladá holé JSON pole z tela požiadavky
        for kluc in ("tags", "znacky", "data"):
            if kluc in data:
                tags = data.get(kluc)
                break

    if tags is None and getattr(frappe, "request", None) is not None:
        try:
            surove = frappe.request.get_data(as_text=True)
        except Exception:
            surove = None

        if surove:
            try:
                nacitane = json.loads(surove)
            except Exception:
                nacitane = None

            if isinstance(nacitane, list):
                tags = nacitane
            elif isinstance(nacitane, dict):
                for kluc in ("tags", "znacky", "data"):
                    if kluc in nacitane:
                        tags = nacitane.get(kluc)
                        break

    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = tags.replace(",", "\n").splitlines()

    return tags if isinstance(tags, list) else None


def _prepoj_na_poradcu(poradca_name: str, klient_mena: list[str]) -> list[str]:
    """Doplní klientov do tabuľky poradcu, ak tam ešte nie sú. Ukladá raz."""
    poradca = frappe.get_doc("Poradca", poradca_name)

    uz_prepojeni = {
        row.uzivatel_link
        for row in (poradca.get("poradcovia") or [])
        if row.typ_uzivatela == "Klient" and row.uzivatel_link
    }

    pridane = []
    for meno in klient_mena:
        if meno in uz_prepojeni:
            continue
        poradca.append("poradcovia", {"typ_uzivatela": "Klient", "uzivatel_link": meno})
        uz_prepojeni.add(meno)
        pridane.append(meno)

    if pridane:
        poradca.save(ignore_permissions=True)

    return pridane


def _uvolni_savepoint(nazov: str) -> None:
    """Uvoľní savepoint, ak to daná verzia Frappe podporuje."""
    uvolni = getattr(frappe.db, "release_savepoint", None)
    if uvolni:
        try:
            uvolni(nazov)
        except Exception:
            pass


@frappe.whitelist(methods=["POST"], allow_guest=True)
def import_klientov(tags=None, prepojit=1):
    """
    Hromadne založí klientov podľa zoznamu značiek.

    Telo požiadavky: JSON pole značiek, napr. ["agrovio", "barkoci"],
    prípadne objekt {"tags": ["agrovio", ...]}.

    Pre každú značku: ak klient s takou značkou (alebo menom) ešte neexistuje,
    vytvorí ho – username aj znacka_klienta = značka. Porovnáva sa bez ohľadu
    na veľkosť písmen, takže opakované volanie nič nezduplikuje.

    Klienta, ktorý už patrí inému poradcovi, import nepreberá – skončí v chybách.

    prepojit=1 (predvolene) zároveň prepojí klientov na prihláseného poradcu –
    inak by sa v skratke nezobrazili.

    Naraz najviac MAX_ZNACIEK značiek; pre pokoj odporúčame dávky do 50.

    Volanie: POST /api/method/bcservices.api.skratka.import_klientov
    Hlavička: X-Clerk-Authorization: Bearer <JWT>
    """
    poradca_name, chyba = _poradca_prihlaseneho()
    if not poradca_name:
        return {"success": False, "error": chyba}

    znacky = _nacitaj_znacky(tags)
    if znacky is None:
        return {
            "success": False,
            "error": 'Očakáva sa JSON pole značiek, napr. ["agrovio", "barkoci"].',
        }

    if len(znacky) > MAX_ZNACIEK:
        return {
            "success": False,
            "error": f"Naraz je možné importovať najviac {MAX_ZNACIEK} značiek.",
        }

    # kto už klienta vlastní – aby import nesiahol na klientov iného poradcu
    vlastnici: dict[str, set[str]] = {}
    for r in frappe.get_all(
        "Poradca Klienta",
        filters={"parenttype": "Poradca", "typ_uzivatela": "Klient"},
        fields=["parent", "uzivatel_link"],
    ):
        if r.get("uzivatel_link"):
            vlastnici.setdefault(r["uzivatel_link"], set()).add(r["parent"])

    # index existujúcich klientov: značka AJ username (username == názov dokumentu)
    existujuce: dict[str, str] = {}
    for r in frappe.get_all("Klient", fields=["name", "username", "znacka_klienta"]):
        for surovy in (r.get("znacka_klienta"), r.get("username"), r.get("name")):
            kluc = _normalizuj(surovy).lower()
            if kluc:
                existujuce.setdefault(kluc, r["name"])

    vytvorene: list[str] = []
    preskocene: list[str] = []
    chyby: list[dict] = []
    na_prepojenie: list[str] = []
    videne: set[str] = set()

    # klient bez emailu a hesla neposiela uvítací mail (viď Klient.send_welcome_email),
    # správu o tom ale netreba vracať pri každom zázname
    povodne_mute = frappe.flags.get("mute_messages")
    frappe.flags.mute_messages = True
    try:
        for surova in znacky:
            kod = _normalizuj(surova)
            if not kod:
                continue

            kluc = kod.lower()
            if kluc in videne:
                continue
            videne.add(kluc)

            # klient už existuje
            if kluc in existujuce:
                meno_klienta = existujuce[kluc]
                majitelia = vlastnici.get(meno_klienta) or set()

                if majitelia and poradca_name not in majitelia:
                    chyby.append({
                        "znacka": kod,
                        "chyba": "Klient s touto značkou už patrí inému poradcovi.",
                    })
                    continue

                # doplníme značku, ak ju záznam ešte nemá
                if not (frappe.db.get_value("Klient", meno_klienta, "znacka_klienta") or "").strip():
                    frappe.db.set_value("Klient", meno_klienta, "znacka_klienta", kod)

                preskocene.append(kod)
                na_prepojenie.append(meno_klienta)
                continue

            # nový klient – savepoint, aby zlyhanie neprepísalo predošlé záznamy
            sp = f"skratka_{len(vytvorene) + len(chyby)}"
            frappe.db.savepoint(sp)
            try:
                doc = frappe.get_doc({
                    "doctype": "Klient",
                    "username": kod,
                    "znacka_klienta": kod,
                })
                doc.insert(ignore_permissions=True)
            except Exception as e:
                frappe.db.rollback(save_point=sp)
                frappe.log_error(frappe.get_traceback(), "skratka.import_klientov")
                chyby.append({"znacka": kod, "chyba": _popis_chyby(e)})
                continue

            _uvolni_savepoint(sp)

            existujuce[kluc] = doc.name
            vytvorene.append(kod)
            na_prepojenie.append(doc.name)
    finally:
        frappe.flags.mute_messages = povodne_mute

    prepojene: list[str] = []
    if _pravda(prepojit) and na_prepojenie:
        prepojene = _prepoj_na_poradcu(poradca_name, na_prepojenie)

    return {
        "success": True,
        "vytvorene": vytvorene,
        "preskocene": preskocene,
        "prepojene": prepojene,
        "chyby": chyby,
    }
