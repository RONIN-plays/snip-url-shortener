def _register(client, email):
    r = client.post("/api/auth/register", json={"email": email, "password": "correct-horse"})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_register_login_and_duplicate(client, unique):
    creds = {"email": f"a{unique}@example.com", "password": "correct-horse"}
    assert client.post("/api/auth/register", json=creds).status_code == 201
    assert client.post("/api/auth/register", json=creds).status_code == 409
    r = client.post("/api/auth/login", json=creds)
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"


def test_bad_login_is_generic(client, unique):
    creds = {"email": f"b{unique}@example.com", "password": "correct-horse"}
    client.post("/api/auth/register", json=creds)
    wrong_pw = client.post("/api/auth/login", json={**creds, "password": "wrong-password"})
    no_user = client.post("/api/auth/login",
                          json={"email": f"nobody{unique}@example.com", "password": "whatever123"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()            # can't tell which emails exist


def test_validation(client):
    assert client.post("/api/auth/register", json={"email": "nope", "password": "correct-horse"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_protected_routes_need_token(client):
    assert client.get("/api/links").status_code == 401
    assert client.get("/api/links", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.delete("/api/links/abc").status_code == 401


def test_my_links_only_shows_my_links(client, user, unique):
    other = _register(client, f"o{unique}@example.com")
    mine = client.post("/api/links", json={"url": "https://mine.com"}, headers=user["headers"]).json()["code"]
    theirs = client.post("/api/links", json={"url": "https://theirs.com"}, headers=other).json()["code"]
    codes = [l["code"] for l in client.get("/api/links", headers=user["headers"]).json()]
    assert mine in codes and theirs not in codes


def test_cannot_delete_or_view_stats_of_others_links(client, user, unique):
    other = _register(client, f"p{unique}@example.com")
    code = client.post("/api/links", json={"url": "https://theirs.com"}, headers=other).json()["code"]
    assert client.delete(f"/api/links/{code}", headers=user["headers"]).status_code == 404
    assert client.get(f"/api/links/{code}/stats", headers=user["headers"]).status_code == 404
    assert client.get(f"/{code}").status_code == 302                  # still alive


def test_delete_own_link_removes_it_even_from_cache(client, user):
    code = client.post("/api/links", json={"url": "https://mine.com"}, headers=user["headers"]).json()["code"]
    assert client.get(f"/{code}").status_code == 302                  # now cached
    assert client.delete(f"/api/links/{code}", headers=user["headers"]).status_code == 204
    assert client.get(f"/{code}").status_code == 404                  # cache was invalidated
