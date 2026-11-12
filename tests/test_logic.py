from datetime import datetime, time

from src import logic, models
from src.database import SessionLocal

from conftest import TZ


def test_open_hours_boundaries(clock):
    with SessionLocal() as db:
        service = db.get(models.Service, 1)
        for hour, minute, expected in [(7, 59, False), (8, 0, True), (15, 59, True), (16, 0, False)]:
            clock["now"] = datetime(2026, 11, 9, hour, minute, tzinfo=TZ)
            assert logic.is_open(service) is expected


def test_fifo_order_between_counters():
    with SessionLocal() as db:
        first = logic.create_ticket(db, 1, "A")
        second = logic.create_ticket(db, 1, "B")
        logic.create_ticket(db, 1, "C")
        assert logic.call_next(db, 1).id == first.id
        assert logic.call_next(db, 2).id == second.id


def test_numbering_restarts_each_day(clock):
    with SessionLocal() as db:
        today = logic.create_ticket(db, 1, None).ticket_number
        clock["now"] = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        tomorrow = logic.create_ticket(db, 1, None).ticket_number
    assert (today, tomorrow) == ("A001", "A001")


def test_final_statuses_have_no_exit():
    final = {logic.COMPLETED, logic.CANCELLED, logic.EXPIRED}
    for sources, _ in logic.TRANSITIONS.values():
        assert not (sources & final)


def test_approach_notification_messages():
    assert logic.approach_notification(None) is None
    assert logic.approach_notification(11) is None
    assert logic.approach_notification(10) == "Plus que 10 personnes devant vous. Rapprochez-vous du service."
    assert logic.approach_notification(1) == "Plus qu'une personne devant vous. Rapprochez-vous du service."
    assert logic.approach_notification(0) == "Vous êtes le prochain : présentez-vous près du guichet."
