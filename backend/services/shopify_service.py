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


class ShopifyAPIError(Exception):
    pass


class ShopifyService:
    API_VERSION = "2024-10"

    def __init__(self):
        self.api_key = os.environ.get("SHOPIFY_API_KEY", "")
        self.api_secret = os.environ.get("SHOPIFY_API_SECRET", "")
        self.API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
        self.scopes = os.environ.get("SHOPIFY_SCOPES", "read_products,read_orders,write_orders,read_customers")

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

    async def sync_catalog(self, shop_domain: str, access_token_enc: str, merchant_id: str) -> dict:
        """Pull products/variants from Shopify Admin API into the tenant catalog.

        Requires a live connection. Raises when not configured so we never
        return fake catalog data.
        """
        if not self.configured or not access_token_enc:
            raise ShopifyNotConfigured(
                "Cannot sync catalog: connect a Shopify store first. "
                "Live sync runs once SHOPIFY_API_KEY/SECRET are set and a store is authorized."
            )
        import requests
        from bson import ObjectId
        from datetime import datetime, timezone
        from database import db
        from services.crypto import decrypt_secret

        token = decrypt_secret(access_token_enc)
        headers = {"X-Shopify-Access-Token": token}
        url = f"https://{shop_domain}/admin/api/{self.API_VERSION}/products.json?limit=250"
        errors = 0
        products = variants = 0
        oid = ObjectId(merchant_id)
        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            for p in resp.json().get("products", []):
                image = (p.get("image") or {}).get("src") or (p.get("images", [{}])[0].get("src") if p.get("images") else None)
                await db.products.update_one(
                    {"merchant_id": oid, "shopify_product_id": p["id"]},
                    {"$set": {"merchant_id": oid, "shopify_product_id": p["id"],
                              "title": p["title"], "status": p.get("status", "active"),
                              "price": float(p["variants"][0]["price"]) if p.get("variants") else 0,
                              "image": image, "updated_at": datetime.now(timezone.utc).isoformat()}},
                    upsert=True)
                pdoc = await db.products.find_one({"merchant_id": oid, "shopify_product_id": p["id"]})
                products += 1
                for v in p.get("variants", []):
                    inv = v.get("inventory_quantity", 0) or 0
                    await db.product_variants.update_one(
                        {"merchant_id": oid, "shopify_variant_id": v["id"]},
                        {"$set": {"merchant_id": oid, "product_id": pdoc["_id"],
                                  "shopify_variant_id": v["id"], "size": v.get("option1"),
                                  "color": v.get("option2"), "price": float(v["price"]),
                                  "inventory": inv, "sku": v.get("sku")}},
                        upsert=True)
                    variants += 1
            # pagination via Link header
            link = resp.headers.get("Link", "")
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part[part.find("<") + 1:part.find(">")]
            url = nxt

        await db.shopify_connections.update_one(
            {"merchant_id": oid},
            {"$set": {"last_sync": datetime.now(timezone.utc).isoformat(), "sync_errors": errors}})
        return {"synced": True, "products": products, "variants": variants, "errors": errors}

    async def create_order(self, shop_domain: str, access_token_enc: str, payload: dict) -> dict:
        """Create an order via Shopify Admin API. Never fake success."""
        if not self.configured or not access_token_enc:
            raise ShopifyNotConfigured("Cannot create Shopify order: no authorized Shopify connection.")
        import requests
        from services.crypto import decrypt_secret
        token = decrypt_secret(access_token_enc)
        try:
            resp = requests.post(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders.json",
                headers={"X-Shopify-Access-Token": token},
                json={"order": payload}, timeout=30)
        except requests.RequestException as exc:
            raise ShopifyAPIError("Shopify order creation could not be reached.") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise ShopifyAPIError("Shopify rejected the store access token or order permission.")
        if resp.status_code == 429:
            raise ShopifyAPIError("Shopify rate-limited order creation. Please retry shortly.")
        if resp.status_code >= 400:
            raise ShopifyAPIError("Shopify rejected the order payload.")
        return resp.json()["order"]

    async def cancel_order(self, shop_domain: str, access_token_enc: str, shopify_order_id: str) -> dict:
        """Cancel an open Shopify order through the official Admin API."""
        if not self.configured or not access_token_enc:
            raise ShopifyNotConfigured("Cannot cancel Shopify order: no authorized Shopify connection.")
        import requests
        from services.crypto import decrypt_secret
        token = decrypt_secret(access_token_enc)
        try:
            resp = requests.post(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders/{shopify_order_id}/cancel.json",
                headers={"X-Shopify-Access-Token": token},
                json={"reason": "customer", "email": True}, timeout=30)
        except requests.RequestException as exc:
            raise ShopifyAPIError("Shopify cancellation could not be reached.") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise ShopifyAPIError("Shopify rejected the store access token or order cancellation permission.")
        if resp.status_code == 429:
            raise ShopifyAPIError("Shopify rate-limited cancellation. Please retry shortly.")
        if resp.status_code >= 400:
            raise ShopifyAPIError("Shopify rejected the cancellation because the order may already be closed or cancelled.")
        return resp.json().get("order", {})



    async def update_payment_review(self, shop_domain: str, access_token_enc: str,
                                    shopify_order_id: str, approved: bool,
                                    amount: float, note: str) -> dict:
        """Reflect a seller's payment review in Shopify.

        Approval records a successful manual sale transaction. Rejection adds a
        seller note and tag so the Shopify order history reflects the decision.
        """
        if not self.configured or not access_token_enc:
            raise ShopifyNotConfigured("Cannot update Shopify payment review without an authorized store.")
        import requests
        from services.crypto import decrypt_secret
        token = decrypt_secret(access_token_enc)
        headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        if approved:
            resp = requests.post(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders/{shopify_order_id}/transactions.json",
                headers=headers,
                json={"transaction": {"kind": "sale", "status": "success", "amount": f"{amount:.2f}", "gateway": "manual"}},
                timeout=30,
            )
        else:
            resp = requests.put(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders/{shopify_order_id}.json",
                headers=headers,
                json={"order": {"id": int(shopify_order_id), "note": note, "tags": "chatify-ai,payment-rejected"}},
                timeout=30,
            )
        if resp.status_code in (401, 403):
            raise ShopifyAPIError("Shopify rejected the store access token or payment permission.")
        if resp.status_code == 429:
            raise ShopifyAPIError("Shopify rate-limited the payment update. Please retry shortly.")
        if resp.status_code >= 400:
            raise ShopifyAPIError("Shopify rejected the payment review update.")
        return resp.json()


shopify_service = ShopifyService()
