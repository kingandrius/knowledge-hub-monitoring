"""Generate a new API key and the SHA-256 hash the API stores.

The key goes to the monitoring script's .env (KH_API_KEY); only the hash goes
to the API's settings (KH_API_KEY_HASHES). Store the key in the password manager.
"""
import hashlib
import secrets

if __name__ == "__main__":
    key = secrets.token_urlsafe(32)
    print(f"API key (client .env, password manager): {key}")
    print(f"SHA-256 (API setting KH_API_KEY_HASHES): {hashlib.sha256(key.encode()).hexdigest()}")
