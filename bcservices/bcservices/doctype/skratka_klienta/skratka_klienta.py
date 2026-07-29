# Copyright (c) 2026, Focus Hub s.r.o and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SkratkaKlienta(Document):
	"""
	Evidenčný záznam klienta (databáza skratiek).

	Zámerne oddelené od DocType Klient – ten slúži používateľom mobilnej
	aplikácie a vytvára im prístup do nej. Tento DocType žiadny prístup
	nevytvára, je to len zoznam skratiek pre výkazy času.
	"""

	def validate(self):
		# skratka sa ukladá očistená – bez medzier a bez úvodnej mriežky
		self.skratka = (self.skratka or "").strip().lstrip("#").strip()

		if not self.skratka:
			frappe.throw("Skratka je povinná.")

		if self.nazov:
			self.nazov = self.nazov.strip()
