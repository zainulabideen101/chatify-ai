"""FastAPI dependencies: authenticated user + tenant scoping."""
from bson import ObjectId
from fastapi import Request, HTTPException
import jwt

from database import db, serialize
from security import decode_token


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user = serialize(user)
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_tenant_id(request: Request) -> str:
    """Return the merchant_id (tenant) for the authenticated user."""
    user = await get_current_user(request)
    merchant_id = user.get("merchant_id")
    if not merchant_id:
        raise HTTPException(status_code=403, detail="No merchant associated with user")
    return merchant_id
