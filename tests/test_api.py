from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta

import pytest

from src import logic, models
from src.database import SessionLocal

from conftest import TZ


def new_ticket(client, name="Awa Ndiaye"):
    response = client.post("/api/tickets", json={"service_id": 1, "user_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def call_next(counter_id=1):
    with SessionLocal() as db:
        return logic.call_next(db, counter_id).id


def error_code(response):
    return response.json()["error"]["code"]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_t01_create_ticket_during_opening_hours(client):
    body = new_ticket(client)
    assert set(body) == {"id", "ticket_number", "status", "position", "message"}
    assert body["ticket_number"] == "A001"
    assert body["status"] == "WAITING"
    assert body["position"] == 1
    assert body["message"] == "Votre ticket a été créé."


def test_positions_follow_creation_order(client):
    assert [new_ticket(client)["position"] for _ in range(3)] == [1, 2, 3]


def test_t02_refused_before_opening(client, clock):
    clock["now"] = datetime(2026, 11, 9, 7, 0, tzinfo=TZ)
    response = client.post("/api/tickets", json={"service_id": 1})
    assert response.status_code == 409
    assert error_code(response) == "SERVICE_CLOSED"
    assert "08:00" in response.json()["error"]["message"]


def test_t03_refused_at_closing_time(client, clock):
    clock["now"] = datetime(2026, 11, 9, 16, 0, tzinfo=TZ)
    response = client.post("/api/tickets", json={"service_id": 1})
    assert response.status_code == 409
    assert error_code(response) == "SERVICE_CLOSED"
    assert client.get("/api/tickets").json()["total"] == 0


def test_unknown_service(client):
    response = client.post("/api/tickets", json={"service_id": 99})
    assert response.status_code == 404
    assert error_code(response) == "SERVICE_NOT_FOUND"


def test_t04_get_ticket_and_position(client):
    new_ticket(client)
    ticket = new_ticket(client)
    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["ticket_number"] == "A002"
    assert detail["status"] == "WAITING"
    assert detail["position"] == 2
    assert detail["created_at"].endswith("Z")
    assert detail["called_at"] is None
    position = client.get(f"/api/tickets/{ticket['id']}/position").json()
    assert position["position"] == 2


def test_t05_called_ticket_is_the_oldest(client):
    first = new_ticket(client)
    new_ticket(client)
    call_next(1)
    detail = client.get(f"/api/tickets/{first['id']}").json()
    assert detail["status"] == "CALLED"
    assert detail["counter_id"] == 1
    assert detail["position"] is None


def test_t06_start_then_complete(client):
    ticket = new_ticket(client)
    call_next(1)
    started = client.patch(f"/api/tickets/{ticket['id']}/start")
    assert started.status_code == 200 and started.json()["status"] == "IN_PROGRESS"
    done = client.patch(f"/api/tickets/{ticket['id']}/complete")
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED"


def test_complete_a_waiting_ticket_is_refused(client):
    ticket = new_ticket(client)
    response = client.patch(f"/api/tickets/{ticket['id']}/complete")
    assert response.status_code == 409
    assert error_code(response) == "INVALID_TRANSITION"


def test_t07_no_show_too_early_then_allowed(client, clock):
    first = new_ticket(client)
    second = new_ticket(client)
    call_next(1)

    early = client.patch(f"/api/tickets/{first['id']}/no-show")
    assert early.status_code == 409
    assert error_code(early) == "NO_SHOW_TOO_EARLY"
    assert early.json()["error"]["details"]["remaining_seconds"] == 120

    clock["now"] += timedelta(seconds=121)
    response = client.patch(f"/api/tickets/{first['id']}/no-show")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NO_SHOW"
    assert body["created_at"] == "2026-11-09T10:00:00Z"  # un seul suffixe Z, pas de +00:00
    assert body["called_at"] == "2026-11-09T10:00:00Z"
    assert body["next_ticket"]["id"] == second["id"]
    assert body["next_ticket"]["status"] == "CALLED"


def test_t08_reactivation_goes_to_the_end_of_the_queue(client, clock):
    first = new_ticket(client)
    new_ticket(client)
    call_next(1)
    clock["now"] += timedelta(seconds=121)
    client.patch(f"/api/tickets/{first['id']}/no-show")  # le 2e ticket est appelé
    third = new_ticket(client)
    clock["now"] += timedelta(seconds=4)

    response = client.post(f"/api/tickets/{first['id']}/reactivate")
    assert response.status_code == 200
    assert response.json()["status"] == "WAITING"
    assert client.get(f"/api/tickets/{third['id']}").json()["position"] == 1
    assert client.get(f"/api/tickets/{first['id']}").json()["position"] == 2


def test_t09_reactivation_refused_after_closing(client, clock):
    first = new_ticket(client)
    call_next(1)
    clock["now"] += timedelta(seconds=121)
    client.patch(f"/api/tickets/{first['id']}/no-show")
    clock["now"] = datetime(2026, 11, 9, 16, 30, tzinfo=TZ)
    response = client.post(f"/api/tickets/{first['id']}/reactivate")
    assert response.status_code == 409
    assert error_code(response) == "SERVICE_CLOSED"


def test_t10_tickets_expire_at_closing(client, clock):
    ticket = new_ticket(client)
    clock["now"] = datetime(2026, 11, 9, 16, 0, tzinfo=TZ)
    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["status"] == "EXPIRED"
    assert detail["position"] is None


def test_cancel_waiting_ticket(client):
    ticket = new_ticket(client)
    response = client.patch(f"/api/tickets/{ticket['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"


def test_list_tickets_with_filter(client):
    new_ticket(client)
    new_ticket(client)
    call_next(1)
    waiting = client.get("/api/tickets", params={"status": "WAITING"}).json()
    assert waiting["total"] == 1 and len(waiting["items"]) == 1
    assert client.get("/api/tickets", params={"status": "BAD"}).status_code == 422


def test_services_endpoint(client, clock):
    service = client.get("/api/services").json()[0]
    assert service == {
        "id": 1,
        "name": "Retrait",
        "opening_time": "08:00",
        "closing_time": "16:00",
        "ticket_cutoff_time": "15:30",
        "ticket_cutoff_minutes_before_closing": 30,
        "is_open": True,
        "ticket_distribution_open": True,
        "no_show_timeout_seconds": 120,
    }
    clock["now"] = datetime(2026, 11, 9, 17, 0, tzinfo=TZ)
    assert client.get("/api/services/1").json()["is_open"] is False


def test_error_format_not_found(client):
    response = client.get("/api/tickets/999")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "TICKET_NOT_FOUND", "message": "Ticket introuvable.", "details": None}
    }


def test_error_format_validation(client):
    response = client.post("/api/tickets", json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "service_id"


def test_error_format_unknown_route(client):
    response = client.get("/api/inconnu")
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


def test_notification_starts_when_ten_people_are_ahead(client):
    tickets = [new_ticket(client, f"Usager {i}") for i in range(12)]
    far = client.get(f"/api/tickets/{tickets[11]['id']}").json()  # 11 personnes devant
    assert far["people_ahead"] == 11 and far["notification"] is None
    limit = client.get(f"/api/tickets/{tickets[10]['id']}").json()  # 10 personnes devant
    assert limit["people_ahead"] == 10
    assert limit["notification"] == "Plus que 10 personnes devant vous. Rapprochez-vous du service."


def test_notification_appears_when_the_queue_moves(client):
    tickets = [new_ticket(client) for _ in range(12)]
    last = tickets[11]["id"]
    assert client.get(f"/api/tickets/{last}").json()["notification"] is None
    call_next(1)  # le 1er ticket est appelé : le 12e n'a plus que 10 personnes devant lui
    body = client.get(f"/api/tickets/{last}").json()
    assert body["people_ahead"] == 10 and body["notification"] is not None
    position = client.get(f"/api/tickets/{last}/position").json()
    assert position["people_ahead"] == 10 and position["notification"] == body["notification"]


def test_next_in_line_notification(client):
    ticket = new_ticket(client)
    body = client.get(f"/api/tickets/{ticket['id']}").json()
    assert body["people_ahead"] == 0
    assert body["notification"] == "Vous êtes le prochain : présentez-vous près du guichet."


def test_no_notification_once_the_ticket_is_called(client):
    ticket = new_ticket(client)
    call_next(1)
    body = client.get(f"/api/tickets/{ticket['id']}").json()
    assert body["people_ahead"] is None and body["notification"] is None



def test_reactivation_is_limited_to_once(client, clock):
    first = new_ticket(client)
    second = new_ticket(client)

    call_next(1)
    clock["now"] += timedelta(seconds=121)
    client.patch(f"/api/tickets/{first['id']}/no-show")

    # Le second ticket est appelé automatiquement : on le termine pour libérer le guichet.
    client.patch(f"/api/tickets/{second['id']}/start")
    client.patch(f"/api/tickets/{second['id']}/complete")

    # Le premier ticket revient en fin de file, puis peut être appelé une seconde fois.
    client.post(f"/api/tickets/{first['id']}/reactivate")
    call_next(1)
    clock["now"] += timedelta(seconds=121)
    client.patch(f"/api/tickets/{first['id']}/no-show")

    refused = client.post(f"/api/tickets/{first['id']}/reactivate")
    assert refused.status_code == 409
    assert error_code(refused) == "REACTIVATION_LIMIT_REACHED"


def test_ticket_distribution_cutoff_is_separate_from_service_closing(client, clock):
    created = client.post("/api/services", json={
        "name": "État civil",
        "opening_time": "08:00",
        "closing_time": "16:00",
        "ticket_cutoff_time": "11:30",
    })
    assert created.status_code == 201, created.text
    service = created.json()
    assert service["is_open"] is True
    assert service["ticket_distribution_open"] is True
    assert service["ticket_cutoff_time"] == "11:30"

    clock["now"] = clock["now"].replace(hour=12, minute=0)
    service_view = client.get(f"/api/services/{service['id']}").json()
    assert service_view["is_open"] is True
    assert service_view["ticket_distribution_open"] is False

    refused = client.post("/api/tickets", json={
        "service_id": service["id"],
        "user_name": "Awa",
    })
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "SERVICE_CLOSED"


def test_default_ticket_cutoff_is_30_minutes_before_closing(client, clock):
    created = client.post("/api/services", json={
        "name": "Accueil",
        "opening_time": "10:00",
        "closing_time": "12:00",
    })
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]
    assert created.json()["ticket_cutoff_time"] == "11:30"
    assert created.json()["ticket_cutoff_minutes_before_closing"] == 30

    clock["now"] = clock["now"].replace(hour=11, minute=29)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is True
    clock["now"] = clock["now"].replace(minute=30)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is False
    refused = client.post("/api/tickets", json={"service_id": service_id})
    assert refused.status_code == 409


def test_ticket_cutoff_margin_is_configurable_per_service(client, clock):
    created = client.post("/api/services", json={
        "name": "Rendez-vous",
        "opening_time": "10:00",
        "closing_time": "12:00",
        "ticket_cutoff_minutes_before_closing": 5,
    })
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]
    assert created.json()["ticket_cutoff_time"] == "11:55"

    clock["now"] = clock["now"].replace(hour=11, minute=54)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is True
    clock["now"] = clock["now"].replace(minute=55)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is False

    updated = client.patch(
        f"/api/services/{service_id}",
        json={"ticket_cutoff_minutes_before_closing": 10},
    )
    assert updated.status_code == 200
    assert updated.json()["ticket_cutoff_time"] == "11:50"


