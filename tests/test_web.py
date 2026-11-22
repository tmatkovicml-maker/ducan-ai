"""Web layer: login, CSRF, CRUD, approval queue."""

from __future__ import annotations

from decimal import Decimal

from ducan.actions import service
from ducan.models import ActionStatus, ActionType, AuditLog, Product
from tests.conftest import PASSWORD, csrf_from


def test_pages_require_login(client):
    for path in ("/", "/proizvodi", "/dobavljaci", "/zalihe", "/prodaja", "/akcije", "/audit"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path


def test_wrong_password(client):
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"password": "krivo", "csrf_token": token})
    assert response.status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_without_csrf_is_rejected(client):
    client.get("/login")
    response = client.post("/login", data={"password": PASSWORD})
    assert response.status_code == 403


def test_login_throttle(client):
    token = csrf_from(client.get("/login").text)
    for _ in range(5):
        client.post("/login", data={"password": "krivo", "csrf_token": token})
    response = client.post("/login", data={"password": PASSWORD, "csrf_token": token})
    assert response.status_code == 429


def test_post_without_csrf_is_rejected(logged_in):
    response = logged_in.post("/proizvodi/P4-0001", data={"name_hr": "X"})
    assert response.status_code == 403


def test_disclosure_on_every_page(logged_in):
    for path in ("/", "/proizvodi", "/zalihe", "/prodaja", "/akcije", "/audit", "/login"):
        assert "Poslovanje dućana vodi AI uz nadzor ljudi." in logged_in.get(path).text


def test_edit_product_records_price_history_and_audit(logged_in, factory):
    page = logged_in.get("/proizvodi/P4-0001").text
    response = logged_in.post(
        "/proizvodi/P4-0001",
        data={
            "csrf_token": csrf_from(page),
            "name_hr": "Mineralna voda 0,5 l",
            "category": "Pića",
            "unit": "boca",
            "vat_rate": "25",
            "supplier_id": "1",
            "purchase_price_net": "0,45",
            "retail_price_gross": "1,20",
            "min_stock": "24",
            "max_stock": "240",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with factory() as s:
        assert s.get(Product, "P4-0001").purchase_price_net == Decimal("0.45")
        assert s.query(AuditLog).filter_by(event="product.updated").count() == 1
    assert "Povijest cijena" in logged_in.get("/proizvodi/P4-0001").text


def test_activating_incomplete_product_is_refused(logged_in):
    page = logged_in.get("/proizvodi/P4-0003").text
    response = logged_in.post(
        "/proizvodi/P4-0003",
        data={
            "csrf_token": csrf_from(page),
            "name_hr": "Razglednica",
            "category": "Suveniri",
            "unit": "kom",
            "active": "on",
        },
    )
    assert response.status_code == 422
    assert "aktivan proizvod mora imati" in response.text


def test_create_supplier_and_physical_count(logged_in, factory):
    page = logged_in.get("/dobavljaci/novi").text
    response = logged_in.post(
        "/dobavljaci/novi",
        data={
            "csrf_token": csrf_from(page),
            "name": "Novi dobavljač",
            "delivery_days": "2,5",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = logged_in.get("/zalihe").text
    response = logged_in.post(
        "/zalihe/inventura", data={"csrf_token": csrf_from(page), "count_P4-0002": "3"}
    )
    assert response.status_code == 200 and "Rezultat inventure" in response.text


def test_upload_sales_csv(logged_in):
    page = logged_in.get("/prodaja").text
    csv_bytes = (
        b"timestamp,sku,quantity,gross_amount,payment_method\n"
        b"2026-07-15T10:00:00,P4-0001,1,1.20,card\n"
    )
    response = logged_in.post(
        "/prodaja/ucitaj",
        data={"csrf_token": csrf_from(page)},
        files={"file": ("dan.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200 and "uvezeno 1" in response.text


def test_approve_via_ui(logged_in, factory, business):
    with factory() as s:
        action = service.propose(
            s,
            business,
            action_type=ActionType.PRICE_CHANGE,
            title="Ulje poskupljenje",
            payload={
                "sku": "P4-0002",
                "old_retail_price_gross": "15.90",
                "new_retail_price_gross": "16.90",
            },
            reasoning_summary="Nabavna cijena porasla.",
        )
        s.commit()
        action_id = action.id
    page = logged_in.get("/akcije").text
    assert "Ulje poskupljenje" in page
    response = logged_in.post(
        f"/akcije/{action_id}/odobri",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_from(page)},
    )
    assert response.status_code == 200 and "izvršeno" in response.text
    with factory() as s:
        assert s.get(Product, "P4-0002").retail_price_gross == Decimal("16.90")
        assert s.get(type(action), action_id).status == ActionStatus.EXECUTED


def test_manifest_and_security_headers(client):
    response = client.get("/manifest.webmanifest")
    assert response.json()["lang"] == "hr"
    assert "default-src 'self'" in response.headers["content-security-policy"]
