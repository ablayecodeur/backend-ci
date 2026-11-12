"""SamaFile - backend unique : services (horaires), tickets, guichets et appel du prochain ticket."""
from contextlib import asynccontextmanager
import os
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, logic, models, routes_admin, schemas, security
from .database import SessionLocal, get_db, seed_demo_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        seed_demo_data(db)  # crée les services et guichets seulement si la base est vide
        auth.seed_admin(db)  # compte administrateur si ADMIN_EMAIL et ADMIN_PASSWORD sont définis
        auth.seed_demo_accounts(db)  # agents et usager de démo si SEED_DEMO_ACCOUNTS=true
    yield


app = FastAPI(title="SamaFile - API", version="2.0.0", lifespan=lifespan)

_cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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


# ---------------------------------------------------------------- authentification (tous les rôles)

def _token_response(user: models.User) -> schemas.TokenOut:
    return schemas.TokenOut(
        access_token=security.create_access_token(user.id, user.role),
        expires_in=security.ACCESS_TOKEN_MINUTES * 60,
        user=schemas.UserOut.model_validate(user),
    )


@app.post("/api/auth/register", response_model=schemas.TokenOut, status_code=201)
def register(payload: schemas.RegisterIn, db: Session = Depends(get_db)):
    """Inscription libre d'un usager (rôle USER). Les agents et administrateurs sont créés par le personnel."""
    email = payload.email.strip().lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise logic.ApiError(409, "EMAIL_ALREADY_USED", "Un compte existe déjà avec cet email.")
    user = models.User(
        email=email,
        full_name=payload.full_name.strip(),
        phone=(payload.phone or "").strip() or None,
        password_hash=security.hash_password(payload.password),
        role=auth.USER,
        created_at=logic.now_utc(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise logic.ApiError(409, "EMAIL_ALREADY_USED", "Un compte existe déjà avec cet email.")
    db.refresh(user)
    return _token_response(user)


@app.post("/api/auth/login", response_model=schemas.TokenOut)
def login(payload: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.strip().lower()).first()
    stored = user.password_hash if user else security.DUMMY_HASH
    password_ok = security.verify_password(payload.password, stored)
    if user is None or not password_ok or not user.is_active:
        raise logic.ApiError(401, "INVALID_CREDENTIALS", "Email ou mot de passe incorrect.")
    return _token_response(user)


@app.get("/api/auth/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(auth.get_current_user)):
    return user


@app.patch("/api/auth/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("full_name") is not None:
        user.full_name = data["full_name"].strip()
    if "phone" in data:
        user.phone = (data["phone"] or "").strip() or None
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/change-password")
def change_password(
    payload: schemas.PasswordChange,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    if not security.verify_password(payload.current_password, user.password_hash):
        raise logic.ApiError(400, "INVALID_PASSWORD", "Le mot de passe actuel est incorrect.")
    user.password_hash = security.hash_password(payload.new_password)
    db.commit()
    return {"message": "Mot de passe modifié."}


# ---------------------------------------------------------------- services (lecture publique)

@app.get("/api/services", response_model=list[schemas.ServiceOut])
def list_services(db: Session = Depends(get_db)):
    services = db.query(models.Service).filter(models.Service.is_active.is_(True)).order_by(models.Service.id).all()
    return [logic.service_out(s) for s in services]


@app.get("/api/services/{service_id}", response_model=schemas.ServiceOut)
def get_service(service_id: int, db: Session = Depends(get_db)):
    return logic.service_out(logic.get_service(db, service_id))


# ---------------------------------------------------------------- tickets

@app.post("/api/tickets", response_model=schemas.TicketCreated, status_code=201)
def create_ticket(
    payload: schemas.TicketCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    """Usager : crée son propre ticket. Personnel : crée un ticket au comptoir, pour un compte usager
    (user_id) ou pour une personne sans compte (user_name seulement)."""
    owner_id = None
    user_name = payload.user_name
    if user.role == auth.USER:
        if payload.user_id is not None and payload.user_id != user.id:
            raise logic.ApiError(403, "FORBIDDEN", "Vous ne pouvez créer un ticket que pour vous-même.")
        owner_id = user.id
        user_name = user_name or user.full_name
    else:
        auth.ensure_service_scope(db, user, payload.service_id)
        if payload.user_id is not None:
            owner = db.get(models.User, payload.user_id)
            if owner is None or owner.role != auth.USER or not owner.is_active:
                raise logic.ApiError(404, "USER_NOT_FOUND", "Compte usager introuvable.")
            owner_id = owner.id
            user_name = user_name or owner.full_name
    ticket = logic.create_ticket(db, payload.service_id, user_name, owner_id)
    return schemas.TicketCreated(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status,
        position=logic.position_of(db, ticket),
        message="Votre ticket a été créé.",
    )


@app.get("/api/me/tickets", response_model=schemas.TicketList)
def my_tickets(
    status: Optional[str] = Query(None, pattern=logic.STATUS_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    """Historique des tickets du compte connecté (du plus récent au plus ancien)."""
    return logic.list_user_tickets(db, user.id, status, limit, offset)


@app.get("/api/tickets", response_model=schemas.TicketList)
def list_tickets(
    service_id: Optional[int] = None,
    counter_id: Optional[int] = None,
    status: Optional[str] = Query(None, pattern=logic.STATUS_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    if user.role == auth.AGENT:  # un agent ne voit que le service de son guichet
        own_service = auth.agent_counter(db, user).service_id
        if service_id not in (None, own_service):
            raise logic.ApiError(403, "FORBIDDEN", "Vous ne pouvez consulter que le service de votre guichet.")
        service_id = own_service
    return logic.list_tickets(db, service_id, counter_id, status, limit, offset)


@app.get("/api/tickets/{ticket_id}", response_model=schemas.TicketOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    auth.ensure_ticket_access(db, ticket_id, user)
    return logic.ticket_out(db, logic.get_ticket(db, ticket_id))


@app.get("/api/tickets/{ticket_id}/position", response_model=schemas.PositionOut)
def get_position(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    auth.ensure_ticket_access(db, ticket_id, user)
    ticket = logic.get_ticket(db, ticket_id)
    position = logic.position_of(db, ticket)
    people_ahead = position - 1 if position else None
    return schemas.PositionOut(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status,
        counter_id=ticket.counter_id,
        counter_name=logic.counter_name_of(db, ticket),
        position=position,
        people_ahead=people_ahead,
        notification=logic.approach_notification(people_ahead),
    )


@app.patch("/api/tickets/{ticket_id}/start", response_model=schemas.TicketOut)
def start_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.require_staff)):
    auth.ensure_ticket_handler(db, ticket_id, user)
    ticket, _ = logic.apply_action(db, ticket_id, "start", actor_id=user.id)
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/complete", response_model=schemas.TicketOut)
def complete_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.require_staff)):
    auth.ensure_ticket_handler(db, ticket_id, user)
    ticket, _ = logic.apply_action(db, ticket_id, "complete", actor_id=user.id)
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/cancel", response_model=schemas.TicketOut)
def cancel_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    auth.ensure_ticket_access(db, ticket_id, user)  # propriétaire, ou personnel du service
    ticket, _ = logic.apply_action(db, ticket_id, "cancel", actor_id=user.id)
    return logic.ticket_out(db, ticket)


@app.patch("/api/tickets/{ticket_id}/no-show", response_model=schemas.NoShowOut)
def no_show_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.require_staff)):
    auth.ensure_ticket_handler(db, ticket_id, user)
    ticket, next_ticket = logic.apply_action(db, ticket_id, "no_show", actor_id=user.id)
    return schemas.NoShowOut(
        **dict(logic.ticket_out(db, ticket)),
        next_ticket=logic.ticket_out(db, next_ticket) if next_ticket else None,
    )


@app.post("/api/tickets/{ticket_id}/reactivate", response_model=schemas.TicketOut)
def reactivate_ticket(ticket_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.require_staff)):
    ticket = logic.get_ticket(db, ticket_id)
    auth.ensure_service_scope(db, user, ticket.service_id)
    ticket, _ = logic.apply_action(db, ticket_id, "reactivate", actor_id=user.id)
    return logic.ticket_out(db, ticket)


# ---------------------------------------------------------------- guichets

@app.get("/api/counters", response_model=list[schemas.CounterOut])
def list_counters(service_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(models.Counter)
    if service_id is not None:
        query = query.filter(models.Counter.service_id == service_id)
    return [logic.counter_out(c) for c in query.order_by(models.Counter.id).all()]


@app.get("/api/counters/{counter_id}/tickets", response_model=schemas.TicketList)
def counter_queue(
    counter_id: int,
    status: Optional[str] = Query("WAITING", pattern=logic.STATUS_PATTERN),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """File du guichet : tickets de son service (par défaut ceux en attente, dans l'ordre FIFO)."""
    counter = db.get(models.Counter, counter_id)
    if counter is None:
        raise logic.ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")
    auth.ensure_service_scope(db, user, counter.service_id)
    return logic.list_tickets(db, counter.service_id, None, status, limit, offset)


@app.post("/api/counters/{counter_id}/next", response_model=schemas.TicketOut)
def call_next_ticket(counter_id: int, db: Session = Depends(get_db), user: models.User = Depends(auth.require_staff)):
    auth.ensure_own_counter(db, user, counter_id)
    ticket = logic.call_next(db, counter_id, actor_id=user.id)
    return logic.ticket_out(db, ticket)


# ---------------------------------------------------------------- comptes, services, guichets (gestion), statistiques

app.include_router(routes_admin.router)
