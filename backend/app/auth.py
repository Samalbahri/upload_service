import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer_scheme = HTTPBearer(auto_error=False)
_TOKEN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str

# reads the token hash from the environment variable and returns a dictionary of user_id to token_hash. Raises HTTPException if the configuration is missing or invalid.
# return the authneticatedUser when match is found.
def _configured_token_hashes() -> dict[str, str]:
    raw = os.getenv("UPLOAD_API_TOKEN_HASHES")
    if not raw:
        raise HTTPException(503, detail="Authentication is not configured")

    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(503, detail="Authentication is misconfigured") from exc

    if not isinstance(configured, dict) or not configured:
        raise HTTPException(503, detail="Authentication is misconfigured")

    if any(
        not isinstance(user_id, str)
        or not user_id
        or len(user_id) > 100
        or not isinstance(token_hash, str)
        or not _TOKEN_HASH_RE.fullmatch(token_hash)
        for user_id, token_hash in configured.items()
    ):
        raise HTTPException(503, detail="Authentication is misconfigured")

    return configured


# _configured_token_hashes() read server authenticate cinfiguratoin and put it in a expected format for get_current_user
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    candidate_hash = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
    for user_id, configured_hash in _configured_token_hashes().items():
        if hmac.compare_digest(candidate_hash, configured_hash):
            return AuthenticatedUser(user_id=user_id)

    raise HTTPException(
        401,
        detail="Invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )