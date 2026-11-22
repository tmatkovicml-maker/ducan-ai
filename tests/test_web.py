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


def test_dashboard_with_sales_and_jobs(logged_in, factory, app_config):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ducan.replenishment.job import run_replenishment
    from tests.conftest import add_daily_sales

    today = datetime.now(ZoneInfo("Europe/Zagreb")).date()
    with factory() as s:
        from ducan.models import Supplier

        s.get(Supplier, 1).min_order_net = None  # the real date decides the delivery horizon
        add_daily_sales(s, "P4-0001", "15", 28, today, price="1.20")
        run_replenishment(s, app_config, today)
        s.commit()
    page = logged_in.get("/").text
    assert "Što naručiti danas" in page and "Prodaja po danima" in page
    assert "Test Dobavljač d.o.o.: " in page
    assert "<svg" in page and "18,00 €" in page  # one day of water sales


def test_competitor_price_entry(logged_in, factory):
    page = logged_in.get("/konkurencija").text
    response = logged_in.post(
        "/konkurencija",
        data={
            "csrf_token": csrf_from(page),
            "sku": "P4-0002",
            "competitor": "Market A",
            "price_gross": "14,49",
            "observed_on": "2026-11-01",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Market A" in logged_in.get("/konkurencija").text
    bad = logged_in.post(
        "/konkurencija",
        data={
            "csrf_token": csrf_from(page),
            "sku": "P4-0002",
            "competitor": "",
            "price_gross": "-1",
            "observed_on": "2099-01-01",
        },
    )
    assert bad.status_code == 422


def test_order_documents_and_receiving_via_ui(logged_in, factory, app_config):
    from datetime import date

    from ducan.replenishment.job import run_replenishment
    from tests.conftest import add_daily_sales

    today = date(2026, 11, 18)
    with factory() as s:
        add_daily_sales(s, "P4-0001", "15", 28, today)
        run = run_replenishment(s, app_config, today)
        s.commit()
        order_id = run.summary["proposed"][0]
    csv_response = logged_in.get(f"/akcije/{order_id}/narudzba.csv")
    assert csv_response.status_code == 200 and b"P4-0001" in csv_response.content
    page = logged_in.get("/akcije").text
    token = csrf_from(page)
    logged_in.post(f"/akcije/{order_id}/odobri", data={"csrf_token": token})
    response = logged_in.post(
        f"/akcije/{order_id}/primka",
        headers={"HX-Request": "true", "X-CSRF-Token": token},
        data={"qty_P4-0001": "129"},
    )
    assert "Roba je zaprimljena" in response.text


def test_staff_with_availability_grid(logged_in, factory):
    from ducan.models import Staff, StaffAvailability

    page = logged_in.get("/osoblje/novi").text
    response = logged_in.post(
        "/osoblje/novi",
        data={
            "csrf_token": csrf_from(page),
            "name": "Ana",
            "email": "ana@example.test",
            "role": "zaposlenik",
            "active": "on",
            "from_1": "07:00",
            "to_1": "15:00",
            "from_6": "08:00",
            "to_6": "20:00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with factory() as s:
        staff = s.query(Staff).one()
        days = {a.weekday for a in s.query(StaffAvailability).filter_by(staff_id=staff.id)}
        assert days == {1, 6}
    bad = logged_in.post(
        "/osoblje/novi",
        data={
            "csrf_token": csrf_from(page),
            "name": "X",
            "role": "zaposlenik",
            "from_2": "15:00",
            "to_2": "07:00",
        },
    )
    assert bad.status_code == 422 and "prije kraja" in bad.text


def test_rota_page_shows_missing_rules(logged_in):
    page = logged_in.get("/raspored").text
    assert "Raspored se ne može složiti" in page and "max_hours_per_day" in page


def test_personal_shift_page_needs_valid_token(client, factory):
    from datetime import date, time, timedelta

    from ducan.models import Shift, Staff
    from ducan.scheduling.service import new_link_token

    with factory() as s:
        ana = Staff(name="Ana", role="zaposlenik")
        ivo = Staff(name="Ivo", role="zaposlenik")
        s.add_all([ana, ivo])
        s.flush()
        token = new_link_token(ana)
        s.add(
            Shift(
                staff_id=ana.id, day=date.today() + timedelta(days=3), start=time(7), end=time(14)
            )
        )
        s.commit()
        ivo_id = ivo.id
    assert client.get("/smjene/krivi-token").status_code == 404
    page = client.get(f"/smjene/{token}")
    assert page.status_code == 200 and "Bok Ana" in page.text
    assert "Odobrenja" not in page.text  # no admin navigation without login
    shift_id = int(page.text.split('<option value="')[1].split('"')[0])
    response = client.post(
        f"/smjene/{token}/zamjena",
        data={
            "csrf_token": csrf_from(page.text),
            "shift_id": shift_id,
            "to_staff_id": ivo_id,
            "reason": "liječnik",
        },
    )
    assert "Zahtjev je poslan" in response.text


def test_chat_api_identity_and_cors(client, app_config):
    app_config.env.chat_allowed_origins = "https://ducan.example.test"
    ok = client.post(
        "/api/chat",
        json={"message": "Are you a bot?"},
        headers={"Origin": "https://ducan.example.test"},
    )
    assert ok.status_code == 200 and ok.json()["language"] == "en"
    assert "AI assistant" in ok.json()["text"]
    assert ok.headers["access-control-allow-origin"] == "https://ducan.example.test"
    other = client.post(
        "/api/chat", json={"message": "Bok"}, headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in other.headers
    intro = client.get("/api/chat/uvod?language=de").json()
    assert "KI" in intro["text"] and intro["disclosure"].startswith("Dieses Geschäft")


def test_chat_api_rate_limit_and_size(client):
    from ducan.web.routes import chat_api

    chat_api._hits.clear()
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 422
    codes = [
        client.post("/api/chat", json={"message": "Jesi li robot?"}).status_code
        for _ in range(chat_api.LIMIT + 1)
    ]
    assert codes[-1] == 429
    chat_api._hits.clear()


def test_phase4_pages(logged_in, factory):
    from ducan.models import FaqEntry, Review

    for path in ("/sadrzaj", "/recenzije", "/faq", "/transparentnost"):
        page = logged_in.get(path)
        assert page.status_code == 200 and "Poslovanje dućana vodi AI" in page.text, path
    assert logged_in.get("/transparentnost/natpis.pdf").content.startswith(b"%PDF")
    token = csrf_from(logged_in.get("/faq").text)
    logged_in.post(
        "/faq",
        data={
            "csrf_token": token,
            "question_hr": "Radite li nedjeljom?",
            "answer_hr": "Da, nedjeljom radimo.",
            "reviewed": "on",
        },
    )
    response = logged_in.post(
        "/recenzije", data={"csrf_token": token, "text": "Odlično!", "stars": "5", "language": "hr"}
    )
    assert "AI model nije postavljen" in response.text  # saved, but no model configured
    with factory() as s:
        assert s.query(FaqEntry).one().reviewed
        assert s.query(Review).count() == 1
    assert (
        "Da, nedjeljom radimo."
        in logged_in.post(
            "/api/chat", json={"message": "radite li nedjeljom", "language": "hr"}
        ).json()["text"]
    )
