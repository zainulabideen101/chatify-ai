"""MongoDB connection, ObjectId helpers, and base document model."""
import os
from typing import Annotated, Any
from bson import ObjectId
from pydantic import BeforeValidator, BaseModel, ConfigDict, Field
from motor.motor_asyncio import AsyncIOMotorClient

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]


def _validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str):
        return v
    raise ValueError("Invalid ObjectId")


PyObjectId = Annotated[str, BeforeValidator(_validate_object_id)]


class BaseDocument(BaseModel):
    """Base for all persisted documents. Maps mongo _id <-> id (str)."""
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: PyObjectId | None = Field(default=None, alias="_id")

    @classmethod
    def from_mongo(cls, doc: dict | None):
        if not doc:
            return None
        return cls(**doc)

    def to_mongo(self, exclude_none: bool = True) -> dict:
        data = self.model_dump(by_alias=True, exclude_none=exclude_none)
        data.pop("_id", None)
        return data


def _jsonify(value):
    """Recursively convert ObjectId values into strings."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


def serialize(doc: dict | None) -> dict | None:
    """Convert a raw mongo doc into a JSON-safe dict (_id -> id string, nested ObjectIds -> str)."""
    if doc is None:
        return None
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        else:
            out[k] = _jsonify(v)
    return out


def serialize_list(docs: list[dict]) -> list[dict]:
    return [serialize(d) for d in docs]
