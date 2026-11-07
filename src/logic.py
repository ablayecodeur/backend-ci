"""Règles métier de SamaFile (FIFO, horaires, absence, expiration)."""
import math
from datetime import datetime, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, schemas

TZ = ZoneInfo("Africa/Dakar")  # UTC+0, sans changement d'heure

WAITING, CALLED, IN_PROGRESS = "WAITING", "CALLED", "IN_PROGRESS"
COMPLETED, NO_SHOW, CANCELLED, EXPIRED = "COMPLETED", "NO_SHOW", "CANCELLED", "EXPIRED"
STATUSES = [WAITING, CALLED, IN_PROGRESS, COMPLETED, NO_SHOW, CANCELLED, EXPIRED]
STATUS_PATTERN = "^(" + "|".join(STATUSES) + ")$"

# action -> (statuts de départ autorisés, statut d'arrivée)
TRANSITIONS = {
    "start": ({CALLED}, IN_PROGRESS),
    "complete": ({IN_PROGRESS}, COMPLETED),
    "no_show": ({CALLED}, NO_SHOW),
    "cancel": ({WAITING, CALLED}, CANCELLED),
    "reactivate": ({NO_SHOW}, WAITING),
}

MESSAGES = {
    WAITING: "Votre ticket est en attente.",
    CALLED: "Votre ticket est appelé. Présentez-vous au guichet.",
    IN_PROGRESS: "Votre ticket est en cours de traitement.",
    COMPLETED: "Votre ticket est terminé.",
    NO_SHOW: "Vous avez été déclaré absent.",
    CANCELLED: "Votre ticket est annulé.",
    EXPIRED: "Votre ticket a expiré.",
}


# Notification « rapprochez-vous » : dès qu'il reste ce nombre de personnes (ou moins) devant un ticket.
APPROACH_THRESHOLD = 10


class ApiError(Exception):
    """Erreur métier renvoyée au format {"error": {"code", "message", "details"}}."""

    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


# ---------------------------------------------------------------- temps

def now_local() -> datetime:
    return datetime.now(TZ)


def now_utc() -> datetime:
    """Heure UTC sans fuseau (format stocké en base)."""
    return now_local().astimezone(timezone.utc).replace(tzinfo=None)


def start_of_local_day_utc() -> datetime:
    midnight = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _local_date(dt_utc: datetime):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(TZ).date()


# ---------------------------------------------------------------- services

def get_service(db: Session, service_id: int) -> models.Service:
    service = db.get(models.Service, service_id)
    if service is None or not service.is_active:
        raise ApiError(404, "SERVICE_NOT_FOUND", "Service introuvable.")
    return service


def is_open(service: models.Service) -> bool:
    return service.opening_time <= now_local().time() < service.closing_time


def service_out(service: models.Service) -> schemas.ServiceOut:
    open_now = is_open(service)
    return schemas.ServiceOut(
        id=service.id,
        name=service.name,
        opening_time=service.opening_time.strftime("%H:%M"),
        closing_time=service.closing_time.strftime("%H:%M"),
        is_open=open_now,
        ticket_distribution_open=open_now,
        no_show_timeout_seconds=service.no_show_timeout_seconds,
    )


def expire_if_needed(db: Session, service: models.Service) -> None:
    """RM-04 : à la fermeture (ou le lendemain), les tickets WAITING/CALLED passent à EXPIRED."""
    closed = now_local().time() >= service.closing_time
    today = now_local().date()
    tickets = (
        db.query(models.Ticket)
        .filter(models.Ticket.service_id == service.id, models.Ticket.status.in_([WAITING, CALLED]))
        .all()
    )
    changed = False
    for ticket in tickets:
        if closed or _local_date(ticket.created_at) < today:
            ticket.status = EXPIRED
            changed = True
    if changed:
        db.commit()


def expire_all(db: Session, service_id: Optional[int] = None) -> None:
    query = db.query(models.Service).filter(models.Service.is_active.is_(True))
    if service_id is not None:
        query = query.filter(models.Service.id == service_id)
    for service in query.all():
        expire_if_needed(db, service)


# ---------------------------------------------------------------- tickets

def position_of(db: Session, ticket: models.Ticket) -> Optional[int]:
    """1 + nombre de tickets WAITING du même service passés avant lui (FIFO)."""
    if ticket.status != WAITING:
        return None
    ahead = (
        db.query(func.count(models.Ticket.id))
        .filter(
            models.Ticket.service_id == ticket.service_id,
            models.Ticket.status == WAITING,
            (models.Ticket.queue_at < ticket.queue_at)
            | ((models.Ticket.queue_at == ticket.queue_at) & (models.Ticket.id < ticket.id)),
        )
        .scalar()
    )
    return ahead + 1


def approach_notification(people_ahead: Optional[int]) -> Optional[str]:
    """Message à afficher quand l'usager doit se rapprocher du service (sinon None)."""
    if people_ahead is None or people_ahead > APPROACH_THRESHOLD:
        return None
    if people_ahead == 0:
        return "Vous êtes le prochain : présentez-vous près du guichet."
    if people_ahead == 1:
        return "Plus qu'une personne devant vous. Rapprochez-vous du service."
    return f"Plus que {people_ahead} personnes devant vous. Rapprochez-vous du service."


