"""Generate a collector key for one agent, easy to type in a VM console.

Format kh-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx: 24 random characters from an
alphabet without look-alikes (no 0/o, 1/l/i), about 119 bits of entropy.
Prints "<key> <sha256>"; the collector stores only the hash.
"""
import hashlib
import secrets

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

if __name__ == "__main__":
    chars = "".join(secrets.choice(ALPHABET) for _ in range(24))
    key = "kh-" + "-".join(chars[i:i + 4] for i in range(0, 24, 4))
    print(key, hashlib.sha256(key.encode()).hexdigest())