def test_zero_cutoff_margin_allows_ticket_until_closing(client, clock):
    created = client.post("/api/services", json={
        "name": "Jusqu'à fermeture",
        "opening_time": "10:00",
        "closing_time": "12:00",
        "ticket_cutoff_minutes_before_closing": 0,
    })
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]
    assert created.json()["ticket_cutoff_time"] == "12:00"

    clock["now"] = clock["now"].replace(hour=11, minute=59)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is True
    clock["now"] = clock["now"].replace(hour=12, minute=0)
    assert client.get(f"/api/services/{service_id}").json()["ticket_distribution_open"] is False


def test_default_cutoff_disables_ticketing_for_short_service_window(client, clock):
    created = client.post("/api/services", json={
        "name": "Service court",
        "opening_time": "10:00",
        "closing_time": "10:08",
    })
    assert created.status_code == 201, created.text
    service = created.json()
    assert service["ticket_cutoff_time"] == "10:00"
    assert service["ticket_distribution_open"] is False
    assert client.post("/api/tickets", json={"service_id": service["id"]}).status_code == 409


def test_in_progress_ticket_expires_at_service_closing(client, clock):
    ticket = new_ticket(client)
    call_next(1)
    started = client.patch(f"/api/tickets/{ticket['id']}/start")
    assert started.status_code == 200, started.text

    clock["now"] = clock["now"].replace(hour=16, minute=0)
    response = client.get(f"/api/tickets/{ticket['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "EXPIRED"


def test_concurrent_ticket_creation_keeps_ticket_numbers_unique():
    def create_one(index):
        with SessionLocal() as db:
            ticket = logic.create_ticket(db, 1, f"Usager {index}")
            return ticket.ticket_number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(create_one, range(8)))

    assert len(numbers) == len(set(numbers))
    assert set(numbers) == {f"A{i:03d}" for i in range(1, 9)}


