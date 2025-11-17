import requests, jwt, json

JWKS_URL = "https://api.clerk.com/v1/jwks"

def verify_clerk_jwt(auth_header: str) -> str:
    token = (auth_header or "").replace("Bearer ", "").strip()
    if not token:
        raise ValueError("Missing Bearer token")
    jwks = requests.get(JWKS_URL, timeout=10).json()
    header = jwt.get_unverified_header(token)
    key = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
    payload = jwt.decode(token, key=public_key, algorithms=["RS256"], options={"verify_aud": False})
    return payload["sub"]  # Clerk user id
