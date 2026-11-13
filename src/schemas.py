"""Formats JSON échangés avec le frontend (contrat d'API)."""
from datetime import datetime, timezone
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is not None:  # ramener en UTC sans fuseau pour ne pas doubler le suffixe
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


# Les dates sont stockées en UTC et renvoyées en ISO 8601 avec "Z" (ex. 2026-11-09T10:00:00Z).
UtcDatetime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


class TicketCreate(BaseModel):
    service_id: int
    user_name: Optional[str] = Field(default=None, max_length=100)
    user_id: Optional[int] = None  # réservé au personnel : créer le ticket pour un compte usager


class TicketCreated(BaseModel):
    id: int
    ticket_number: str
    status: str
    position: int
    message: str


class TicketItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticket_number: str
    service_id: int
    counter_id: Optional[int] = None
    status: str
    created_at: UtcDatetime
    called_at: Optional[UtcDatetime] = None


class TicketOut(TicketItem):
    user_name: Optional[str] = None
    counter_name: Optional[str] = None  # guichet qui a appelé le ticket (à afficher à l'usager)
    position: Optional[int] = None  # renseignée seulement si le ticket est WAITING
    people_ahead: Optional[int] = None  # personnes WAITING devant lui (position - 1)
    notification: Optional[str] = None  # message « rapprochez-vous » (10 personnes ou moins devant)
    message: str


class NoShowOut(TicketOut):
    next_ticket: Optional[TicketOut] = None  # ticket appelé automatiquement après l'absence


class TicketList(BaseModel):
    items: List[TicketItem]
    total: int


class PositionOut(BaseModel):
    id: int
    ticket_number: str
    status: str
    counter_id: Optional[int] = None  # guichet appelant (renseigné une fois le ticket appelé)
    counter_name: Optional[str] = None
    position: Optional[int] = None
    people_ahead: Optional[int] = None
    notification: Optional[str] = None


class ServiceOut(BaseModel):
    id: int
    name: str
    opening_time: str
    closing_time: str
    ticket_cutoff_time: Optional[str] = None
    ticket_cutoff_minutes_before_closing: int = 30
    is_open: bool
    ticket_distribution_open: bool
    no_show_timeout_seconds: int


class CounterOut(BaseModel):
    id: int
    name: str
    service_id: int
    service_name: str
    is_active: bool


# ---------------------------------------------------------------- comptes et authentification

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
Role = Literal["USER", "AGENT", "ADMIN"]


class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    phone: Optional[str] = None
    role: str
    counter_id: Optional[int] = None  # guichet assigné (agents)
    is_active: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # secondes
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class UserCreate(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)
    role: Role = "USER"
    counter_id: Optional[int] = None  # seulement pour un agent


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)
    role: Optional[Role] = None
    counter_id: Optional[int] = None
    is_active: Optional[bool] = None


class UserList(BaseModel):
    items: List[UserOut]
    total: int


# ---------------------------------------------------------------- administration

HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    opening_time: str = Field(pattern=HHMM_PATTERN)
    closing_time: str = Field(pattern=HHMM_PATTERN)
    ticket_cutoff_time: Optional[str] = Field(default=None, pattern=HHMM_PATTERN)
    ticket_cutoff_minutes_before_closing: int = Field(default=30, ge=0, le=1440)
    no_show_timeout_seconds: int = Field(default=120, ge=10, le=3600)


class ServiceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    opening_time: Optional[str] = Field(default=None, pattern=HHMM_PATTERN)
    closing_time: Optional[str] = Field(default=None, pattern=HHMM_PATTERN)
    ticket_cutoff_time: Optional[str] = Field(default=None, pattern=HHMM_PATTERN)
    ticket_cutoff_minutes_before_closing: int = Field(default=30, ge=0, le=1440)
    no_show_timeout_seconds: Optional[int] = Field(default=None, ge=10, le=3600)
    is_active: Optional[bool] = None


class CounterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    service_id: int


class CounterUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class ServiceStats(BaseModel):
    service_id: int
    service_name: str
    total_today: int
    waiting: int
    called: int
    in_progress: int
    completed: int
    no_show: int
    cancelled: int
    expired: int
    average_wait_seconds: Optional[int] = None  # attente moyenne avant l'appel (tickets appelés aujourd'hui)


class StatsOut(BaseModel):
    date: str
    total_tickets: int
    services: List[ServiceStats]