def test_concurrent_calls_on_same_counter_never_assign_two_active_tickets(client):
    new_ticket(client, "Usager 1")
    new_ticket(client, "Usager 2")

    def call_once():
        with SessionLocal() as db:
            try:
                return logic.call_next(db, 1).id
            except logic.ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call_once(), range(2)))

    assert sum(isinstance(result, int) for result in results) == 1
    assert "COUNTER_BUSY" in results


def test_concurrent_no_show_transition_is_recorded_once(clock):
    with SessionLocal() as db:
        ticket_id = logic.create_ticket(db, 1, "Usager").id
    call_next(1)
    clock["now"] += timedelta(seconds=121)

    def mark_no_show():
        with SessionLocal() as db:
            try:
                logic.apply_action(db, ticket_id, "no_show")
                return "NO_SHOW"
            except logic.ApiError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: mark_no_show(), range(2)))

    assert sorted(results) == ["INVALID_TRANSITION", "NO_SHOW"]
    with SessionLocal() as db:
        history_count = (
            db.query(models.TicketStatusHistory)
            .filter(
                models.TicketStatusHistory.ticket_id == ticket_id,
                models.TicketStatusHistory.new_status == logic.NO_SHOW,
            )
            .count()
        )
    assert history_count == 1


def test_ticket_creation_rechecks_cutoff_after_numbering_lock(monkeypatch, clock):
    clock["now"] = datetime(2026, 11, 9, 11, 29, 59, tzinfo=TZ)
    with SessionLocal() as db:
        service = db.get(models.Service, 1)
        service.ticket_cutoff_time = time(11, 30)
        db.commit()

        original_execute = db.execute

        def execute_and_advance_clock(statement, params=None, **kwargs):
            result = original_execute(statement, params, **kwargs)
            if "pg_advisory_xact_lock" in str(statement):
                clock["now"] = datetime(2026, 11, 9, 11, 30, tzinfo=TZ)
            return result

        monkeypatch.setattr(db, "execute", execute_and_advance_clock)
        with pytest.raises(logic.ApiError) as exc_info:
            logic.create_ticket(db, 1, "Trop tard")

    assert exc_info.value.code == "SERVICE_CLOSED"
