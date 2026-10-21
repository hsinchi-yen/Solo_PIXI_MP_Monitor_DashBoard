"""Request / response models (shown in the OpenAPI docs at /docs)."""
from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ─── Auth & accounts ─────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = Field("", max_length=64)
    password: str = Field("", max_length=256)


class LoginResult(BaseModel):
    ok: bool = True
    token: str
    role: Literal["admin", "uploader"]
    username: str
    display_name: Optional[str] = None
    expires_at: datetime


class Identity(BaseModel):
    username: str
    role: Literal["admin", "uploader"]
    display_name: Optional[str] = None
    expires_at: datetime


class ChangePasswordBody(BaseModel):
    old_password: str = Field("", max_length=256)
    new_password: str = Field("", max_length=256)


class UserCreate(BaseModel):
    username: str = Field(..., max_length=64)
    display_name: str = Field("", max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    password: str = Field("", max_length=256)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    enabled: Optional[bool] = None


class PasswordBody(BaseModel):
    password: str = Field("", max_length=256)


# ─── Analytics ───────────────────────────────────────────────────────────────

class ScopeInfo(BaseModel):
    label: str
    params: Dict[str, str]


class Summary(BaseModel):
    total: int
    pass_: int = Field(..., alias="pass")
    fail: int
    stop: int
    yield_pct: Optional[float] = None
    first_pass: int = 0
    first_pass_pct: Optional[float] = None
    retry_units: int
    retry_rate: float
    stop_to_pass: int
    test_attempts: int
    first_test: Optional[datetime] = None
    last_test: Optional[datetime] = None

    model_config = {"populate_by_name": True}


class TrendPoint(BaseModel):
    t: datetime | date
    total: int
    passed: int
    yield_pct: Optional[float] = None
    first_pass: int = 0
    fpy_pct: Optional[float] = None


class Trend(BaseModel):
    unit: Literal["hour", "day"]
    points: List[TrendPoint]


class AlignmentTargets(BaseModel):
    """{work order: planned quantity, or null to clear}"""
    targets: Dict[str, Optional[int]]


# ─── Log Uploader ────────────────────────────────────────────────────────────

class ValidateBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    work_order: Optional[str] = Field("", max_length=64)
    planned_qty: Optional[str | int] = None


class CommitBody(ValidateBody):
    ack_warnings: bool = False
    mode: Literal["chat", "form"] = "form"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    messages: list = Field(default_factory=list)
    known: Optional[dict] = None
    lang: str = Field("zh", max_length=8)      # UI language for the reply


# ─── AI reports ──────────────────────────────────────────────────────────────

class AskBody(BaseModel):
    messages: list = Field(default_factory=list)
    lang: str = Field("zh", max_length=8)      # UI language: the default answer language
