"""Formats JSON échangés avec le frontend (contrat d'API)."""
from datetime import datetime, timezone
from typing import Annotated, List, Optional

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
    position: Optional[int] = None
    people_ahead: Optional[int] = None
    notification: Optional[str] = None


class ServiceOut(BaseModel):
    id: int
    name: str
    opening_time: str
    closing_time: str
    is_open: bool
    ticket_distribution_open: bool
    no_show_timeout_seconds: int


class CounterOut(BaseModel):
    id: int
    name: str
    service_id: int
    service_name: str
    is_active: bool
