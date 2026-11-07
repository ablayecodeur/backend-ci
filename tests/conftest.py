import os
import pathlib
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
# Base de test PostgreSQL dédiée (les tests vident les tables, ne jamais viser la vraie base).
# Par défaut : le conteneur postgres_server publié sur localhost:5432.
_url = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://projetdevops:projet@localhost:5432/samafile_test",
)
from sqlalchemy.engine import make_url  # noqa: E402

assert "test" in (make_url(_url).database or ""), "la base de test doit contenir 'test' dans son nom"
os.environ["DATABASE_URL"] = _url

from fastapi.testclient import TestClient  # noqa: E402

from src import logic, models  # noqa: E402
from src.database import Base, SessionLocal, engine  # noqa: E402
from src.main import app  # noqa: E402

TZ = ZoneInfo("Africa/Dakar")


@pytest.fixture
def clock(monkeypatch):
    """Horloge contrôlée : lundi 9 novembre 2026, 10h00 (service ouvert de 08:00 à 16:00)."""
    state = {"now": datetime(2026, 11, 9, 10, 0, tzinfo=TZ)}
    monkeypatch.setattr(logic, "now_local", lambda: state["now"])
    return state


@pytest.fixture(autouse=True)
def database(clock):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        service = models.Service(
            name="Retrait", opening_time=time(8, 0), closing_time=time(16, 0), no_show_timeout_seconds=120
        )
        db.add(service)
        db.flush()
        db.add(models.Counter(name="Guichet 1", service_id=service.id))
        db.add(models.Counter(name="Guichet 2", service_id=service.id))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)
