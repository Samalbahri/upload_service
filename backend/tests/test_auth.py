import hashlib
import json

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import get_current_user


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_accepts_configured_token(monkeypatch):
    token = "a-long-random-test-token"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.setenv("UPLOAD_API_TOKEN_HASHES", json.dumps({"alice": token_hash}))

    user = get_current_user(_credentials(token))

    assert user.user_id == "alice"


@pytest.mark.parametrize("credentials", [None, _credentials("wrong-token")])
def test_get_current_user_rejects_missing_or_invalid_token(monkeypatch, credentials):
    token_hash = hashlib.sha256(b"valid-token").hexdigest()
    monkeypatch.setenv("UPLOAD_API_TOKEN_HASHES", json.dumps({"alice": token_hash}))

    with pytest.raises(HTTPException) as error:
        get_current_user(credentials)

    assert error.value.status_code == 401
    assert error.value.headers["WWW-Authenticate"] == "Bearer"


def test_get_current_user_fails_closed_when_auth_is_not_configured(monkeypatch):
    monkeypatch.delenv("UPLOAD_API_TOKEN_HASHES", raising=False)

    with pytest.raises(HTTPException) as error:
        get_current_user(_credentials("any-token"))

    assert error.value.status_code == 503