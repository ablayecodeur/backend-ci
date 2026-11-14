"""Authentification et rôles (USER, AGENT, ADMIN) : dépendances FastAPI et règles d'accès."""
import os
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import logic, models, security
from .database import get_db

USER, AGENT, ADMIN = "USER", "AGENT", "ADMIN"
ROLES = [USER, AGENT, ADMIN]

# auto_error=False : l'erreur 401 est renvoyée dans le format uniforme de l'API.
bearer_scheme = HTTPBearer(auto_error=False, description="Jeton obtenu avec POST /api/auth/login")


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if credentials is None:
        raise logic.ApiError(401, "UNAUTHORIZED", "Authentification requise.")
    user_id = security.decode_access_token(credentials.credentials)
    user = db.get(models.User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise logic.ApiError(401, "INVALID_TOKEN", "Jeton invalide ou expiré.")
    return user


def require_roles(*roles: str):
    """Fabrique une dépendance qui n'accepte que les rôles indiqués."""

    def checker(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise logic.ApiError(403, "FORBIDDEN", "Vous n'avez pas le droit d'effectuer cette action.")
        return user

    return checker


require_admin = require_roles(ADMIN)
require_staff = require_roles(AGENT, ADMIN)  # personnel : agents et administrateurs


# ---------------------------------------------------------------- périmètre des agents

def agent_counter(db: Session, user: models.User) -> models.Counter:
    """Guichet assigné à l'agent (403 s'il n'en a pas)."""
    counter = db.get(models.Counter, user.counter_id) if user.counter_id is not None else None
    if counter is None:
        raise logic.ApiError(
            403, "NO_COUNTER_ASSIGNED", "Aucun guichet ne vous est assigné. Contactez l'administrateur."
        )
    return counter


def ensure_service_scope(db: Session, user: models.User, service_id: int) -> None:
    """ADMIN : tous les services. AGENT : uniquement le service de son guichet."""
    if user.role == ADMIN:
        return
    if agent_counter(db, user).service_id != service_id:
        raise logic.ApiError(403, "FORBIDDEN", "Cette ressource n'appartient pas au service de votre guichet.")


def ensure_own_counter(db: Session, user: models.User, counter_id: int) -> None:
    """Appeler le suivant : ADMIN sur n'importe quel guichet, AGENT seulement sur le sien."""
    if user.role == AGENT:
        counter = agent_counter(db, user)
        if counter.id != counter_id:
            raise logic.ApiError(403, "FORBIDDEN", "Vous ne pouvez appeler que depuis votre propre guichet.")


def ensure_ticket_access(db: Session, ticket_id: int, user: models.User) -> models.Ticket:
    """Lecture / annulation : le propriétaire du ticket, ou le personnel du service concerné."""
    ticket = logic.get_ticket(db, ticket_id)  # 404 si le ticket n'existe pas
    if user.role == USER:
        if ticket.user_id != user.id:
            raise logic.ApiError(403, "FORBIDDEN", "Ce ticket ne vous appartient pas.")
    else:
        ensure_service_scope(db, user, ticket.service_id)
    return ticket


def ensure_ticket_handler(db: Session, ticket_id: int, user: models.User) -> models.Ticket:
    """start / complete / no-show : ADMIN toujours, AGENT seulement sur les tickets appelés à son guichet."""
    ticket = logic.get_ticket(db, ticket_id)
    if user.role == AGENT:
        counter = agent_counter(db, user)
        if ticket.counter_id != counter.id:
            raise logic.ApiError(403, "FORBIDDEN", "Ce ticket n'est pas traité à votre guichet.")
    return ticket


# ---------------------------------------------------------------- comptes de démarrage

def seed_admin(db: Session) -> None:
    """Crée le compte administrateur depuis ADMIN_EMAIL / ADMIN_PASSWORD (ADMIN_NAME facultatif).

    Ne fait rien si les variables sont absentes ou si le compte existe déjà (le mot de passe n'est jamais écrasé).
    """
    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or ""
    if not email or not password:
        return
    if db.query(models.User).filter(models.User.email == email).first():
        return
    db.add(
        models.User(
            email=email,
            full_name=os.getenv("ADMIN_NAME", "Administrateur"),
            password_hash=security.hash_password(password),
            role=ADMIN,
            created_at=logic.now_utc(),
        )
    )
    db.commit()


def seed_demo_accounts(db: Session) -> None:
    """Si SEED_DEMO_ACCOUNTS=true : crée 2 agents (guichets 1 et 2) et 1 usager de démonstration.

    Mot de passe : DEMO_PASSWORD (par défaut Demo12345). À réserver au développement et aux démonstrations.
    """
    if (os.getenv("SEED_DEMO_ACCOUNTS") or "").strip().lower() not in ("1", "true", "yes"):
        return
    if (os.getenv("APP_ENV") or "development").strip().lower() in {"prod", "production"}:
        raise RuntimeError("SEED_DEMO_ACCOUNTS est interdit avec APP_ENV=production.")
    password = os.getenv("DEMO_PASSWORD", "Demo12345")
    counters = db.query(models.Counter).order_by(models.Counter.id).limit(2).all()
    demo = [
        ("agent1@samafile.sn", "Agent Un", AGENT, counters[0].id if len(counters) > 0 else None),
        ("agent2@samafile.sn", "Agent Deux", AGENT, counters[1].id if len(counters) > 1 else None),
        ("usager@samafile.sn", "Usager Démo", USER, None),
    ]
    for email, full_name, role, counter_id in demo:
        if db.query(models.User).filter(models.User.email == email).first():
            continue
        db.add(
            models.User(
                email=email,
                full_name=full_name,
                password_hash=security.hash_password(password),
                role=role,
                counter_id=counter_id,
                created_at=logic.now_utc(),
            )
        )
    db.commit()
