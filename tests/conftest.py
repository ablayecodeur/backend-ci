import os
import pathlib
import sys
from datetime import datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
_test_url = os.getenv("TEST_DATABASE_URL")
if not _test_url:
    raise RuntimeError("Définissez TEST_DATABASE_URL vers une base PostgreSQL dédiée aux tests.")

_parsed_test_url = make_url(_test_url)
_database_name = (_parsed_test_url.database or "").lower()
if os.getenv("APP_ENV", "development").strip().lower() in {"prod", "production"}:
    raise RuntimeError("Les tests sont interdits avec APP_ENV=production.")
if not _database_name.endswith("_test"):
    raise RuntimeError("La base de test doit avoir un nom se terminant par '_test'.")
if _parsed_test_url.host not in {"localhost", "127.0.0.1", "::1"} and os.getenv("TEST_DATABASE_ALLOW_REMOTE") != "1":
    raise RuntimeError(
        "La base de test distante doit être explicitement autorisée avec TEST_DATABASE_ALLOW_REMOTE=1."
    )

# Toutes les tables de la suite vivent dans un schéma éphémère dédié. Les tests
# n'effacent et ne recréent jamais les tables du schéma public ni d'autres schémas.
_test_schema = f"samafile_pytest_{uuid4().hex}"
_schema_admin_engine = create_engine(_parsed_test_url)
with _schema_admin_engine.begin() as connection:
    connection.execute(text(f'CREATE SCHEMA "{_test_schema}"'))

_isolated_test_url = _parsed_test_url.set(
    query={**dict(_parsed_test_url.query), "options": f"-c search_path={_test_schema}"}
)
os.environ["DATABASE_URL"] = _isolated_test_url.render_as_string(hide_password=False)

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
    with engine.connect() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
    if current_schema != _test_schema:
        raise RuntimeError(
            f"Isolation PostgreSQL invalide : schéma courant {current_schema!r}, attendu {_test_schema!r}."
        )
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


def pytest_sessionfinish(session, exitstatus):
    try:
        with _schema_admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{_test_schema}" CASCADE'))
    finally:
        engine.dispose()
        _schema_admin_engine.dispose()


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
