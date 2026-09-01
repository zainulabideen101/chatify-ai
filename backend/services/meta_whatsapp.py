"""Official Meta Graph API helpers for WhatsApp Cloud API onboarding.

All app secrets and customer business tokens stay server-side. This service
fails closed when Meta app configuration is incomplete and never returns raw
Graph errors or tokens to the browser.
"""
import hashlib
import hmac
import os
from typing import Any

import httpx


class MetaGraphError(Exception):
    def __init__(self, status_code: int, category: str, message: str, graph_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.message = message
        self.graph_code = graph_code

    def public_detail(self) -> str:
        return f"{self.category}: {self.message}"


class MetaWhatsAppService:
    def __init__(self):
        self.app_id = os.environ.get("META_APP_ID", "").strip()
        self.app_secret = os.environ.get("META_APP_SECRET", "").strip()
        self.graph_version = os.environ.get("META_GRAPH_VERSION", "v25.0").strip()

    @property
    def app_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.app_secret and (os.environ.get("META_VERIFY_TOKEN") or os.environ.get("META_WEBHOOK_VERIFY_TOKEN")))

    @property
    def verify_token(self) -> str:
        return os.environ.get("META_VERIFY_TOKEN") or os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")

    def appsecret_proof(self, token: str) -> str | None:
        if not self.app_secret or not token:
            return None
        return hmac.new(self.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        if not self.app_secret or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header[7:])

    async def _request(self, method: str, path: str, token: str | None = None, *, params: dict | None = None, json: dict | None = None) -> dict:
        query = dict(params or {})
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            proof = self.appsecret_proof(token)
            if proof:
                query["appsecret_proof"] = proof
        url = f"https://graph.facebook.com/{self.graph_version}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(method, url, headers=headers, params=query, json=json)
        except httpx.HTTPError as exc:
            raise MetaGraphError(502, "meta_unavailable", "Meta Graph API could not be reached.") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_error:
            error = payload.get("error") or {}
            code = str(error.get("code")) if error.get("code") is not None else None
            if response.status_code == 401:
                category = "invalid_or_expired_token"
                message = "The Meta access token is invalid, expired, or revoked."
            elif response.status_code == 403:
                category = "insufficient_permissions"
                message = "The Meta token does not have the required WhatsApp permissions."
            elif response.status_code == 429:
                category = "meta_rate_limited"
                message = "Meta rate-limited this request. Please retry shortly."
            elif response.status_code == 400:
                category = "invalid_meta_configuration"
                message = "Meta rejected the WABA, phone number, token, or app configuration."
            else:
                category = "meta_api_error"
                message = "Meta Graph API returned an error."
            raise MetaGraphError(response.status_code, category, message, code)
        return payload

    async def exchange_code(self, code: str) -> str:
        if not self.app_configured:
            raise MetaGraphError(503, "meta_app_not_configured", "META_APP_ID and META_APP_SECRET are required for Embedded Signup.")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"https://graph.facebook.com/{self.graph_version}/oauth/access_token",
                    params={"client_id": self.app_id, "client_secret": self.app_secret, "code": code},
                )
        except httpx.HTTPError as exc:
            raise MetaGraphError(502, "meta_unavailable", "Meta authorization exchange could not be reached.") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_error or not payload.get("access_token"):
            raise MetaGraphError(400, "signup_exchange_failed", "Meta could not exchange the Embedded Signup authorization code.")
        return payload["access_token"]

    async def debug_token(self, token: str) -> dict:
        if not self.app_configured:
            return {}
        app_token = f"{self.app_id}|{self.app_secret}"
        return await self._request("GET", "/debug_token", params={"input_token": token, "access_token": app_token})

    async def validate_connection(self, access_token: str, waba_id: str | None, phone_number_id: str | None, *, subscribe: bool = False) -> dict:
        if not access_token or len(access_token) < 10:
            raise MetaGraphError(400, "invalid_access_token", "A valid Meta access token is required.")
        debug = await self.debug_token(access_token)
        debug_data = debug.get("data") or {}
        if debug_data and debug_data.get("is_valid") is False:
            raise MetaGraphError(401, "invalid_or_expired_token", "The Meta access token is invalid, expired, or revoked.")
        if debug_data and debug_data.get("app_id") and str(debug_data["app_id"]) != self.app_id:
            raise MetaGraphError(400, "invalid_meta_configuration", "The Meta token belongs to a different app.")
        scopes = set(debug_data.get("scopes") or [])
        granular = debug_data.get("granular_scopes") or []
        scopes.update(str(item.get("scope")) for item in granular if item.get("scope"))
        required = {"whatsapp_business_management", "whatsapp_business_messaging"}
        if debug_data and not required.issubset(scopes):
            missing = ", ".join(sorted(required - scopes))
            raise MetaGraphError(403, "insufficient_permissions", f"The Meta token is missing required permissions: {missing}.")
        if not waba_id:
            targets = {str(target) for item in granular for target in item.get("target_ids", [])}
            if len(targets) != 1:
                raise MetaGraphError(400, "waba_selection_required", "Meta did not identify exactly one WhatsApp Business Account. Select a WABA and try again.")
            waba_id = next(iter(targets))
        waba = await self._request("GET", waba_id, access_token, params={"fields": "id,name,account_review_status,business_verification_status"})
        phones_payload = await self._request("GET", f"{waba_id}/phone_numbers", access_token, params={"fields": "id,display_phone_number,verified_name,status,quality_rating"})
        phones = phones_payload.get("data") or []
        selected = next((phone for phone in phones if phone_number_id and str(phone.get("id")) == str(phone_number_id)), None)
        if phone_number_id and not selected:
            raise MetaGraphError(400, "invalid_phone_number", "The Phone Number ID is not attached to the selected WhatsApp Business Account.")
        if not selected:
            if len(phones) != 1:
                raise MetaGraphError(400, "phone_selection_required", "Meta returned multiple phone numbers. Select one phone number and try again.")
            selected = phones[0]
        if subscribe:
            await self._request("POST", f"{waba_id}/subscribed_apps", access_token, json={"messaging_product": "whatsapp"})
        return {
            "waba_id": str(waba.get("id") or waba_id),
            "waba_name": waba.get("name"),
            "phone_number_id": str(selected.get("id")),
            "display_phone_number": selected.get("display_phone_number"),
            "verified_name": selected.get("verified_name"),
            "phone_status": selected.get("status"),
            "quality_rating": selected.get("quality_rating"),
            "business_verification_status": waba.get("business_verification_status"),
            "account_review_status": waba.get("account_review_status"),
            "token_expires_at": debug_data.get("expires_at"),
        }


meta_whatsapp_service = MetaWhatsAppService()
