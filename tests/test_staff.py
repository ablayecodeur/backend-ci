"""Rôles AGENT et ADMIN : création de comptes, guichet, services, statistiques."""
from conftest import login_client, make_agent_client, make_user_client


def error_code(response):
    return response.json()["error"]["code"]


# ---------------------------------------------------------------- création de comptes

def test_admin_creates_agent_with_counter(client):
    response = client.post(
        "/api/users",
        json={"email": "Agent@Test.sn", "password": "AgentPass123", "full_name": "Agent Un", "role": "AGENT", "counter_id": 1},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "AGENT" and body["counter_id"] == 1 and body["email"] == "agent@test.sn"
    assert "password" not in str(body)
    assert login_client("agent@test.sn", "AgentPass123").get("/api/auth/me").json()["role"] == "AGENT"


def test_counter_id_only_for_agents(client):
    response = client.post(
        "/api/users",
        json={"email": "u@test.sn", "password": "Motdepasse1", "full_name": "U", "role": "USER", "counter_id": 1},
    )
    assert response.status_code == 422


def test_agent_creates_user_accounts_but_not_staff(client):
    agent = make_agent_client(client)
    created = agent.post(
        "/api/users", json={"email": "nouveau@test.sn", "password": "Motdepasse1", "full_name": "Nouveau", "phone": "771234567"}
    )
    assert created.status_code == 201
    assert created.json()["role"] == "USER" and created.json()["phone"] == "771234567"
    for role in ("AGENT", "ADMIN"):
        response = agent.post(
            "/api/users", json={"email": f"{role}@test.sn", "password": "Motdepasse1", "full_name": "X", "role": role}
        )
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"
    duplicate = agent.post("/api/users", json={"email": "nouveau@test.sn", "password": "Motdepasse1", "full_name": "N"})
    assert duplicate.status_code == 409


def test_user_cannot_manage_accounts():
    user = make_user_client()
    assert user.post("/api/users", json={"email": "x@test.sn", "password": "Motdepasse1", "full_name": "X"}).status_code == 403
    assert user.get("/api/users").status_code == 403
    assert user.get("/api/stats").status_code == 403


def test_agent_only_sees_user_accounts(client):
    agent = make_agent_client(client)
    make_user_client("a@test.sn", "A")
    listing = agent.get("/api/users")
    assert listing.status_code == 200
    assert {u["role"] for u in listing.json()["items"]} == {"USER"}
    assert agent.get("/api/users?role=AGENT").status_code == 403
    admin_id = client.get("/api/auth/me").json()["id"]
    assert agent.get(f"/api/users/{admin_id}").status_code == 403


def test_admin_lists_filters_and_searches_accounts(client):
    make_agent_client(client)
    make_user_client("awa@test.sn", "Awa Ndiaye")
    agents = client.get("/api/users?role=AGENT").json()
    assert agents["total"] == 1 and agents["items"][0]["role"] == "AGENT"
    found = client.get("/api/users?q=ndiaye").json()
    assert found["total"] == 1 and found["items"][0]["email"] == "awa@test.sn"


def test_admin_updates_and_deactivates_user(client, anon_client):
    user = make_user_client()
    user_id = user.get("/api/auth/me").json()["id"]
    updated = client.patch(f"/api/users/{user_id}", json={"full_name": "Awa Diop", "phone": "770000000"})
    assert updated.status_code == 200 and updated.json()["full_name"] == "Awa Diop"
    disabled = client.patch(f"/api/users/{user_id}", json={"is_active": False})
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    # l'ancien jeton et la connexion sont refusés
    assert user.get("/api/auth/me").status_code == 401
    login = anon_client.post("/api/auth/login", json={"email": "awa@test.sn", "password": "Motdepasse1"})
    assert login.status_code == 401


def test_admin_promotes_user_to_agent_with_counter(client):
    user = make_user_client()
    user_id = user.get("/api/auth/me").json()["id"]
    response = client.patch(f"/api/users/{user_id}", json={"role": "AGENT", "counter_id": 2})
    assert response.status_code == 200
    assert response.json()["role"] == "AGENT" and response.json()["counter_id"] == 2
    assert client.patch(f"/api/users/{user_id}", json={"counter_id": 99}).status_code == 404


def test_admin_cannot_deactivate_or_demote_self(client):
    admin_id = client.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/users/{admin_id}", json={"is_active": False}).status_code == 409
    response = client.patch(f"/api/users/{admin_id}", json={"role": "USER"})
    assert response.status_code == 409 and error_code(response) == "CANNOT_MODIFY_SELF"


def test_agent_can_edit_user_name_but_not_role(client):
    agent = make_agent_client(client)
    user_id = make_user_client().get("/api/auth/me").json()["id"]
    assert agent.patch(f"/api/users/{user_id}", json={"full_name": "Nouveau Nom"}).status_code == 200
    assert agent.patch(f"/api/users/{user_id}", json={"role": "ADMIN"}).status_code == 403


def test_agent_resets_user_password(client, anon_client):
    agent = make_agent_client(client)
    user_id = make_user_client().get("/api/auth/me").json()["id"]
    assert agent.post(f"/api/users/{user_id}/reset-password", json={"new_password": "NouveauPass1"}).status_code == 200
    ok = anon_client.post("/api/auth/login", json={"email": "awa@test.sn", "password": "NouveauPass1"})
    assert ok.status_code == 200
    old = anon_client.post("/api/auth/login", json={"email": "awa@test.sn", "password": "Motdepasse1"})
    assert old.status_code == 401


# ---------------------------------------------------------------- compte de l'usager

def test_user_updates_profile_and_changes_password(anon_client):
    user = make_user_client()
    assert user.patch("/api/auth/me", json={"full_name": "Awa Sow", "phone": "781112233"}).json()["full_name"] == "Awa Sow"
    wrong = user.post("/api/auth/change-password", json={"current_password": "faux", "new_password": "NouveauPass1"})
    assert wrong.status_code == 400 and error_code(wrong) == "INVALID_PASSWORD"
    ok = user.post("/api/auth/change-password", json={"current_password": "Motdepasse1", "new_password": "NouveauPass1"})
    assert ok.status_code == 200
    assert anon_client.post("/api/auth/login", json={"email": "awa@test.sn", "password": "NouveauPass1"}).status_code == 200


def test_user_lists_own_tickets_only():
    owner = make_user_client("owner@test.sn", "Owner")
    other = make_user_client("other@test.sn", "Other")
    ticket_id = owner.post("/api/tickets", json={"service_id": 1}).json()["id"]
    other.post("/api/tickets", json={"service_id": 1})
    mine = owner.get("/api/me/tickets").json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == ticket_id
    assert owner.get("/api/me/tickets?status=COMPLETED").json()["total"] == 0


def test_user_ticket_defaults_to_account_name():
    user = make_user_client(full_name="Awa Ndiaye")
    ticket_id = user.post("/api/tickets", json={"service_id": 1}).json()["id"]
    assert user.get(f"/api/tickets/{ticket_id}").json()["user_name"] == "Awa Ndiaye"


def test_user_cannot_create_ticket_for_someone_else(client):
    other_id = make_user_client("other@test.sn", "Other").get("/api/auth/me").json()["id"]
    user = make_user_client()
    response = user.post("/api/tickets", json={"service_id": 1, "user_id": other_id})
    assert response.status_code == 403


# ---------------------------------------------------------------- agent au guichet

def test_agent_full_counter_flow(client):
    agent = make_agent_client(client, counter_id=1)
    user = make_user_client()
    ticket_id = user.post("/api/tickets", json={"service_id": 1}).json()["id"]
    assert [t["id"] for t in agent.get("/api/counters/1/tickets").json()["items"]] == [ticket_id]
    called = agent.post("/api/counters/1/next")
    assert called.status_code == 200 and called.json()["id"] == ticket_id
    assert user.get(f"/api/tickets/{ticket_id}").json()["status"] == "CALLED"
    assert agent.patch(f"/api/tickets/{ticket_id}/start").json()["status"] == "IN_PROGRESS"
    assert agent.patch(f"/api/tickets/{ticket_id}/complete").json()["status"] == "COMPLETED"


def test_agent_cannot_call_from_another_counter(client):
    agent = make_agent_client(client, counter_id=1)
    make_user_client().post("/api/tickets", json={"service_id": 1})
    response = agent.post("/api/counters/2/next")
    assert response.status_code == 403 and error_code(response) == "FORBIDDEN"


def test_agent_without_counter_cannot_work(client):
    agent = make_agent_client(client, counter_id=None)
    response = agent.post("/api/counters/1/next")
    assert response.status_code == 403 and error_code(response) == "NO_COUNTER_ASSIGNED"


def test_agent_cannot_handle_ticket_called_at_another_counter(client):
    agent1 = make_agent_client(client, "agent1@test.sn", counter_id=1)
    agent2 = make_agent_client(client, "agent2@test.sn", counter_id=2)
    ticket_id = make_user_client().post("/api/tickets", json={"service_id": 1}).json()["id"]
    agent1.post("/api/counters/1/next")
    assert agent2.patch(f"/api/tickets/{ticket_id}/start").status_code == 403
    assert agent1.patch(f"/api/tickets/{ticket_id}/start").status_code == 200


def test_agent_creates_ticket_for_user_account_and_walk_in(client):
    agent = make_agent_client(client)
    account = agent.post(
        "/api/users", json={"email": "client@test.sn", "password": "Motdepasse1", "full_name": "Client Guichet"}
    ).json()
    for_account = agent.post("/api/tickets", json={"service_id": 1, "user_id": account["id"]})
    assert for_account.status_code == 201
    walk_in = agent.post("/api/tickets", json={"service_id": 1, "user_name": "Passant"})
    assert walk_in.status_code == 201
    # le client retrouve son ticket avec son propre compte
    mine = login_client("client@test.sn", "Motdepasse1").get("/api/me/tickets").json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == for_account.json()["id"]
    assert agent.post("/api/tickets", json={"service_id": 1, "user_id": 9999}).status_code == 404


# ---------------------------------------------------------------- administration

def test_admin_creates_and_updates_service(client, anon_client):
    created = client.post(
        "/api/services", json={"name": "Conseil", "opening_time": "08:00", "closing_time": "16:00"}
    )
    assert created.status_code == 201
    service = created.json()
    assert service["name"] == "Conseil" and service["opening_time"] == "08:00" and service["no_show_timeout_seconds"] == 120
    updated = client.patch(f"/api/services/{service['id']}", json={"closing_time": "17:30", "name": "Conseil client"})
    assert updated.status_code == 200 and updated.json()["closing_time"] == "17:30"
    assert len(anon_client.get("/api/services").json()) == 2
    client.patch(f"/api/services/{service['id']}", json={"is_active": False})
    assert len(anon_client.get("/api/services").json()) == 1


def test_service_validation(client):
    inverted = client.post("/api/services", json={"name": "X", "opening_time": "17:00", "closing_time": "09:00"})
    assert inverted.status_code == 422
    bad_format = client.post("/api/services", json={"name": "X", "opening_time": "8h", "closing_time": "16:00"})
    assert bad_format.status_code == 422
    assert client.patch("/api/services/99", json={"name": "X"}).status_code == 404


def test_admin_creates_counter(client):
    created = client.post("/api/counters", json={"name": "Guichet 3", "service_id": 1})
    assert created.status_code == 201
    assert created.json()["service_name"] == "Retrait"
    counter_id = created.json()["id"]
    assert client.patch(f"/api/counters/{counter_id}", json={"is_active": False}).json()["is_active"] is False
    assert client.post("/api/counters", json={"name": "X", "service_id": 99}).status_code == 404


def test_service_and_counter_management_is_admin_only(client):
    agent = make_agent_client(client)
    user = make_user_client()
    service = {"name": "X", "opening_time": "08:00", "closing_time": "16:00"}
    for caller in (agent, user):
        assert caller.post("/api/services", json=service).status_code == 403
        assert caller.patch("/api/services/1", json={"name": "Y"}).status_code == 403
        assert caller.post("/api/counters", json={"name": "X", "service_id": 1}).status_code == 403
        assert caller.patch("/api/counters/1", json={"name": "Y"}).status_code == 403


def test_agent_is_limited_to_its_service(client):
    agent = make_agent_client(client, counter_id=1)
    service2 = client.post("/api/services", json={"name": "Conseil", "opening_time": "08:00", "closing_time": "16:00"}).json()
    other_ticket = make_user_client().post("/api/tickets", json={"service_id": service2["id"]}).json()["id"]
    assert agent.patch(f"/api/tickets/{other_ticket}/cancel").status_code == 403
    assert agent.get(f"/api/tickets/{other_ticket}").status_code == 403
    assert agent.post("/api/tickets", json={"service_id": service2["id"], "user_name": "X"}).status_code == 403
    assert agent.get(f"/api/tickets?service_id={service2['id']}").status_code == 403
    assert client.get(f"/api/tickets/{other_ticket}").status_code == 200  # l'admin voit tout


def test_agent_can_cancel_ticket_of_own_service(client):
    agent = make_agent_client(client)
    ticket_id = make_user_client().post("/api/tickets", json={"service_id": 1}).json()["id"]
    response = agent.patch(f"/api/tickets/{ticket_id}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"


def test_stats_for_admin_and_agent(client):
    agent = make_agent_client(client)
    user = make_user_client()
    user.post("/api/tickets", json={"service_id": 1})
    user.post("/api/tickets", json={"service_id": 1})
    agent.post("/api/counters/1/next")
    stats = client.get("/api/stats").json()
    assert stats["total_tickets"] == 2
    service = stats["services"][0]
    assert service["service_id"] == 1 and service["waiting"] == 1 and service["called"] == 1
    assert service["average_wait_seconds"] is not None
    assert agent.get("/api/stats").json()["total_tickets"] == 2
    assert agent.get("/api/stats?service_id=99").status_code == 403
