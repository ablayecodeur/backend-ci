"""Routes de gestion : comptes (agents et usagers), services, guichets et statistiques."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, logic, models, schemas, security
from .database import get_db

router = APIRouter()


# ---------------------------------------------------------------- comptes

def _get_manageable_user(db: Session, actor: models.User, user_id: int) -> models.User:
    """Un administrateur gère tous les comptes ; un agent ne gère que les comptes usagers."""
    target = db.get(models.User, user_id)
    if target is None:
        raise logic.ApiError(404, "USER_NOT_FOUND", "Compte introuvable.")
    if actor.role == auth.AGENT and target.role != auth.USER:
        raise logic.ApiError(403, "FORBIDDEN", "Un agent ne peut gérer que les comptes usagers.")
    return target


def _check_counter(db: Session, counter_id: Optional[int]) -> None:
    if counter_id is not None and db.get(models.Counter, counter_id) is None:
        raise logic.ApiError(404, "COUNTER_NOT_FOUND", "Guichet introuvable.")


@router.post("/api/users", response_model=schemas.UserOut, status_code=201)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    actor: models.User = Depends(auth.require_staff),
):
    """Crée un compte. Un agent crée des comptes usagers ; un administrateur crée aussi agents et administrateurs."""
    if actor.role == auth.AGENT and payload.role != auth.USER:
        raise logic.ApiError(403, "FORBIDDEN", "Un agent ne peut créer que des comptes usagers.")
    if payload.counter_id is not None and payload.role != auth.AGENT:
        raise logic.ApiError(422, "VALIDATION_ERROR", "counter_id n'est valable que pour un agent.")
    _check_counter(db, payload.counter_id)

    email = payload.email.strip().lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise logic.ApiError(409, "EMAIL_ALREADY_USED", "Un compte existe déjà avec cet email.")
    user = models.User(
        email=email,
        full_name=payload.full_name.strip(),
        phone=(payload.phone or "").strip() or None,
        password_hash=security.hash_password(payload.password),
        role=payload.role,
        counter_id=payload.counter_id,
        created_at=logic.now_utc(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise logic.ApiError(409, "EMAIL_ALREADY_USED", "Un compte existe déjà avec cet email.")
    db.refresh(user)
    return user


@router.get("/api/users", response_model=schemas.UserList)
def list_users(
    role: Optional[str] = Query(None, pattern="^(USER|AGENT|ADMIN)$"),
    q: Optional[str] = Query(None, max_length=100, description="Recherche dans le nom et l'email"),
    is_active: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: models.User = Depends(auth.require_staff),
):
    query = db.query(models.User)
    if actor.role == auth.AGENT:
        if role not in (None, auth.USER):
            raise logic.ApiError(403, "FORBIDDEN", "Un agent ne peut consulter que les comptes usagers.")
        query = query.filter(models.User.role == auth.USER)
    elif role is not None:
        query = query.filter(models.User.role == role)
    if is_active is not None:
        query = query.filter(models.User.is_active.is_(is_active))
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(models.User.email).like(pattern), func.lower(models.User.full_name).like(pattern)))
    total = query.count()
    rows = query.order_by(models.User.id).offset(offset).limit(limit).all()
    return schemas.UserList(items=[schemas.UserOut.model_validate(u) for u in rows], total=total)


@router.get("/api/users/{user_id}", response_model=schemas.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), actor: models.User = Depends(auth.require_staff)):
    return _get_manageable_user(db, actor, user_id)


@router.patch("/api/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    db: Session = Depends(get_db),
    actor: models.User = Depends(auth.require_staff),
):
    """Modifie un compte. L'administrateur peut tout changer (rôle, guichet, activation) ; l'agent seulement
    le nom, le téléphone et l'activation d'un compte usager."""
    target = _get_manageable_user(db, actor, user_id)
    data = payload.model_dump(exclude_unset=True)

    if actor.role == auth.AGENT and set(data) - {"full_name", "phone", "is_active"}:
        raise logic.ApiError(
            403, "FORBIDDEN", "Un agent peut seulement modifier le nom, le téléphone et l'activation d'un usager."
        )
    if target.id == actor.id and (
        data.get("is_active") is False or (data.get("role") is not None and data["role"] != target.role)
    ):
        raise logic.ApiError(409, "CANNOT_MODIFY_SELF", "Vous ne pouvez pas vous désactiver ni changer votre propre rôle.")

    if "role" in data or "counter_id" in data:
        new_role = data.get("role") or target.role
        counter_id = data.get("counter_id", target.counter_id)
        if new_role != auth.AGENT:
            counter_id = None  # seul un agent a un guichet
        _check_counter(db, counter_id)
        target.role = new_role
        target.counter_id = counter_id
    if data.get("full_name") is not None:
        target.full_name = data["full_name"].strip()
    if "phone" in data:
        target.phone = (data["phone"] or "").strip() or None
    if data.get("is_active") is not None:
        target.is_active = data["is_active"]
    db.commit()
    db.refresh(target)
    return target


@router.post("/api/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: schemas.PasswordReset,
    db: Session = Depends(get_db),
    actor: models.User = Depends(auth.require_staff),
):
    """Définit un nouveau mot de passe (usager qui a oublié le sien, par exemple)."""
    target = _get_manageable_user(db, actor, user_id)
    target.password_hash = security.hash_password(payload.new_password)
    db.commit()
    return {"message": "Mot de passe réinitialisé."}


# ---------------------------------------------------------------- services et guichets (administrateur)

@router.post(
    "/api/services",
    response_model=schemas.ServiceOut,
    status_code=201,
    dependencies=[Depends(auth.require_admin)],
)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)):
    return logic.service_out(logic.create_service(db, payload))


@router.patch(
    "/api/services/{service_id}",
    response_model=schemas.ServiceOut,
    dependencies=[Depends(auth.require_admin)],
)
def update_service(service_id: int, payload: schemas.ServiceUpdate, db: Session = Depends(get_db)):
    """Modifie le nom, les horaires, le délai d'absence ou désactive le service (is_active=false)."""
    return logic.service_out(logic.update_service(db, service_id, payload.model_dump(exclude_unset=True)))


@router.post(
    "/api/counters",
    response_model=schemas.CounterOut,
    status_code=201,
    dependencies=[Depends(auth.require_admin)],
)
def create_counter(payload: schemas.CounterCreate, db: Session = Depends(get_db)):
    return logic.counter_out(logic.create_counter(db, payload.name, payload.service_id))


@router.patch(
    "/api/counters/{counter_id}",
    response_model=schemas.CounterOut,
    dependencies=[Depends(auth.require_admin)],
)
def update_counter(counter_id: int, payload: schemas.CounterUpdate, db: Session = Depends(get_db)):
    return logic.counter_out(logic.update_counter(db, counter_id, payload.model_dump(exclude_unset=True)))


# ---------------------------------------------------------------- statistiques

@router.get("/api/stats", response_model=schemas.StatsOut)
def get_stats(
    service_id: Optional[int] = None,
    db: Session = Depends(get_db),
    actor: models.User = Depends(auth.require_staff),
):
    """Tableau de bord du jour. Un agent ne voit que le service de son guichet."""
    if actor.role == auth.AGENT:
        own_service = auth.agent_counter(db, actor).service_id
        if service_id not in (None, own_service):
            raise logic.ApiError(403, "FORBIDDEN", "Vous ne pouvez consulter que le service de votre guichet.")
        service_id = own_service
    return logic.stats(db, service_id)
