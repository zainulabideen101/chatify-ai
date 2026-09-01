"""AI WhatsApp Commerce Manager — FastAPI application entrypoint.

Multi-tenant SaaS backend. Loads env first, wires modular routers, configures
CORS, creates indexes, and seeds the owner account + demo tenant on startup.
"""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import logging
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from database import client
from routers import auth, merchant, integrations, commerce, inbox, analytics, ai, cron, billing
import seed
from services.billing import ensure_all_subscriptions

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("app")

app = FastAPI(title="AI WhatsApp Commerce Manager")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ai-whatsapp-commerce"}


app.include_router(auth.router)
app.include_router(merchant.router)
app.include_router(integrations.router)
app.include_router(integrations.webhook_router)
app.include_router(commerce.router)
app.include_router(inbox.router)
app.include_router(analytics.router)
app.include_router(ai.router)
app.include_router(cron.router)
app.include_router(billing.router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    try:
        await seed.ensure_indexes()
        await seed.seed_owner_and_demo()
        await ensure_all_subscriptions()
        await seed.write_test_credentials()
        logger.info("Startup: indexes + demo seed complete")
    except Exception as e:
        logger.exception("Startup seeding failed: %s", e)


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
