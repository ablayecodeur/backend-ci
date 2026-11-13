"""Sécurité : hachage des mots de passe (scrypt, bibliothèque standard) et jetons JWT."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

# Clé de signature des jetons. En production : définir SECRET_KEY (variable d'environnement).
# Sans SECRET_KEY, une clé aléatoire est générée au démarrage (les jetons expirent à chaque redémarrage).
_environment = os.getenv("APP_ENV", "development").strip().lower()
_configured_secret = os.getenv("SECRET_KEY")
if _environment in {"prod", "production"} and (
    not _configured_secret
    or len(_configured_secret) < 32
    or _configured_secret.upper().startswith("CHANGE_ME")
):
    raise RuntimeError("SECRET_KEY doit être une clé forte et non placeholder en production.")
SECRET_KEY = _configured_secret or secrets.token_urlsafe(48)
ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))  # 8 heures
if ACCESS_TOKEN_MINUTES < 1:
    raise RuntimeError("ACCESS_TOKEN_MINUTES doit être supérieur ou égal à 1.")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


# Faux hachage utilisé quand l'email n'existe pas, pour que le temps de réponse ne révèle pas si le compte existe.
DUMMY_HASH = hash_password("dummy-password-not-used")


def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    """Renvoie l'id de l'utilisateur si le jeton est valide et non expiré, sinon None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