def ticket_out(db: Session, ticket: models.Ticket) -> schemas.TicketOut:
    position = position_of(db, ticket)
    people_ahead = position - 1 if position else None
    return schemas.TicketOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        service_id=ticket.service_id,
        counter_id=ticket.counter_id,
        user_name=ticket.user_name,
        status=ticket.status,
        position=position,
        people_ahead=people_ahead,
        notification=approach_notification(people_ahead),
        created_at=ticket.created_at,
        called_at=ticket.called_at,
        message=MESSAGES[ticket.status],
    )


def get_ticket(db: Session, ticket_id: int) -> models.Ticket:
    ticket = db.get(models.Ticket, ticket_id)
    if ticket is None:
        raise ApiError(404, "TICKET_NOT_FOUND", "Ticket introuvable.")
    expire_if_needed(db, ticket.service)
    db.refresh(ticket)
    return ticket


def create_ticket(db: Session, service_id: int, user_name: Optional[str]) -> models.Ticket:
    service = get_service(db, service_id)
    expire_if_needed(db, service)
    if not is_open(service):
        if now_local().time() < service.opening_time:
            message = f"Le service n'est pas encore ouvert. Ouverture à {service.opening_time:%H:%M}."
        else:
            message = "Le service est fermé. Aucun ticket ne peut être attribué."
        raise ApiError(409, "SERVICE_CLOSED", message)

    created_today = (
        db.query(func.count(models.Ticket.id))
        .filter(models.Ticket.service_id == service.id, models.Ticket.created_at >= start_of_local_day_utc())
        .scalar()
    )
    letter = chr(ord("A") + (service.id - 1) % 26)
    now = now_utc()
    ticket = models.Ticket(
        ticket_number=f"{letter}{created_today + 1:03d}",
        service_id=service.id,
        user_name=(user_name or "").strip() or None,
        status=WAITING,
        created_at=now,
        queue_at=now,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def list_tickets(
    db: Session,
    service_id: Optional[int] = None,
    counter_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> schemas.TicketList:
    expire_all(db, service_id)
    query = db.query(models.Ticket)
    if service_id is not None:
        query = query.filter(models.Ticket.service_id == service_id)
    if counter_id is not None:
        query = query.filter(models.Ticket.counter_id == counter_id)
    if status is not None:
        query = query.filter(models.Ticket.status == status)
    total = query.count()
    rows = query.order_by(models.Ticket.queue_at, models.Ticket.id).offset(offset).limit(limit).all()
    return schemas.TicketList(items=[schemas.TicketItem.model_validate(t) for t in rows], total=total)


def call_next(db: Session, counter_id: int) -> models.Ticket:
    """RM-05 : appelle le plus ancien ticket WAITING du service du guichet."""
    counter = db.get(models.Counter, counter_id)
    if counter is None:
        raise ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")
    if not counter.is_active:
        raise ApiError(409, "COUNTER_INACTIVE", "Ce guichet est inactif.")
    expire_if_needed(db, counter.service)

    busy = (
        db.query(models.Ticket)
        .filter(models.Ticket.counter_id == counter.id, models.Ticket.status.in_([CALLED, IN_PROGRESS]))
        .first()
    )
    if busy is not None:
        raise ApiError(409, "COUNTER_BUSY", f"Le guichet traite déjà le ticket {busy.ticket_number}.")

    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.service_id == counter.service_id, models.Ticket.status == WAITING)
        .order_by(models.Ticket.queue_at, models.Ticket.id)
        .with_for_update(skip_locked=True)  # deux guichets ne prennent jamais le même ticket
        .first()
    )
    if ticket is None:
        raise ApiError(409, "QUEUE_EMPTY", "Aucun ticket en attente.")

    ticket.status = CALLED
    ticket.counter_id = counter.id
    ticket.called_at = now_utc()
    db.commit()
    db.refresh(ticket)
    return ticket


def apply_action(
    db: Session, ticket_id: int, action: str
) -> Tuple[models.Ticket, Optional[models.Ticket]]:
    """Applique start / complete / no_show / cancel / reactivate.
    Renvoie (ticket, ticket_appelé_automatiquement) ; le second n'existe que pour no_show (RM-08)."""
    ticket = get_ticket(db, ticket_id)
    allowed_from, target = TRANSITIONS[action]
    if ticket.status not in allowed_from:
        raise ApiError(
            409,
            "INVALID_TRANSITION",
            f"Action impossible : le ticket est au statut {ticket.status}.",
            {"status": ticket.status},
        )

    if action == "no_show":
        timeout = ticket.service.no_show_timeout_seconds
        remaining = math.ceil(timeout - (now_utc() - ticket.called_at).total_seconds())
        if remaining > 0:
            raise ApiError(
                409,
                "NO_SHOW_TOO_EARLY",
                f"Le délai de présentation n'est pas écoulé : encore {remaining} s.",
                {"remaining_seconds": remaining},
            )

    if action == "reactivate":
        if not is_open(ticket.service):
            raise ApiError(409, "SERVICE_CLOSED", "Le service est fermé. Réactivation impossible.")
        ticket.queue_at = now_utc()  # replacé en fin de file
        ticket.counter_id = None
        ticket.called_at = None

    counter_id = ticket.counter_id
    ticket.status = target
    db.commit()
    db.refresh(ticket)

    next_ticket = None
    if action == "no_show" and counter_id:
        try:
            next_ticket = call_next(db, counter_id)
        except ApiError:
            next_ticket = None  # file vide ou guichet inactif : rien à appeler
    return ticket, next_ticket
