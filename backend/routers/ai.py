"""AI test playground: chat with the GPT-5.4 agent against the merchant's own catalog."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from database import db, serialize_list
from deps import get_tenant_id
from models import iso_now
from services.ai_agent import ai_agent, LLMNotConfigured

router = APIRouter(prefix="/api/ai/playground", tags=["ai-playground"])


@router.get("/{session_id}")
async def get_session(session_id: str, tid: str = Depends(get_tenant_id)):
    msgs = await db.playground_messages.find(
        {"merchant_id": ObjectId(tid), "session_id": session_id}).sort("created_at", 1).to_list(200)
    return {"session_id": session_id, "messages": serialize_list(msgs), "llm_connected": ai_agent.llm_connected}


@router.post("/{session_id}/reset")
async def reset_session(session_id: str, tid: str = Depends(get_tenant_id)):
    await db.playground_messages.delete_many({"merchant_id": ObjectId(tid), "session_id": session_id})
    return {"ok": True}


@router.post("/{session_id}")
async def send(session_id: str, payload: dict, tid: str = Depends(get_tenant_id)):
    text = (payload.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "message required")
    oid = ObjectId(tid)

    prior = await db.playground_messages.find(
        {"merchant_id": oid, "session_id": session_id}).sort("created_at", 1).to_list(200)
    history = [{"role": m["role"], "content": m["content"]} for m in prior]

    await db.playground_messages.insert_one(
        {"merchant_id": oid, "session_id": session_id, "role": "user", "content": text, "created_at": iso_now()})

    try:
        result = await ai_agent.run_agent(tid, text, history=history, conversation_id=None)
    except LLMNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI error: {e}")

    await db.playground_messages.insert_one(
        {"merchant_id": oid, "session_id": session_id, "role": "assistant",
         "content": result["reply"], "tool_trace": result["tool_trace"], "created_at": iso_now()})

    return {"reply": result["reply"], "tool_trace": result["tool_trace"]}
