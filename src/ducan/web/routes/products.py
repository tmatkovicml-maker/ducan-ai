"""Product CRUD. Owner edits are applied directly and written to the audit log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.auth.security import require_csrf, require_login
from ducan.config import AppConfig
from ducan.importers.seed import validate_product_fields
from ducan.models import PriceHistory, Product, StockLevel, Supplier
from ducan.web.deps import get_config, get_session
from ducan.web.routes._forms import FormError, dec, integer, text

router = APIRouter(prefix="/proizvodi")

FIELDS = [
    "name_hr",
    "category",
    "unit",
    "vat_rate",
    "supplier_id",
    "purchase_price_net",
    "retail_price_gross",
    "min_stock",
    "max_stock",
    "shelf_life_days",
    "sales_share_pct",
    "active",
]


def _render(
    request: Request,
    session: Session,
    product: Product | None,
    values: dict[str, Any],
    errors: list[str],
    status_code: int = 200,
) -> Response:
    suppliers = list(session.scalars(select(Supplier).order_by(Supplier.name)))
    history = (
        []
        if product is None
        else list(
            session.scalars(
                select(PriceHistory)
                .where(PriceHistory.sku == product.sku)
                .order_by(PriceHistory.id.desc())
            )
        )
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "product_form.html",
        {
            "product": product,
            "values": values,
            "errors": errors,
            "suppliers": suppliers,
            "history": history,
        },
        status_code=status_code,
    )


def _parse(form: Any, session: Session, config: AppConfig) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    data: dict[str, Any] = {}
    try:
        data["name_hr"] = text(form.get("name_hr"))
        data["category"] = text(form.get("category"))
        data["unit"] = text(form.get("unit"))
        data["vat_rate"] = dec(form.get("vat_rate"), "PDV")
        data["supplier_id"] = integer(form.get("supplier_id"), "Dobavljač")
        data["purchase_price_net"] = dec(form.get("purchase_price_net"), "Nabavna cijena")
        data["retail_price_gross"] = dec(form.get("retail_price_gross"), "Maloprodajna cijena")
        data["min_stock"] = dec(form.get("min_stock"), "Minimalna zaliha")
        data["max_stock"] = dec(form.get("max_stock"), "Maksimalna zaliha")
        data["shelf_life_days"] = integer(form.get("shelf_life_days"), "Rok trajanja")
        data["sales_share_pct"] = dec(form.get("sales_share_pct"), "Udio u prodaji")
    except FormError as exc:
        return data, [str(exc)]
    data["active"] = form.get("active") == "on"
    for key, label in (("name_hr", "Naziv"), ("category", "Kategorija"), ("unit", "Jedinica")):
        if not data[key]:
            errors.append(f"{label} je obavezan podatak.")
    if data["supplier_id"] is not None and session.get(Supplier, data["supplier_id"]) is None:
        errors.append("Odabrani dobavljač ne postoji.")
    errors += validate_product_fields(
        active=data["active"],
        vat_rate=data["vat_rate"],
        purchase=data["purchase_price_net"],
        retail=data["retail_price_gross"],
        min_stock=data["min_stock"],
        max_stock=data["max_stock"],
        vat_rates=config.business.vat_rates,
    )
    return data, errors


@router.get("", response_class=HTMLResponse)
def list_products(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    products = list(session.scalars(select(Product).order_by(Product.category, Product.name_hr)))
    levels = {lvl.sku: lvl.quantity for lvl in session.scalars(select(StockLevel))}
    return request.app.state.templates.TemplateResponse(
        request, "products.html", {"products": products, "levels": levels}
    )


@router.get("/novi", response_class=HTMLResponse)
def new_product_form(
    request: Request, _actor: str = Depends(require_login), session: Session = Depends(get_session)
) -> Response:
    return _render(request, session, None, {}, [])


@router.post("/novi", dependencies=[Depends(require_csrf)])
async def create_product(
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    form = await request.form()
    data, errors = _parse(form, session, config)
    sku = (text(form.get("sku")) or "").upper()  # type: ignore[arg-type]
    if not sku:
        errors.insert(0, "SKU je obavezan.")
    elif session.get(Product, sku) is not None:
        errors.insert(0, f"SKU {sku} već postoji.")
    if errors:
        return _render(request, session, None, {**data, "sku": sku}, errors, 422)
    product = Product(sku=sku, **data)
    session.add(product)
    session.add(StockLevel(sku=sku))
    record(
        session,
        actor=actor,
        event="product.created",
        entity_type="product",
        entity_id=sku,
        summary=f"Novi proizvod {sku} {data['name_hr']}",
        details={k: str(v) for k, v in data.items()},
    )
    session.commit()
    return RedirectResponse("/proizvodi", status_code=303)


@router.get("/{sku}", response_class=HTMLResponse)
def edit_product_form(
    sku: str,
    request: Request,
    _actor: str = Depends(require_login),
    session: Session = Depends(get_session),
) -> Response:
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, "Proizvod ne postoji.")
    return _render(request, session, product, {f: getattr(product, f) for f in FIELDS}, [])


@router.post("/{sku}", dependencies=[Depends(require_csrf)])
async def update_product(
    sku: str,
    request: Request,
    actor: str = Depends(require_login),
    session: Session = Depends(get_session),
    config: AppConfig = Depends(get_config),
) -> Response:
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, "Proizvod ne postoji.")
    data, errors = _parse(await request.form(), session, config)
    if errors:
        return _render(request, session, product, data, errors, 422)
    changes = {}
    for key, value in data.items():
        old = getattr(product, key)
        if old != value:
            changes[key] = {
                "old": None if old is None else str(old),
                "new": None if value is None else str(value),
            }
            if key in ("purchase_price_net", "retail_price_gross"):
                session.add(
                    PriceHistory(
                        sku=sku,
                        field=key,
                        old_value=old,
                        new_value=value,
                        changed_by=actor,
                        reason="Ručna izmjena",
                    )
                )
            setattr(product, key, value)
    if changes:
        record(
            session,
            actor=actor,
            event="product.updated",
            entity_type="product",
            entity_id=sku,
            summary=f"Izmjena proizvoda {sku}",
            details=changes,
        )
    session.commit()
    return RedirectResponse("/proizvodi", status_code=303)
