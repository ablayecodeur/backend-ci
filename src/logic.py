"""Règles métier de SamaFile (FIFO, horaires, absence, expiration)."""
import math
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, text
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


def is_ticket_distribution_open(service: models.Service) -> bool:
    return _ticket_distribution_open_at(service, now_local())


def _ticket_distribution_open_at(service: models.Service, local_now: datetime) -> bool:
    cutoff = ticket_cutoff_time(service)
    return service.opening_time <= local_now.time() < cutoff


def ticket_cutoff_time(service: models.Service) -> dtime:
    """Heure effective : override historique ou fermeture moins l'avance configurée."""
    if service.ticket_cutoff_time is not None:
        return service.ticket_cutoff_time
    base_date = date(2000, 1, 2)
    opening = datetime.combine(base_date, service.opening_time)
    closing = datetime.combine(base_date, service.closing_time)
    cutoff = closing - timedelta(minutes=service.ticket_cutoff_minutes_before_closing)
    # Une marge couvrant toute la durée du service ferme la distribution dès l'ouverture.
    return max(cutoff, opening).time()


def ticket_cutoff_margin(service: models.Service) -> int:
    if service.ticket_cutoff_time is None:
        return service.ticket_cutoff_minutes_before_closing
    closing = datetime.combine(date(2000, 1, 2), service.closing_time)
    cutoff = datetime.combine(date(2000, 1, 2), service.ticket_cutoff_time)
    return int((closing - cutoff).total_seconds() // 60)


def _ticket_distribution_closed_error(service: models.Service, local_now: datetime) -> ApiError:
    if local_now.time() < service.opening_time:
        message = f"Le service n'est pas encore ouvert. Ouverture à {service.opening_time:%H:%M}."
    else:
        cutoff = ticket_cutoff_time(service)
        message = f"La distribution des tickets est terminée depuis {cutoff:%H:%M}."
    return ApiError(409, "SERVICE_CLOSED", message)


def service_out(service: models.Service) -> schemas.ServiceOut:
    open_now = is_open(service)
    return schemas.ServiceOut(
        id=service.id,
        name=service.name,
        opening_time=service.opening_time.strftime("%H:%M"),
        closing_time=service.closing_time.strftime("%H:%M"),
        ticket_cutoff_time=ticket_cutoff_time(service).strftime("%H:%M"),
        ticket_cutoff_minutes_before_closing=ticket_cutoff_margin(service),
        is_open=open_now,
        ticket_distribution_open=is_ticket_distribution_open(service),
        no_show_timeout_seconds=service.no_show_timeout_seconds,
    )


def record_status_change(db: Session, ticket: models.Ticket, previous: Optional[str], new: str, actor_id: Optional[int] = None) -> None:
    """Enregistre chaque transition de statut dans l'historique."""
    db.add(models.TicketStatusHistory(
        ticket_id=ticket.id,
        previous_status=previous,
        new_status=new,
        changed_at=now_utc(),
        actor_id=actor_id,
    ))


def expire_if_needed(db: Session, service: models.Service, *, commit: bool = True) -> None:
    """RM-04 : à la fermeture (ou le lendemain), tout ticket actif passe à EXPIRED."""
    closed = now_local().time() >= service.closing_time
    today = now_local().date()
    tickets = (
        db.query(models.Ticket)
        .filter(models.Ticket.service_id == service.id, models.Ticket.status.in_([WAITING, CALLED, IN_PROGRESS]))
        .all()
    )
    changed = False
    for ticket in tickets:
        if closed or _local_date(ticket.created_at) < today:
            previous = ticket.status
            ticket.status = EXPIRED
            record_status_change(db, ticket, previous, EXPIRED)
            changed = True
    if changed:
        if commit:
            db.commit()
        else:
            # Flush without releasing any row locks held by the caller.
            db.flush()


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


def counter_name_of(db: Session, ticket: models.Ticket) -> Optional[str]:
    """Nom du guichet qui a appelé le ticket (None tant qu'il n'est pas appelé)."""
    if ticket.counter_id is None:
        return None
    counter = db.get(models.Counter, ticket.counter_id)
    return counter.name if counter else None


def ticket_out(db: Session, ticket: models.Ticket) -> schemas.TicketOut:
    position = position_of(db, ticket)
    people_ahead = position - 1 if position else None
    counter_name = counter_name_of(db, ticket)
    message = MESSAGES[ticket.status]
    if ticket.status == CALLED and counter_name:
        message = f"Votre ticket est appelé. Présentez-vous au guichet : {counter_name}."
    return schemas.TicketOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        service_id=ticket.service_id,
        counter_id=ticket.counter_id,
        counter_name=counter_name,
        user_name=ticket.user_name,
        status=ticket.status,
        position=position,
        people_ahead=people_ahead,
        notification=approach_notification(people_ahead),
        created_at=ticket.created_at,
        called_at=ticket.called_at,
        message=message,
    )


