import os
from fastapi import Depends, Header, HTTPException

_firebase_initialized = False


def _ensure_firebase():
    global _firebase_initialized
    if _firebase_initialized:
        return
    import firebase_admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    _firebase_initialized = True


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    id_token = authorization.split("Bearer ", 1)[1].strip()
    try:
        _ensure_firebase()
        from firebase_admin import auth
        decoded = auth.verify_id_token(id_token, check_revoked=True)
        return decoded
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_token")


async def get_verified_user(user: dict = Depends(get_current_user)) -> dict:
    """Like get_current_user, but the sign-in provider must have verified the address."""
    email = user.get("email")
    if not user.get("email_verified") or not isinstance(email, str) or not email.strip():
        raise HTTPException(status_code=403, detail="email_not_verified")
    return user
