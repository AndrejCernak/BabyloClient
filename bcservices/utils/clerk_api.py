import frappe, requests
BASE = "https://api.clerk.com/v1"

def _headers():
    return {
        "Authorization": f"Bearer {frappe.conf.clerk_secret_key}",
        "Content-Type": "application/json"
    }

def create_user(email: str, password: str, role: str):
    payload = {"email_address": [email], "password": password, "public_metadata": {"role": role}}
    r = requests.post(f"{BASE}/users", json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()