def get_ticket(db: Session, ticket_id: int) -> models.Ticket:
    ticket = db.get(models.Ticket, ticket_id)
    if ticket is None:
        raise ApiError(404, "TICKET_NOT_FOUND", "Ticket introuvable.")
    expire_if_needed(db, ticket.service)
    db.refresh(ticket)
    return ticket


def create_ticket(
    db: Session, service_id: int, user_name: Optional[str], user_id: Optional[int] = None
) -> models.Ticket:
    service = get_service(db, service_id)
    expire_if_needed(db, service)
    local_now = now_local()
    if not _ticket_distribution_open_at(service, local_now):
        raise _ticket_distribution_closed_error(service, local_now)

    # Sérialise la numérotation par service : évite deux mêmes numéros en cas de créations simultanées.
    db.execute(text("SELECT pg_advisory_xact_lock(CAST(:service_id AS BIGINT))"), {"service_id": service.id})
    # L'attente du verrou peut franchir l'heure limite ou minuit. Revalider
    # l'horaire et calculer numéro/date depuis le même instant après le verrou.
    local_now = now_local()
    if not _ticket_distribution_open_at(service, local_now):
        raise _ticket_distribution_closed_error(service, local_now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_start = day_start + timedelta(days=1)
    day_start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
    next_day_start_utc = next_day_start.astimezone(timezone.utc).replace(tzinfo=None)
    created_today = (
        db.query(func.count(models.Ticket.id))
        .filter(
            models.Ticket.service_id == service.id,
            models.Ticket.created_at >= day_start_utc,
            models.Ticket.created_at < next_day_start_utc,
        )
        .scalar()
    )
    letter = chr(ord("A") + (service.id - 1) % 26)
    now = local_now.astimezone(timezone.utc).replace(tzinfo=None)
    ticket = models.Ticket(
        ticket_number=f"{letter}{created_today + 1:03d}",
        service_id=service.id,
        user_id=user_id,
        user_name=(user_name or "").strip() or None,
        status=WAITING,
        created_at=now,
        queue_at=now,
    )
    db.add(ticket)
    db.flush()
    record_status_change(db, ticket, None, WAITING, actor_id=user_id)
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


def call_next(db: Session, counter_id: int, actor_id: Optional[int] = None) -> models.Ticket:
    """RM-05 : appelle le plus ancien ticket WAITING du service du guichet."""
    # Verrouille le guichet avant le contrôle BUSY : deux requêtes simultanées
    # ne peuvent pas affecter deux tickets au même guichet.
    counter = (
        db.query(models.Counter)
        .filter(models.Counter.id == counter_id)
        .with_for_update()
        .first()
    )
    if counter is None:
        raise ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")
    if not counter.is_active:
        raise ApiError(409, "COUNTER_INACTIVE", "Ce guichet est inactif.")
    # Keep the counter lock until this call commits. Expiration must not commit
    # midway through the critical section, or concurrent calls could bypass BUSY.
    expire_if_needed(db, counter.service, commit=False)

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
        db.commit()
        raise ApiError(409, "QUEUE_EMPTY", "Aucun ticket en attente.")

    previous = ticket.status
    ticket.status = CALLED
    record_status_change(db, ticket, previous, CALLED, actor_id)
    ticket.counter_id = counter.id
    ticket.called_at = now_utc()
    db.commit()
    db.refresh(ticket)
    return ticket


def apply_action(
    db: Session, ticket_id: int, action: str, actor_id: Optional[int] = None
) -> Tuple[models.Ticket, Optional[models.Ticket]]:
    """Applique start / complete / no_show / cancel / reactivate.
    Renvoie (ticket, ticket_appelé_automatiquement) ; le second n'existe que pour no_show (RM-08)."""
    ticket = (
        db.query(models.Ticket)
        .filter(models.Ticket.id == ticket_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if ticket is None:
        raise ApiError(404, "TICKET_NOT_FOUND", "Ticket introuvable.")
    expire_if_needed(db, ticket.service, commit=False)
    allowed_from, target = TRANSITIONS[action]
    if ticket.status not in allowed_from:
        if ticket.status == EXPIRED:
            # Persist a lazy expiry even though the requested action is refused.
            db.commit()
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
        if ticket.reactivation_count >= 1:
            raise ApiError(409, "REACTIVATION_LIMIT_REACHED", "Ce ticket a déjà été réactivé une fois.")
        if not is_ticket_distribution_open(ticket.service):
            raise ApiError(409, "SERVICE_CLOSED", "La distribution des tickets est terminée. Réactivation impossible.")
        ticket.queue_at = now_utc()  # replacé en fin de file
        ticket.reactivation_count += 1
        ticket.counter_id = None
        ticket.called_at = None

    counter_id = ticket.counter_id
    previous = ticket.status
    ticket.status = target
    record_status_change(db, ticket, previous, target, actor_id)
    db.commit()
    db.refresh(ticket)

    next_ticket = None
    if action == "no_show" and counter_id:
        try:
            next_ticket = call_next(db, counter_id, actor_id=actor_id)
        except ApiError:
            next_ticket = None  # file vide ou guichet inactif : rien à appeler
    return ticket, next_ticket


# ---------------------------------------------------------------- tickets d'un usager

def list_user_tickets(
    db: Session, user_id: int, status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> schemas.TicketList:
    """Tickets d'un compte usager, du plus récent au plus ancien."""
    expire_all(db)
    query = db.query(models.Ticket).filter(models.Ticket.user_id == user_id)
    if status is not None:
        query = query.filter(models.Ticket.status == status)
    total = query.count()
    rows = query.order_by(models.Ticket.id.desc()).offset(offset).limit(limit).all()
    return schemas.TicketList(items=[schemas.TicketItem.model_validate(t) for t in rows], total=total)


# ---------------------------------------------------------------- administration des services et guichets

def parse_hhmm(value: str) -> dtime:
    return datetime.strptime(value, "%H:%M").time()


def _check_hours(opening: dtime, closing: dtime, cutoff: Optional[dtime] = None) -> None:
    if opening >= closing:
        raise ApiError(422, "VALIDATION_ERROR", "L'heure d'ouverture doit être avant l'heure de fermeture.")
    if cutoff is not None and not opening < cutoff <= closing:
        raise ApiError(422, "VALIDATION_ERROR", "L'heure limite de distribution doit être après l'ouverture et au plus égale à la fermeture.")


def create_service(db: Session, payload: schemas.ServiceCreate) -> models.Service:
    opening, closing = parse_hhmm(payload.opening_time), parse_hhmm(payload.closing_time)
    cutoff = parse_hhmm(payload.ticket_cutoff_time) if payload.ticket_cutoff_time else None
    _check_hours(opening, closing, cutoff)
    service = models.Service(
        name=payload.name.strip(),
        opening_time=opening,
        closing_time=closing,
        ticket_cutoff_time=cutoff,
        ticket_cutoff_minutes_before_closing=payload.ticket_cutoff_minutes_before_closing,
        no_show_timeout_seconds=payload.no_show_timeout_seconds,
        is_active=True,
    )
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def update_service(db: Session, service_id: int, data: dict) -> models.Service:
    service = db.get(models.Service, service_id)
    if service is None:
        raise ApiError(404, "SERVICE_NOT_FOUND", "Service introuvable.")
    opening = parse_hhmm(data["opening_time"]) if data.get("opening_time") else service.opening_time
    closing = parse_hhmm(data["closing_time"]) if data.get("closing_time") else service.closing_time
    cutoff = parse_hhmm(data["ticket_cutoff_time"]) if data.get("ticket_cutoff_time") else (None if "ticket_cutoff_time" in data else service.ticket_cutoff_time)
    _check_hours(opening, closing, cutoff)
    service.opening_time = opening
    service.closing_time = closing
    service.ticket_cutoff_time = cutoff
    if "ticket_cutoff_minutes_before_closing" in data:
        service.ticket_cutoff_minutes_before_closing = data["ticket_cutoff_minutes_before_closing"]
        if "ticket_cutoff_time" not in data:
            service.ticket_cutoff_time = None
    if data.get("name") is not None:
        service.name = data["name"].strip()
    if data.get("no_show_timeout_seconds") is not None:
        service.no_show_timeout_seconds = data["no_show_timeout_seconds"]
    if data.get("is_active") is not None:
        service.is_active = data["is_active"]
    db.commit()
    db.refresh(service)
    return service


def counter_out(counter: models.Counter) -> schemas.CounterOut:
    return schemas.CounterOut(
        id=counter.id,
        name=counter.name,
        service_id=counter.service_id,
        service_name=counter.service.name,
        is_active=counter.is_active,
    )


def create_counter(db: Session, name: str, service_id: int) -> models.Counter:
    service = get_service(db, service_id)
    counter = models.Counter(name=name.strip(), service_id=service.id)
    db.add(counter)
    db.commit()
    db.refresh(counter)
    return counter


def update_counter(db: Session, counter_id: int, data: dict) -> models.Counter:
    counter = db.get(models.Counter, counter_id)
    if counter is None:
        raise ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")
    if data.get("name") is not None:
        counter.name = data["name"].strip()
    if data.get("is_active") is not None:
        counter.is_active = data["is_active"]
    db.commit()
    db.refresh(counter)
    return counter


# ---------------------------------------------------------------- statistiques du jour

def stats(db: Session, service_id: Optional[int] = None) -> schemas.StatsOut:
    expire_all(db, service_id)
    services_query = db.query(models.Service).filter(models.Service.is_active.is_(True))
    tickets_query = db.query(models.Ticket).filter(models.Ticket.created_at >= start_of_local_day_utc())
    if service_id is not None:
        services_query = services_query.filter(models.Service.id == service_id)
        tickets_query = tickets_query.filter(models.Ticket.service_id == service_id)

    by_service: dict = {}
    for ticket in tickets_query.all():
        by_service.setdefault(ticket.service_id, []).append(ticket)

    items = []
    for service in services_query.order_by(models.Service.id).all():
        rows = by_service.get(service.id, [])
        counts = {status: 0 for status in STATUSES}
        waits = []
        for ticket in rows:
            counts[ticket.status] = counts.get(ticket.status, 0) + 1
            if ticket.called_at is not None:
                waits.append((ticket.called_at - ticket.created_at).total_seconds())
        items.append(
            schemas.ServiceStats(
                service_id=service.id,
                service_name=service.name,
                total_today=len(rows),
                waiting=counts[WAITING],
                called=counts[CALLED],
                in_progress=counts[IN_PROGRESS],
                completed=counts[COMPLETED],
                no_show=counts[NO_SHOW],
                cancelled=counts[CANCELLED],
                expired=counts[EXPIRED],
                average_wait_seconds=round(sum(waits) / len(waits)) if waits else None,
            )
        )
    return schemas.StatsOut(
        date=now_local().date().isoformat(),
        total_tickets=sum(i.total_today for i in items),
        services=items,
    )
