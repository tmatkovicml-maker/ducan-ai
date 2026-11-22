"""Staff and their weekly availability grid."""

from __future__ import annotations

from datetime import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.models import Staff, StaffAvailability
from ducan.web.deps import get_session
from ducan.web.routes._forms import FormError, dec, text

router = APIRouter(prefix="/osoblje")
DAYS = [
    (1, "Ponedjeljak"),
    (2, "Utorak"),
    (3, "Srijeda"),
    (4, "Četvrtak"),
    (5, "Petak"),
    (6, "Subota"),
    (7, "Nedjelja"),
]
ROLES = [("vlasnik", "Vlasnik"), ("partner", "Partner/ica"), ("zaposlenik", "Zaposlenik")]


def _render(
    request: Request,
    staff: Staff | None,
    values: dict[str, Any],
    grid: dict[int, tuple[str, str]],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    return request.app.state.templates.TemplateResponse(
        request,
        "staff_form.html",
        {
            "staff": staff,
            "values": values,
            "grid": grid,
            "errors": errors,
            "days": DAYS,
            "roles": ROLES,
        },
        status_code=status_code,
    )


def _parse(form: Any) -> tuple[dict[str, Any], dict[int, tuple[time, time]], list[str]]:
    errors: list[str] = []
    data: dict[str, Any] = {
        "name": text(form.get("name")),
        "email": text(form.get("email")),
        "role": form.get("role") or "zaposlenik",
        "active": form.get("active") == "on",
    }
    try:
        data["max_hours_week"] = dec(form.get("max_hours_week"), "Najviše sati tjedno")
    except FormError as exc:
        errors.append(str(exc))
    if not data["name"]:
        errors.append("Ime je obavezno.")
    if data["email"] and "@" not in data["email"]:
        errors.append("E-mail adresa nije ispravna.")
    if data["role"] not in dict(ROLES):
        errors.append("Nepoznata uloga.")
    grid: dict[int, tuple[time, time]] = {}
    for day, label in DAYS:
        start, end = (form.get(f"from_{day}") or "").strip(), (form.get(f"to_{day}") or "").strip()
        if not start and not end:
            continue
        try:
            a, b = time.fromisoformat(start), time.fromisoformat(end)
        except ValueError:
            errors.append(f"{label}: upiši oba vremena (HH:MM).")
            continue
        if a >= b:
            errors.append(f"{label}: početak mora biti prije kraja.")
            continue
        grid[day] = (a, b)
    return data, grid, errors


def _grid_strings(grid: dict[int, tuple[time, time]]) -> dict[int, tuple[str, str]]:
    return {d: (f"{a:%H:%M}", f"{b:%H:%M}") for d, (a, b) in grid.items()}


def _save_grid(session: Session, staff: Staff, grid: dict[int, tuple[time, time]]) -> None:
    session.execute(delete(StaffAvailability).where(StaffAvailability.staff_id == staff.id))
    for day, (a, b) in grid.items():
        session.add(StaffAvailability(staff_id=staff.id, weekday=day, start=a, end=b))


@router.get("", response_class=HTMLResponse)
def list_staff(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    staff = list(session.scalars(select(Staff).order_by(Staff.active.desc(), Staff.name)))
    grids = {
        s.id: {
            a.weekday: a
            for a in session.scalars(
                select(StaffAvailability).where(StaffAvailability.staff_id == s.id)
            )
        }
        for s in staff
    }
    return request.app.state.templates.TemplateResponse(
        request, "staff.html", {"staff": staff, "grids": grids, "days": DAYS}
    )


@router.get("/novi", response_class=HTMLResponse)
def new_staff(request: Request, _actor: str = Depends(require_login)) -> Response:
    return _render(request, None, {"active": True, "role": "zaposlenik"}, {}, [])


@router.post("/novi", dependencies=[Depends(require_csrf)])
async def create_staff(
    request: Request, actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    data, grid, errors = _parse(await request.form())
    if errors:
        return _render(request, None, data, _grid_strings(grid), errors, 422)
    staff = Staff(**data)
    session.add(staff)
    session.flush()
    _save_grid(session, staff, grid)
    record(
        session,
        actor=actor,
        event="staff.created",
        entity_type="staff",
        entity_id=staff.id,
        summary=f"Novi član osoblja: {staff.name}",
    )
    session.commit()
    return RedirectResponse("/osoblje", status_code=303)


@router.get("/{staff_id}", response_class=HTMLResponse)
def edit_staff(
    staff_id: int,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    staff = session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(404, "Ne postoji.")
    grid = {
        a.weekday: (a.start, a.end)
        for a in session.scalars(
            select(StaffAvailability).where(StaffAvailability.staff_id == staff_id)
        )
    }
    values = {
        "name": staff.name,
        "email": staff.email,
        "role": staff.role,
        "max_hours_week": staff.max_hours_week,
        "active": staff.active,
    }
    return _render(request, staff, values, _grid_strings(grid), [])


@router.post("/{staff_id}", dependencies=[Depends(require_csrf)])
async def update_staff(
    staff_id: int,
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    staff = session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(404, "Ne postoji.")
    data, grid, errors = _parse(await request.form())
    if errors:
        return _render(request, staff, data, _grid_strings(grid), errors, 422)
    for key, value in data.items():
        setattr(staff, key, value)
    _save_grid(session, staff, grid)
    record(
        session,
        actor=actor,
        event="staff.updated",
        entity_type="staff",
        entity_id=staff.id,
        summary=f"Izmjena osoblja / dostupnosti: {staff.name}",
        details={"availability": {d: f"{a:%H:%M}-{b:%H:%M}" for d, (a, b) in grid.items()}},
    )
    session.commit()
    return RedirectResponse("/osoblje", status_code=303)
