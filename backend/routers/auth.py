"""Authentication + tenant registration routes."""
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Response, Request, HTTPException, Depends

from database import db
from security import (hash_password, verify_password, create_access_token,
                     create_refresh_token, decode_token, ACCESS_TTL_MIN, REFRESH_TTL_DAYS)
from models import RegisterRequest, LoginRequest, DefaultAISettings
from deps import get_current_user
from audit import log_action
from services.billing import ensure_subscription
from services.recovery import ensure_recovery_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15


def _set_auth_cookies(response: Response, access: str, refresh: str):
    response.set_cookie("access_token", access, httponly=True, secure=True,
                        samesite="none", max_age=ACCESS_TTL_MIN * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True,
                        samesite="none", max_age=REFRESH_TTL_DAYS * 86400, path="/")


async def _issue(response: Response, user: dict) -> dict:
    access = create_access_token(str(user["_id"]), user["email"], str(user["merchant_id"]))
    refresh = create_refresh_token(str(user["_id"]))
    _set_auth_cookies(response, access, refresh)
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "merchant_id": str(user["merchant_id"]),
        "access_token": access,
    }


@router.post("/register")
async def register(payload: RegisterRequest, response: Response):
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    now = datetime.now(timezone.utc).isoformat()
    merchant = {
        "store_name": payload.store_name,
        "currency": "PKR",
        "country": "PK",
        "timezone": "Asia/Karachi",
        "contact_phone": None,
        "created_at": now,
    }
    merchant_id = (await db.merchants.insert_one(merchant)).inserted_id

    user = {
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "role": "owner",
        "merchant_id": merchant_id,
        "created_at": now,
    }
    res = await db.users.insert_one(user)
    user["_id"] = res.inserted_id
    user["merchant_id"] = merchant_id

    await db.ai_settings.insert_one({"merchant_id": merchant_id, **DefaultAISettings.make()})
    await ensure_subscription(merchant_id)
    await ensure_recovery_settings(merchant_id)
    await log_action(str(merchant_id), "merchant_registered", actor="merchant",
                     detail={"store_name": payload.store_name})
    return await _issue(response, user)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    identifier = f"login:{email}"

    attempt = await db.login_attempts.find_one({"identifier": identifier})
    if attempt and attempt.get("count", 0) >= MAX_ATTEMPTS:
        locked_at = attempt.get("locked_at")
        if locked_at:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(locked_at)).total_seconds()
            if elapsed < LOCK_MINUTES * 60:
                raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        await db.login_attempts.update_one(
            {"identifier": identifier},
            {"$inc": {"count": 1},
             "$set": {"locked_at": datetime.now(timezone.utc).isoformat(),
                      "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await db.login_attempts.delete_one({"identifier": identifier})
    return await _issue(response, user)


@router.post("/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    merchant = await db.merchants.find_one({"_id": ObjectId(user["merchant_id"])})
    return {
        **user,
        "store_name": merchant.get("store_name") if merchant else None,
        "currency": merchant.get("currency", "PKR") if merchant else "PKR",
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(str(user["_id"]), user["email"], str(user["merchant_id"]))
        response.set_cookie("access_token", access, httponly=True, secure=True,
                            samesite="none", max_age=ACCESS_TTL_MIN * 60, path="/")
        return {"access_token": access}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
