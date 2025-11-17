import time, jwt, httpx, frappe

def _token():
    team_id = frappe.conf.apns_team_id
    key_id = frappe.conf.apns_key_id
    key_path = frappe.conf.apns_auth_key_path
    private_key = open(key_path, "rb").read()
    return jwt.encode({"iss": team_id, "iat": int(time.time())}, private_key, algorithm="ES256", headers={"kid": key_id})

def send_voip(voip_token: str, payload: dict):
    jwt_token = _token()
    headers = {"authorization": f"bearer {jwt_token}", "apns-topic": frappe.conf.apns_bundle_id, "apns-push-type": "voip"}
    body = {"aps": {"content-available": 1}, **payload}
    url = f"https://api.push.apple.com/3/device/{voip_token}"
    with httpx.Client(http2=True, timeout=10) as client:
        res = client.post(url, headers=headers, json=body)
        frappe.logger().info(f"[APNs] {res.status_code} {res.text}")
