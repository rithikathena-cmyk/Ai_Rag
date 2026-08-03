import hashlib
import hmac
import secrets

from app.core.config import settings

_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = settings.password_hash_iterations
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_str, salt, hex_digest = encoded.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations_str))
    return hmac.compare_digest(candidate.hex(), hex_digest)
