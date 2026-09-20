"""Shopify integration service (per-merchant Client ID + Client Secret).

Each merchant pastes the Client ID and Client Secret of their own Shopify app
(Dev Dashboard → app Settings). Chatify AI exchanges them directly for an Admin
API access token via Shopify's client-credentials grant — no merchant redirect.
Tokens expire after ~24 hours and are refreshed automatically with the stored
(encrypted) credentials whenever an Admin API call is made.
"""
import os
import hmac
import hashlib
import base64
from datetime import datetime, timedelta, timezone


class ShopifyNotConfigured(Exception):
    pass


class ShopifyAPIError(Exception):
    pass


class ShopifyService:
    TOKEN_TTL_SECONDS = 86399  # Shopify client-credentials token lifetime (24h)
    REFRESH_MARGIN_MINUTES = 5

    def __init__(self):
        self.API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")

    async def exchange_client_credentials(self, shop_domain: str, client_id: str, client_secret: str) -> dict:
        """Exchange client credentials for a 24h Admin API access token (no redirect)."""
        import requests
        try:
            resp = requests.post(
                f"https://{shop_domain}/admin/oauth/access_token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ShopifyAPIError(f"Could not reach Shopify: {exc}") from exc
        body = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        if resp.status_code >= 400:
            detail = body.get("error") or body.get("error_description") or f"HTTP {resp.status_code}"
            if "shop_not_permitted" in str(detail):
                detail = ("Shopify says this shop is not permitted for these credentials — the store and the "
                          "app must belong to the same organization in the Shopify Dev Dashboard.")
            raise ShopifyAPIError(f"Shopify rejected the client credentials: {detail}")
        token = body.get("access_token")
        if not token:
            raise ShopifyAPIError("Shopify did not return an access token.")
        return {"access_token": token, "expires_in": int(body.get("expires_in") or self.TOKEN_TTL_SECONDS)}

    async def ensure_access_token(self, connection: dict) -> str:
        """Return a valid plain access token, refreshing via client credentials if expired."""
        from database import db
        from services.crypto import decrypt_secret, encrypt_secret

        token_enc = connection.get("access_token_enc")
        if token_enc:
            expires_at = connection.get("token_expires_at")
            if not expires_at:
                return decrypt_secret(token_enc)  # legacy long-lived token, no refresh needed
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < exp - timedelta(minutes=self.REFRESH_MARGIN_MINUTES):
                    return decrypt_secret(token_enc)
            except ValueError:
                pass

        client_id_enc = connection.get("client_id_enc")
        client_secret_enc = connection.get("client_secret_enc")
        if not client_id_enc or not client_secret_enc:
            if token_enc:
                return decrypt_secret(token_enc)
            raise ShopifyNotConfigured("Connect a Shopify store first.")
        creds = await self.exchange_client_credentials(
            connection["shop_domain"], decrypt_secret(client_id_enc), decrypt_secret(client_secret_enc))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=creds["expires_in"])).isoformat()
        await db.shopify_connections.update_one(
            {"_id": connection["_id"], "merchant_id": connection["merchant_id"]},
            {"$set": {"access_token_enc": encrypt_secret(creds["access_token"]),
                      "token_expires_at": expires_at, "token_refreshed_at": iso_now_str()}},
        )
        return creds["access_token"]

    async def verify_admin_token(self, shop_domain: str, access_token: str) -> dict:
        """Call /shop.json to confirm the token is valid and fetch shop metadata."""
        import requests
        try:
            resp = requests.get(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/shop.json",
                headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ShopifyAPIError(f"Could not reach Shopify: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ShopifyAPIError("Shopify rejected the access token. Verify the app's access scopes.")
        if resp.status_code == 404:
            raise ShopifyAPIError("Shop not found. Double-check the .myshopify.com domain.")
        if resp.status_code >= 400:
            raise ShopifyAPIError(f"Shopify returned HTTP {resp.status_code}.")
        shop = resp.json().get("shop") or {}
        return {"name": shop.get("name"), "email": shop.get("email"), "domain": shop.get("myshopify_domain") or shop_domain}

    def verify_webhook(self, body: bytes, hmac_header: str, webhook_secret: str | None = None) -> bool:
        """Verify Shopify webhook HMAC using the merchant's per-connection webhook secret."""
        if not webhook_secret:
            return False
        digest = base64.b64encode(
            hmac.new(webhook_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(digest, hmac_header or "")

    async def sync_catalog(self, shop_domain: str, access_token: str, merchant_id: str) -> dict:
        """Pull products/variants from Shopify Admin API into the tenant catalog."""
        if not access_token:
            raise ShopifyNotConfigured("Connect a Shopify store first.")
        import requests
        from bson import ObjectId
        from database import db

        headers = {"X-Shopify-Access-Token": access_token}
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
                              "image": image, "updated_at": iso_now_str()}},
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
            link = resp.headers.get("Link", "")
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part[part.find("<") + 1:part.find(">")]
            url = nxt

        await db.shopify_connections.update_one(
            {"merchant_id": oid},
            {"$set": {"last_sync": iso_now_str(), "sync_errors": errors}})
        return {"synced": True, "products": products, "variants": variants, "errors": errors}

    async def sync_orders(self, shop_domain: str, access_token: str, merchant_id: str) -> dict:
        """Pull all orders (any status) from the Shopify Admin API into the tenant.

        Existing local orders matched by shopify_order_id get their sync-safe
        fields updated (total, items, payment/fulfillment status); local-only
        verification fields are never touched. New orders create/link customers
        by phone when available.
        """
        if not access_token:
            raise ShopifyNotConfigured("Connect a Shopify store first.")
        import requests
        from bson import ObjectId
        from database import db

        headers = {"X-Shopify-Access-Token": access_token}
        url = f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders.json?status=any&limit=250"
        oid = ObjectId(merchant_id)
        now = iso_now_str()
        created = updated = 0
        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code in (401, 403):
                raise ShopifyAPIError("Shopify rejected the token or order-read permission. Add read_orders scope.")
            if resp.status_code >= 400:
                raise ShopifyAPIError(f"Shopify order sync failed (HTTP {resp.status_code}).")
            for o in resp.json().get("orders", []):
                shopify_id = str(o["id"])
                financial = o.get("financial_status")
                fulfillment = o.get("fulfillment_status")
                shipping = o.get("shipping_address") or {}
                customer_data = o.get("customer") or {}
                customer_name = (
                    shipping.get("name")
                    or " ".join(filter(None, [customer_data.get("first_name"), customer_data.get("last_name")])).strip()
                    or None
                )
                shipping_address = {
                    "name": shipping.get("name"),
                    "address1": shipping.get("address1"),
                    "address2": shipping.get("address2"),
                    "city": shipping.get("city"),
                    "province": shipping.get("province"),
                    "zip": shipping.get("zip"),
                    "country": shipping.get("country"),
                    "phone": shipping.get("phone") or customer_data.get("phone"),
                }
                address_text = ", ".join(filter(None, [
                    shipping.get("address1"), shipping.get("address2"),
                    shipping.get("city"), shipping.get("province"),
                    shipping.get("zip"), shipping.get("country"),
                ])) or None
                updates = {
                    "shopify_order_id": shopify_id, "shopify_synced_at": now,
                    "order_number": o.get("order_number") or o.get("name") or shopify_id,
                    "total": float(o.get("total_price") or 0),
                    "payment_status": "paid" if financial == "paid" else "unpaid",
                    "fulfillment_status": fulfillment or "pending",
                    "customer_name": customer_name,
                    "shipping_address": shipping_address,
                    "delivery_address": address_text,
                    "order_date": o.get("created_at") or now,
                    "items": [
                        {"title": item.get("title") or item.get("name"), "variant": item.get("variant_title"),
                         "qty": item.get("quantity", 1), "price": float(item.get("price") or 0),
                         "sku": item.get("sku"), "variant_id": item.get("variant_id")}
                        for item in o.get("line_items", [])
                    ],
                }
                if o.get("cancelled_at"):
                    updates.update({"fulfillment_status": "cancelled",
                                    "cancellation_status": "cancelled_on_shopify",
                                    "cancelled_at": o["cancelled_at"]})
                local = await db.shopify_orders.find_one({"merchant_id": oid, "shopify_order_id": shopify_id})
                if local:
                    await db.shopify_orders.update_one(
                        {"_id": local["_id"], "merchant_id": oid}, {"$set": updates})
                    updated += 1
                    continue
                phone = shipping_address.get("phone") or ""
                customer = await db.customers.find_one({"merchant_id": oid, "phone": phone}) if phone else None
                if not customer:
                    inserted = await db.customers.insert_one({
                        "merchant_id": oid,
                        "name": customer_name or phone or "Shopify customer",
                        "phone": phone, "city": shipping.get("city"),
                        "preferred_language": None, "total_orders": 0,
                        "delivered_orders": 0, "cancelled_orders": 0, "refused_orders": 0,
                        "failed_deliveries": 0, "created_at": now,
                    })
                    customer = {"_id": inserted.inserted_id}
                updates.update({
                    "merchant_id": oid, "customer_id": customer["_id"],
                    "payment_method": "SHOPIFY", "attribution": "SHOPIFY",
                    "created_at": o.get("created_at") or now,
                })
                await db.shopify_orders.insert_one(updates)
                await db.customers.update_one(
                    {"_id": customer["_id"], "merchant_id": oid}, {"$inc": {"total_orders": 1}})
                created += 1
            link = resp.headers.get("Link", "")
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part[part.find("<") + 1:part.find(">")]
            url = nxt

        await db.shopify_connections.update_one(
            {"merchant_id": oid}, {"$set": {"last_order_sync": now}})
        return {"synced": True, "orders_created": created, "orders_updated": updated}

    async def create_order(self, shop_domain: str, access_token: str, payload: dict) -> dict:
        """Create an order via Shopify Admin API."""
        import requests
        try:
            resp = requests.post(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders.json",
                headers={"X-Shopify-Access-Token": access_token},
                json={"order": payload}, timeout=30)
        except requests.RequestException as exc:
            raise ShopifyAPIError("Shopify order creation could not be reached.") from exc
        if resp.status_code in (401, 403):
            raise ShopifyAPIError("Shopify rejected the store access token or order permission.")
        if resp.status_code == 429:
            raise ShopifyAPIError("Shopify rate-limited order creation. Please retry shortly.")
        if resp.status_code >= 400:
            raise ShopifyAPIError("Shopify rejected the order payload.")
        return resp.json()["order"]

    async def cancel_order(self, shop_domain: str, access_token: str, shopify_order_id: str) -> dict:
        """Cancel an open Shopify order through the official Admin API."""
        import requests
        try:
            resp = requests.post(
                f"https://{shop_domain}/admin/api/{self.API_VERSION}/orders/{shopify_order_id}/cancel.json",
                headers={"X-Shopify-Access-Token": access_token},
                json={"reason": "customer", "email": True}, timeout=30)
        except requests.RequestException as exc:
            raise ShopifyAPIError("Shopify cancellation could not be reached.") from exc
        if resp.status_code in (401, 403):
            raise ShopifyAPIError("Shopify rejected the store access token or order cancellation permission.")
        if resp.status_code == 429:
            raise ShopifyAPIError("Shopify rate-limited cancellation. Please retry shortly.")
        if resp.status_code >= 400:
            raise ShopifyAPIError("Shopify rejected the cancellation because the order may already be closed or cancelled.")
        return resp.json().get("order", {})

    async def update_payment_review(self, shop_domain: str, access_token: str,
                                    shopify_order_id: str, approved: bool,
                                    amount: float, note: str) -> dict:
        """Reflect a seller's payment review decision in Shopify."""
        import requests
        headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
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


def iso_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


shopify_service = ShopifyService()
