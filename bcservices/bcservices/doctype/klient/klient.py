import frappe
import html
from frappe.model.document import Document

class Klient(Document):
    def after_insert(self):
        self.send_welcome_email()

    @frappe.whitelist()
    def send_welcome_email(self):
        email = self.email
        if not email or not self.heslo:
            frappe.msgprint("Klient nemá vyplnený email alebo heslo.")
            return
        password_safe = html.escape(self.heslo or "")
        subject = "[BFG] Vaše prihlasovacie údaje"

        login_url = "https://apps.apple.com/sk/app/bfg-client/id6782660797?l=sk"

        message = f"""
        Dobrý deň {self.username or 'používateľ'},<br><br>
        boli vám vytvorené prihlasovacie údaje do aplikácie BFG Client Services.<br><br>
        📧 <b>Email:</b> {email}<br>
        🔐 <b>Heslo:</b> {password_safe}<br><br>
        Prosím, <a href="{login_url}">prihláste sa do aplikácie</a><br><br>
        S pozdravom,<br>
        Váš tím
        """
        frappe.sendmail(
            recipients=email,
            subject=subject,
            message=message,
            now=True
        )

    @frappe.whitelist()
    def send_password_reset_email(self):
        import secrets
        import string

        email = self.email
        if not email:
            frappe.msgprint("Klient nemá vyplnený email.")
            return

        # Vygenerujeme nové náhodné heslo a uložíme ho
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(16))
        self.db_set("heslo", new_password)

        password_safe = html.escape(new_password)
        subject = "Reset hesla"
        message = f"""
        Dobrý deň {self.username or 'používateľ'},<br><br>
        dostali sme žiadosť o reset vášho hesla.<br><br>
        Vaše nové heslo je:<br><br>
        🔐 <b>Heslo:</b> <code>{password_safe}</code><br><br>
        Prosím, prihláste sa s týmto heslom. Po prihlásení si ho v prípade potreby zmeňte.<br><br>
        Ak ste o reset hesla nežiadali, kontaktujte nás.<br><br>
        S pozdravom,<br>
        Váš tím
        """
        frappe.sendmail(
            recipients=email,
            subject=subject,
            message=message,
            now=True
        )
        frappe.msgprint(f"✅ Reset email bol odoslaný na {email}")
