import frappe
import html
from frappe.model.document import Document

class Poradca(Document):

    def after_insert(self):
        """
        Táto metóda sa spustí automaticky hneď po vytvorení a uložení nového poradcu.
        """
        self.send_welcome_email()

    @frappe.whitelist()
    def send_welcome_email(self):
        """
        Metóda na odoslanie uvítacieho e-mailu s prihlasovacími údajmi.
        """
        # Kontrola, či sú povinné údaje vyplnené
        if not self.email or not self.heslo:
            frappe.msgprint("Poradca nemá vyplnený email alebo heslo.")
            return

        # Predmet e-mailu
        subject = "Vaše prihlasovacie údaje"

        # Ošetrenie špeciálnych znakov v hesle pre HTML formát
        password_safe = html.escape(str(self.heslo) or "")

        login_url = frappe.utils.get_url("/login")
        # HTML správa - opravená na self.meno a self.email
        message = f"""
        Dobrý deň {self.meno or 'používateľ'},<br><br>
        boli vám vytvorené prihlasovacie údaje do systému.<br><br>
        📧 <b>Email:</b> {self.email}<br>
        🔐 <b>Heslo:</b> <code>{password_safe}</code><br><br>
        Prosím, <a href="{login_url}">prihláste sa do systému</a> a heslo si v prípade potreby zmeňte.<br><br>
        S pozdravom,<br>
        Váš tím
        """

        # Odoslanie e-mailu
        frappe.sendmail(
            recipients=self.email,
            subject=subject,
            message=message,
            now=True
        )

        # Informačná hláška pre používateľa vo Frappe
        frappe.msgprint(f"E-mail s údajmi bol úspešne odoslaný na adresu: {self.email}")