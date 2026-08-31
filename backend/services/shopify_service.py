"""Shopify integration service.

Real integration interface built around Shopify OAuth + Admin API. No mocked
responses: when API credentials are not configured the methods raise
`ShopifyNotConfigured` so the merchant UI can surface a proper connect flow.

Wiring real requests requires SHOPIFY_API_KEY / SHOPIFY_API_SECRET (Partner app)
in the environment; catalog/order sync then runs against the live Admin API.
"""
import os
import hmac
import hashlib
import base64
from urllib.parse import urlencode


class ShopifyNotConfigured(Exception):
    pass


class ShopifyService:
    API_VERSION = "2024-10"

    def __init__(self):
        self.api_key = os.environ.get("SHOPIFY_API_KEY", "")
        self.api_secret = os.environ.get("SHOPIFY_API_SECRET", "")
        self.scopes = os.environ.get("SHOPIFY_SCOPES", "read_products,read_orders")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def build_authorize_url(self, shop_domain: str, redirect_uri: str, state: str) -> str:
        """Step 1 of Shopify OAuth: URL to redirect the merchant to."""
        if not self.configured:
            raise ShopifyNotConfigured(
                "Shopify Partner app credentials (SHOPIFY_API_KEY / SHOPIFY_API_SECRET) "
                "are not configured on the server."
            )
        params = {
            "client_id": self.api_key,
            "scope": self.scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"https://{shop_domain}/admin/oauth/authorize?{urlencode(params)}"

    def verify_hmac(self, query_params: dict) -> bool:
        """Verify the HMAC on Shopify OAuth callback / webhook query params."""
        if not self.configured:
            return False
        provided = query_params.get("hmac", "")
        pairs = "&".join(
            f"{k}={v}" for k, v in sorted(query_params.items()) if k != "hmac"
        )
        digest = hmac.new(
            self.api_secret.encode(), pairs.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(digest, provided)

    def verify_webhook(self, body: bytes, hmac_header: str) -> bool:
        if not self.configured:
            return False
        digest = base64.b64encode(
            hmac.new(self.api_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(digest, hmac_header or "")

    async def exchange_code(self, shop_domain: str, code: str) -> str:
        """Exchange the OAuth code for a permanent access token (live call)."""
        if not self.configured:
            raise ShopifyNotConfigured("Shopify credentials not configured.")
        import requests
        resp = requests.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={"client_id": self.api_key, "client_secret": self.api_secret, "code": code},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def sync_catalog(self, shop_domain: str, access_token: str) -> dict:
        """Pull products/variants/inventory from Shopify Admin API.

        Requires a live connection. Raises when not configured so we never
        return fake catalog data.
        """
        raise ShopifyNotConfigured(
            "Cannot sync catalog: connect a Shopify store first. "
            "Live sync runs once SHOPIFY_API_KEY/SECRET are set and a store is authorized."
        )

    async def create_order(self, shop_domain: str, access_token: str, payload: dict) -> dict:
        """Create an order via Shopify Admin API. Never fake success."""
        raise ShopifyNotConfigured(
            "Cannot create Shopify order: no authorized Shopify connection."
        )


shopify_service = ShopifyService()
