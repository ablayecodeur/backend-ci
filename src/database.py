"""Connexion à PostgreSQL."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Forcer l'encodage UTF-8 pour éviter les erreurs de décodage (accents, Windows)
os.environ["PGCLIENTENCODING"] = "UTF8"

# PostgreSQL conteneurisé (conteneur "postgres_server").
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL doit être définie dans l’environnement.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_demo_data(db):
    """Crée 4 services et leurs guichets si la table services est vide."""
    from datetime import time

    from . import models

    if db.query(models.Service).count() > 0:
        return
    for name in ["Dépôt", "Retrait", "Ouverture de compte", "Renseignements"]:
        service = models.Service(
            name=name,
            opening_time=time(8, 0),
            closing_time=time(16, 0),
            no_show_timeout_seconds=120,
        )
        db.add(service)
        db.flush()
        db.add(models.Counter(name=f"{name} - guichet 1", service_id=service.id))
        db.add(models.Counter(name=f"{name} - guichet 2", service_id=service.id))
    db.commit()
