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

from src import auth, logic, models, security  # noqa: E402
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
        db.add(
            models.User(
                email="admin@test.sn",
                full_name="Admin Test",
                password_hash=security.hash_password("AdminPass123"),
                role=auth.ADMIN,
                created_at=logic.now_utc(),
            )
        )
        db.commit()
    yield


def login_client(email, password):
    """Client HTTP connecté avec ce compte."""
    c = TestClient(app)
    response = c.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    c.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return c


@pytest.fixture
def anon_client():
    """Client sans jeton (visiteur non connecté)."""
    return TestClient(app)


@pytest.fixture
def client():
    """Client connecté en administrateur : peut tout faire (les tests existants l'utilisent)."""
    return login_client("admin@test.sn", "AdminPass123")


def make_user_client(email="awa@test.sn", full_name="Awa Ndiaye"):
    """Crée un compte usager par l'inscription publique et renvoie un client connecté avec ce compte."""
    c = TestClient(app)
    response = c.post("/api/auth/register", json={"email": email, "password": "Motdepasse1", "full_name": full_name})
    assert response.status_code == 201, response.text
    c.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return c


def make_agent_client(admin, email="agent1@test.sn", counter_id=1):
    """L'administrateur crée un agent (affecté à un guichet) ; renvoie un client connecté en agent."""
    payload = {"email": email, "password": "AgentPass123", "full_name": "Agent", "role": "AGENT"}
    if counter_id is not None:
        payload["counter_id"] = counter_id
    response = admin.post("/api/users", json=payload)
    assert response.status_code == 201, response.text
    return login_client(email, "AgentPass123")
