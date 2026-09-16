"""OpenAI chat adapter and tenant-scoped tool dispatch."""
import json
import logging
import os
import re
from bson import ObjectId

from database import db
from models import iso_now
from services.ai_tools import TOOL_SCHEMAS, dispatch

logger = logging.getLogger(__name__)


class LLMNotConfigured(Exception):
    pass


class ToolRegistry:
    def list(self) -> list[dict]:
        return [
            {"name": schema["function"]["name"], "implemented": True}
            for schema in TOOL_SCHEMAS
        ]


_SALES_REFUSAL_PATTERN = re.compile(
    r"\b(no thanks|not interested|no need|don't need|do not need|don't want|do not want|that's all|bas itna|bas itni|nahi chahiye|nahin chahiye|nahi lena|nahin lena)\b",
    re.IGNORECASE,
)


async def note_sales_refusal(merchant_id: str, conversation_id: str, customer_message: str) -> bool:
    if not _SALES_REFUSAL_PATTERN.search(customer_message or ""):
        return False
    oid = ObjectId(merchant_id)
    previous = await db.messages.find_one(
        {"conversation_id": ObjectId(conversation_id), "merchant_id": oid, "direction": "outbound"},
        sort=[("created_at", -1)],
    )
    if not previous:
        return False
    await db.conversations.update_one(
        {"_id": ObjectId(conversation_id), "merchant_id": oid},
        {"$set": {"sales_opt_out": True, "sales_opt_out_at": iso_now()}},
    )
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id), "merchant_id": oid})
    if conversation and conversation.get("customer_id"):
        await db.customers.update_one(
            {"_id": conversation["customer_id"], "merchant_id": oid},
            {"$set": {"sales_opt_out": True, "sales_opt_out_at": iso_now()}},
        )
    return True


class AIAgentService:
    def __init__(self):
        self.tools = ToolRegistry()

    @property
    def llm_connected(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    async def _build_system_prompt(self, merchant_id: str) -> str:
        return ""

    async def run_agent(
        self,
        merchant_id: str,
        user_message: str,
        history: list[dict] | None = None,
        conversation_id: str | None = None,
        state: dict | None = None,
        max_tool_rounds: int = 6,
    ) -> dict:
        if not self.llm_connected:
            raise LLMNotConfigured("OPENAI_API_KEY not set")

        from emergentintegrations.llm.chat import LlmChat, UserMessage

        import uuid
        session = f"{conversation_id or f'pg-{merchant_id}'}-{uuid.uuid4().hex[:8]}"
        chat = (
            LlmChat(
                api_key=os.environ["OPENAI_API_KEY"],
                session_id=session,
                system_message="",
            )
            .with_model("openai", "gpt-5.4")
            .with_tools(TOOL_SCHEMAS, tool_choice="auto")
        )

        ctx = {
            "merchant_id": merchant_id,
            "conversation_id": conversation_id,
            "latest_message": user_message,
            "state": state or {},
        }
        tool_trace = []
        response = await chat.send_message_with_tools(UserMessage(text=user_message))
        rounds = 0
        while getattr(response, "tool_calls", None) and rounds < max_tool_rounds:
            rounds += 1
            for tool_call in response.tool_calls:
                args = tool_call.arguments if isinstance(tool_call.arguments, dict) else json.loads(tool_call.arguments or "{}")
                result = await dispatch(tool_call.name, args, ctx)
                logger.info("AI tool conversation_id=%s tool=%s", conversation_id, tool_call.name)
                tool_trace.append({"tool": tool_call.name, "args": args, "result": result})
                chat.add_tool_result(tool_call.id, json.dumps(result, default=str))
            response = await chat.send_message_with_tools()

        return {"reply": response.content or "", "tool_trace": tool_trace}


ai_agent = AIAgentService()
