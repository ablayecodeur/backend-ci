from conftest import make_user_client


def error_code(response):
    return response.json()["error"]["code"]


def test_register_returns_token_and_user_role(anon_client):
    response = anon_client.post(
        "/api/auth/register", json={"email": "Awa@Test.sn", "password": "Motdepasse1", "full_name": "Awa"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["user"]["email"] == "awa@test.sn"  # email normalisé en minuscules
    assert body["user"]["role"] == "USER"
    assert "password" not in str(body)


def test_register_cannot_choose_admin_role(anon_client):
    response = anon_client.post(
        "/api/auth/register",
        json={"email": "x@test.sn", "password": "Motdepasse1", "full_name": "X", "role": "ADMIN"},
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "USER"


def test_register_duplicate_email(anon_client):
    payload = {"email": "dup@test.sn", "password": "Motdepasse1", "full_name": "Dup"}
    assert anon_client.post("/api/auth/register", json=payload).status_code == 201
    second = anon_client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert error_code(second) == "EMAIL_ALREADY_USED"


def test_register_validation(anon_client):
    short = anon_client.post("/api/auth/register", json={"email": "a@test.sn", "password": "court", "full_name": "A"})
    assert short.status_code == 422
    bad_email = anon_client.post(
        "/api/auth/register", json={"email": "pas-un-email", "password": "Motdepasse1", "full_name": "A"}
    )
    assert bad_email.status_code == 422


def test_login_success_and_failure(anon_client):
    ok = anon_client.post("/api/auth/login", json={"email": "admin@test.sn", "password": "AdminPass123"})
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "ADMIN"
    bad_password = anon_client.post("/api/auth/login", json={"email": "admin@test.sn", "password": "mauvais"})
    assert bad_password.status_code == 401
    assert error_code(bad_password) == "INVALID_CREDENTIALS"
    unknown = anon_client.post("/api/auth/login", json={"email": "inconnu@test.sn", "password": "Motdepasse1"})
    assert unknown.status_code == 401
    assert error_code(unknown) == "INVALID_CREDENTIALS"


def test_me(anon_client):
    user = make_user_client()
    response = user.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "awa@test.sn"
    assert anon_client.get("/api/auth/me").status_code == 401


def test_invalid_token_is_rejected(anon_client):
    response = anon_client.get("/api/auth/me", headers={"Authorization": "Bearer pas.un.jeton"})
    assert response.status_code == 401
    assert error_code(response) == "INVALID_TOKEN"


def test_public_routes_need_no_token(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/api/services").status_code == 200
    assert anon_client.get("/api/counters").status_code == 200


def test_ticket_routes_require_login(anon_client):
    response = anon_client.post("/api/tickets", json={"service_id": 1})
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHORIZED"


def test_user_creates_and_reads_own_ticket(anon_client):
    user = make_user_client()
    created = user.post("/api/tickets", json={"service_id": 1, "user_name": "Awa"})
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    assert user.get(f"/api/tickets/{ticket_id}").status_code == 200
    assert user.get(f"/api/tickets/{ticket_id}/position").status_code == 200


def test_user_cannot_read_or_cancel_someone_elses_ticket():
    owner = make_user_client("owner@test.sn", "Owner")
    other = make_user_client("other@test.sn", "Other")
    ticket_id = owner.post("/api/tickets", json={"service_id": 1}).json()["id"]
    for response in (
        other.get(f"/api/tickets/{ticket_id}"),
        other.get(f"/api/tickets/{ticket_id}/position"),
        other.patch(f"/api/tickets/{ticket_id}/cancel"),
    ):
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"


def test_user_can_cancel_own_ticket():
    user = make_user_client()
    ticket_id = user.post("/api/tickets", json={"service_id": 1}).json()["id"]
    response = user.patch(f"/api/tickets/{ticket_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_user_cannot_use_admin_routes():
    user = make_user_client()
    ticket_id = user.post("/api/tickets", json={"service_id": 1}).json()["id"]
    for response in (
        user.post("/api/counters/1/next"),
        user.get("/api/counters/1/tickets"),
        user.get("/api/tickets"),
        user.patch(f"/api/tickets/{ticket_id}/start"),
        user.patch(f"/api/tickets/{ticket_id}/complete"),
        user.patch(f"/api/tickets/{ticket_id}/no-show"),
        user.post(f"/api/tickets/{ticket_id}/reactivate"),
    ):
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"


def test_admin_can_call_next_for_a_users_ticket(client):
    user = make_user_client()
    ticket_id = user.post("/api/tickets", json={"service_id": 1}).json()["id"]
    called = client.post("/api/counters/1/next")
    assert called.status_code == 200
    assert called.json()["id"] == ticket_id
    # l'usager voit le changement de statut sur son propre ticket
    assert user.get(f"/api/tickets/{ticket_id}").json()["status"] == "CALLED"
    assert client.get(f"/api/tickets/{ticket_id}").status_code == 200  # l'admin accède à tous les tickets
