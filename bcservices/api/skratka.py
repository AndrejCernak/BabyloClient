# apps/bcservices/bcservices/api/skratka.py
"""
Endpointy pre iPhone skratku „Výkaz“ a pre hromadný import skratiek klientov.

Pracuje výhradne nad DocType „Skratka Klienta“ – evidenčným zoznamom klientov.
Do DocType „Klient“ (používatelia mobilnej aplikácie) sa zámerne nesiaha,
pretože každý záznam v ňom vytvára prístup do aplikácie.

Autentifikácia je štandardná Frappe:
    Authorization: token <api_key>:<api_secret>
"""
from __future__ import annotations

import json
import unicodedata

import frappe

DOCTYPE = "Skratka Klienta"

# najviac skratiek v jednej dávke – väčší import by prekročil časový limit
MAX_SKRATIEK = 200


# -----------------------------------------------------------------------------
# POMOCNÉ FUNKCIE
# -----------------------------------------------------------------------------

def _normalizuj(hodnota) -> str:
	"""Očistí skratku – odstráni medzery a prípadnú úvodnú mriežku."""
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


def _popis_chyby(e: Exception) -> str:
	"""Zrozumiteľná hláška – bez vnútorných detailov databázy."""
	if isinstance(e, (frappe.DuplicateEntryError, frappe.UniqueValidationError)):
		return "Skratka už existuje."
	if isinstance(e, frappe.PermissionError):
		return "Chýba oprávnenie na vytvorenie záznamu."
	return "Záznam sa nepodarilo vytvoriť."


def _uvolni_savepoint(nazov: str) -> None:
	"""Uvoľní savepoint, ak to daná verzia Frappe podporuje."""
	uvolni = getattr(frappe.db, "release_savepoint", None)
	if uvolni:
		try:
			uvolni(nazov)
		except Exception:
			pass


def _nacitaj_skratky(tags):
	"""
	Prijme pole, JSON reťazec alebo surové telo požiadavky.
	Vráti zoznam skratiek, alebo None ak sa vstup nedá prečítať.
	"""
	if tags is None:
		data = frappe.local.form_dict or {}
		# „data“ = sem Frappe ukladá holé JSON pole z tela požiadavky
		for kluc in ("tags", "skratky", "znacky", "data"):
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
				for kluc in ("tags", "skratky", "znacky", "data"):
					if kluc in nacitane:
						tags = nacitane.get(kluc)
						break

	if isinstance(tags, str):
		try:
			tags = json.loads(tags)
		except Exception:
			tags = tags.replace(",", "\n").splitlines()

	return tags if isinstance(tags, list) else None


# -----------------------------------------------------------------------------
# ZOZNAM KLIENTOV – pre výber v skratke, s vyhľadávaním
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["GET", "POST"])
def get_klienti(q: str | None = None, vsetky: int | str | None = None):
	"""
	Vráti skratky klientov ako [{"kod": ..., "meno": ...}], zoradené podľa skratky.

	q       – voliteľný filter; hľadá v skratke aj názve, bez ohľadu na veľkosť
	          písmen a diakritiku. Prázdne q vráti všetkých.
	vsetky  – 1 = vrátiť aj neaktívnych. Predvolene sa vracajú len aktívni.

	Volanie: GET /api/method/bcservices.api.skratka.get_klienti?q=agro
	Hlavička: Authorization: token <api_key>:<api_secret>
	"""
	if q is None:
		q = (frappe.local.form_dict or {}).get("q")

	hladanie = _bez_diakritiky(_normalizuj(q))

	filtre = {}
	if not frappe.utils.cint(vsetky):
		filtre["aktivny"] = 1

	zaznamy = frappe.get_all(
		DOCTYPE,
		filters=filtre,
		fields=["name", "skratka", "nazov"],
	)

	klienti = []
	for z in zaznamy:
		kod = _normalizuj(z.get("skratka")) or _normalizuj(z.get("name"))
		if not kod:
			continue

		meno = (z.get("nazov") or "").strip() or kod

		if hladanie and hladanie not in _bez_diakritiky(kod) and hladanie not in _bez_diakritiky(meno):
			continue

		klienti.append({"kod": kod, "meno": meno})

	klienti.sort(key=lambda k: _bez_diakritiky(k["kod"]))

	return {"success": True, "clients": klienti}


# -----------------------------------------------------------------------------
# HROMADNÝ IMPORT SKRATIEK
# -----------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def import_klientov(tags=None):
	"""
	Hromadne založí skratky klientov v DocType „Skratka Klienta“.

	Telo požiadavky: JSON pole skratiek, napr. ["agrovio", "barkoci"],
	prípadne objekt {"tags": ["agrovio", ...]}.

	Pre každú skratku: ak ešte neexistuje, vytvorí nový záznam. Porovnáva sa
	bez ohľadu na veľkosť písmen a bez úvodnej mriežky, takže opakované volanie
	nič nezduplikuje.

	Naraz najviac MAX_SKRATIEK skratiek.

	Volanie: POST /api/method/bcservices.api.skratka.import_klientov
	Hlavička: Authorization: token <api_key>:<api_secret>
	"""
	skratky = _nacitaj_skratky(tags)
	if skratky is None:
		return {
			"success": False,
			"error": 'Očakáva sa JSON pole skratiek, napr. ["agrovio", "barkoci"].',
		}

	if len(skratky) > MAX_SKRATIEK:
		return {
			"success": False,
			"error": f"Naraz je možné importovať najviac {MAX_SKRATIEK} skratiek.",
		}

	# index existujúcich záznamov – kľúč bez ohľadu na veľkosť písmen
	existujuce: dict[str, str] = {}
	for r in frappe.get_all(DOCTYPE, fields=["name", "skratka"]):
		for surova in (r.get("skratka"), r.get("name")):
			kluc = _normalizuj(surova).lower()
			if kluc:
				existujuce.setdefault(kluc, r["name"])

	vytvorene: list[str] = []
	preskocene: list[str] = []
	chyby: list[dict] = []
	videne: set[str] = set()

	for surova in skratky:
		kod = _normalizuj(surova)
		if not kod:
			continue

		kluc = kod.lower()
		if kluc in videne:
			continue
		videne.add(kluc)

		if kluc in existujuce:
			preskocene.append(kod)
			continue

		# savepoint, aby zlyhanie jedného záznamu nezhodilo celú dávku
		sp = f"skratka_{len(vytvorene) + len(chyby)}"
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc({"doctype": DOCTYPE, "skratka": kod})
			doc.insert()
		except Exception as e:
			frappe.db.rollback(save_point=sp)
			frappe.log_error(frappe.get_traceback(), "skratka.import_klientov")
			chyby.append({"skratka": kod, "chyba": _popis_chyby(e)})
			continue

		_uvolni_savepoint(sp)

		existujuce[kluc] = doc.name
		vytvorene.append(kod)

	return {
		"success": True,
		"vytvorene": vytvorene,
		"preskocene": preskocene,
		"chyby": chyby,
	}
