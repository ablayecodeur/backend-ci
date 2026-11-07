from datetime import datetime

from src import logic
from src.database import SessionLocal

from conftest import TZ


def new_ticket(name="Awa"):
    with SessionLocal() as db:
        return logic.create_ticket(db, 1, name).id


def error_code(response):
    return response.json()["error"]["code"]


def test_list_counters(client):
    counters = client.get("/api/counters").json()
    assert [c["name"] for c in counters] == ["Guichet 1", "Guichet 2"]
    assert counters[0] == {
        "id": 1,
        "name": "Guichet 1",
        "service_id": 1,
        "service_name": "Retrait",
        "is_active": True,
    }


def test_next_calls_the_oldest_waiting_ticket(client):
    first = new_ticket()
    new_ticket()
    response = client.post("/api/counters/1/next")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == first
    assert body["ticket_number"] == "A001"
    assert body["status"] == "CALLED"
    assert body["counter_id"] == 1
    assert body["called_at"].endswith("Z")


def test_busy_counter_cannot_call_again_but_another_can(client):
    new_ticket()
    new_ticket()
    client.post("/api/counters/1/next")
    busy = client.post("/api/counters/1/next")
    assert busy.status_code == 409 and error_code(busy) == "COUNTER_BUSY"
    other = client.post("/api/counters/2/next")
    assert other.status_code == 200 and other.json()["ticket_number"] == "A002"


def test_next_on_empty_queue(client):
    response = client.post("/api/counters/1/next")
    assert response.status_code == 409
    assert error_code(response) == "QUEUE_EMPTY"


def test_next_after_closing_finds_nothing(client, clock):
    new_ticket()
    clock["now"] = datetime(2026, 11, 9, 16, 5, tzinfo=TZ)
    assert error_code(client.post("/api/counters/1/next")) == "QUEUE_EMPTY"


def test_unknown_counter(client):
    response = client.post("/api/counters/99/next")
    assert response.status_code == 404
    assert error_code(response) == "COUNTER_NOT_FOUND"


def test_counter_queue_lists_waiting_tickets_in_fifo_order(client):
    new_ticket("A")
    new_ticket("B")
    new_ticket("C")
    client.post("/api/counters/1/next")
    queue = client.get("/api/counters/1/tickets").json()
    assert queue["total"] == 2
    assert [t["ticket_number"] for t in queue["items"]] == ["A002", "A003"]
    called = client.get("/api/counters/1/tickets", params={"status": "CALLED"}).json()
    assert called["total"] == 1
