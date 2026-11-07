"""SamaFile - backend unique : services (horaires), tickets, guichets et appel du prochain ticket."""
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import logic, models, schemas
from .database import Base, SessionLocal, engine, get_db, seed_demo_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Provisoire : création des tables au démarrage, en attendant les migrations Alembic de M4.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo_data(db)  # crée les services et guichets seulement si la base est vide
    yield


app = FastAPI(title="SamaFile - API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- format d'erreur uniforme

def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


@app.exception_handler(logic.ApiError)
async def api_error_handler(request, exc: logic.ApiError):
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    details = [
        {
            "field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path")),
            "message": e["msg"],
        }
        for e in exc.errors()
    ]
    return error_response(422, "VALIDATION_ERROR", "Les données envoyées sont invalides.", details)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return error_response(404, "NOT_FOUND", "Ressource introuvable.")
    if exc.status_code == 405:
        return error_response(405, "METHOD_NOT_ALLOWED", "Méthode non autorisée.")
    return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc: Exception):
    return error_response(500, "INTERNAL_ERROR", "Une erreur interne est survenue.")


# ---------------------------------------------------------------- santé

@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise logic.ApiError(503, "DATABASE_UNAVAILABLE", "La base de données est injoignable.")
    return {"status": "ok"}


# ---------------------------------------------------------------- services

@app.get("/api/services", response_model=list[schemas.ServiceOut])
def list_services(db: Session = Depends(get_db)):
    services = db.query(models.Service).filter(models.Service.is_active.is_(True)).order_by(models.Service.id).all()
    return [logic.service_out(s) for s in services]


@app.get("/api/services/{service_id}", response_model=schemas.ServiceOut)
def get_service(service_id: int, db: Session = Depends(get_db)):
    return logic.service_out(logic.get_service(db, service_id))


# ---------------------------------------------------------------- tickets

@app.post("/api/tickets", response_model=schemas.TicketCreated, status_code=201)
def create_ticket(payload: schemas.TicketCreate, db: Session = Depends(get_db)):
    ticket = logic.create_ticket(db, payload.service_id, payload.user_name)
    return schemas.TicketCreated(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status,
        position=logic.position_of(db, ticket),
        message="Votre ticket a été créé.",
    )


@app.get("/api/tickets", response_model=schemas.TicketList)
def list_tickets(
    service_id: Optional[int] = None,
    counter_id: Optional[int] = None,
    status: Optional[str] = Query(None, pattern=logic.STATUS_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return logic.list_tickets(db, service_id, counter_id, status, limit, offset)


@app.get("/api/tickets/{ticket_id}", response_model=schemas.TicketOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    return logic.ticket_out(db, logic.get_ticket(db, ticket_id))


@app.get("/api/tickets/{ticket_id}/position", response_model=schemas.PositionOut)
def get_position(ticket_id: int, db: Session = Depends(get_db)):
    ticket = logic.get_ticket(db, ticket_id)
    position = logic.position_of(db, ticket)
    people_ahead = position - 1 if position else None
    return schemas.PositionOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status,
        position=position,
        people_ahead=people_ahead,
        notification=logic.approach_notification(people_ahead),
    )


@app.patch("/api/tickets/{ticket_id}/start", response_model=schemas.TicketOut)
def start_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket, _ = logic.apply_action(db, ticket_id, "start")
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/complete", response_model=schemas.TicketOut)
def complete_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket, _ = logic.apply_action(db, ticket_id, "complete")
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/cancel", response_model=schemas.TicketOut)
def cancel_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket, _ = logic.apply_action(db, ticket_id, "cancel")
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/no-show", response_model=schemas.NoShowOut)
def no_show_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket, next_ticket = logic.apply_action(db, ticket_id, "no_show")
    return schemas.NoShowOut(
        **dict(logic.ticket_out(db, ticket)),
        next_ticket=logic.ticket_out(db, next_ticket) if next_ticket else None,
    )


@app.post("/api/tickets/{ticket_id}/reactivate", response_model=schemas.TicketOut)
def reactivate_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket, _ = logic.apply_action(db, ticket_id, "reactivate")
    return logic.ticket_out(db, ticket)


# ---------------------------------------------------------------- guichets

@app.get("/api/counters", response_model=list[schemas.CounterOut])
def list_counters(service_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(models.Counter)
    if service_id is not None:
        query = query.filter(models.Counter.service_id == service_id)
    return [
        schemas.CounterOut(
            id=c.id,
            name=c.name,
            service_id=c.service_id,
            service_name=c.service.name,
            is_active=c.is_active,
        )
        for c in query.order_by(models.Counter.id).all()
    ]


@app.get("/api/counters/{counter_id}/tickets", response_model=schemas.TicketList)
def counter_queue(
    counter_id: int,
    status: Optional[str] = Query("WAITING", pattern=logic.STATUS_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """File du guichet : tickets de son service (par défaut ceux en attente, dans l'ordre FIFO)."""
    counter = db.get(models.Counter, counter_id)
    if counter is None:
        raise logic.ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")
    return logic.list_tickets(db, counter.service_id, None, status, limit, offset)


@app.post("/api/counters/{counter_id}/next", response_model=schemas.TicketOut)
def call_next_ticket(counter_id: int, db: Session = Depends(get_db)):
    ticket = logic.call_next(db, counter_id)
    return logic.ticket_out(db, ticket)
