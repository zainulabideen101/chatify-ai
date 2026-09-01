"""WhatsApp Business Platform (Meta) integration service.

Real interface around the Meta Graph API / Cloud API. No mocked sends: when a
merchant has not connected a WhatsApp Business Account the methods raise
`WhatsAppNotConfigured`. Incoming messages arrive via verified webhooks.
"""
import os
import hmac
import hashlib

import httpx

from services.crypto import decrypt_secret

class WhatsAppNotConfigured(Exception):
    pass


class WhatsAppService:

    def __init__(self):
        self.app_id = os.environ.get("META_APP_ID", "")
        self.app_secret = os.environ.get("META_APP_SECRET", "")
        self.verify_token = os.environ.get("META_VERIFY_TOKEN") or os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
        self.graph_version = os.environ.get("META_GRAPH_VERSION", "v25.0")

    @property
    def GRAPH_VERSION(self):
        return self.graph_version

    @property
    def app_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def verify_webhook_subscription(self, mode: str, token: str, challenge: str):
        """Meta webhook verification handshake (GET)."""
        if mode == "subscribe" and token and token == self.verify_token:
            return challenge
        return None

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """Verify X-Hub-Signature-256 on incoming webhook payloads."""
        if not self.app_secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(
            self.app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    async def send_text(self, connection: dict, to: str, body: str) -> dict:
        if not connection:
            raise WhatsAppNotConfigured("No WhatsApp Business Account connected for this merchant.")
        return await self._graph_send(connection, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        })

    async def send_media(self, connection: dict, to: str, media_type: str, link: str, caption: str = None) -> dict:
        if not connection:
            raise WhatsAppNotConfigured("No WhatsApp Business Account connected for this merchant.")
        payload = {"messaging_product": "whatsapp", "to": to, "type": media_type,
                   media_type: {"link": link}}
        if caption and media_type in ("image", "video"):
            payload[media_type]["caption"] = caption
        return await self._graph_send(connection, payload)

    async def download_media(self, connection: dict, media_id: str) -> tuple[bytes, str]:
        """Download an inbound WhatsApp image with the tenant's encrypted token."""
        if not connection or not connection.get("access_token_enc"):
            raise WhatsAppNotConfigured("No WhatsApp Business Account connected for this merchant.")
        token = decrypt_secret(connection["access_token_enc"])
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            metadata_response = await client.get(
                f"https://graph.facebook.com/{self.GRAPH_VERSION}/{media_id}", headers=headers)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            media_url = metadata.get("url")
            mime_type = metadata.get("mime_type", "")
            if not media_url:
                raise ValueError("Meta did not return a media download URL")
            async with client.stream("GET", media_url, headers=headers) as response:
                response.raise_for_status()
                chunks = []
                total = 0
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > 10 * 1024 * 1024:
                        raise ValueError("Payment screenshot exceeds the 10 MB limit")
                    chunks.append(chunk)
        return b"".join(chunks), mime_type

    async def send_template(self, connection: dict, to: str, template_name: str,
                            language: str, components: list = None) -> dict:
        if not connection:
            raise WhatsAppNotConfigured("No WhatsApp Business Account connected for this merchant.")
        return await self._graph_send(connection, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language},
                         "components": components or []},
        })

    async def _graph_send(self, connection: dict, payload: dict) -> dict:
        """Live POST to the WhatsApp Cloud API. Requires a decrypted access token."""
        import requests
        from services.crypto import decrypt_secret
        phone_number_id = connection["phone_number_id"]
        token = decrypt_secret(connection["access_token_enc"])
        params = {}
        if self.app_secret:
            params["appsecret_proof"] = hmac.new(
                self.app_secret.encode(), token.encode(), hashlib.sha256
            ).hexdigest()
        resp = requests.post(
            f"https://graph.facebook.com/{self.GRAPH_VERSION}/{phone_number_id}/messages",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()


whatsapp_service = WhatsAppService()
