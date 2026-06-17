import os
from fastapi import Header, HTTPException

_firebase_initialized = False


def _ensure_firebase():
    global _firebase_initialized
    if _firebase_initialized:
        return
    import firebase_admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    _firebase_initialized = True


async def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    id_token = authorization.split("Bearer ", 1)[1].strip()
    try:
        _ensure_firebase()
        from firebase_admin import auth
        decoded = auth.verify_id_token(id_token)
        return decoded
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_token")
