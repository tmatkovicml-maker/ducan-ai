"""PWA manifest and service worker. Icons are used only if listed in brand/brand.yaml."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ducan.brand import BrandAssetError
from ducan.web.deps import STATIC_DIR

router = APIRouter()
ICONS = {"icon_192": "192x192", "icon_512": "512x512"}


@router.get("/manifest.webmanifest")
def manifest(request: Request) -> Response:
    brand = request.app.state.brand
    icons = [
        {"src": f"/brand/{key}", "sizes": size, "type": "image/png"}
        for key, size in ICONS.items()
        if brand.has(key)
    ]
    return JSONResponse(
        {
            "name": "Dućan AI",
            "short_name": "Dućan AI",
            "lang": "hr",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#ffffff",
            "icons": icons,
        },
        media_type="application/manifest+json",
    )


@router.get("/sw.js")
def service_worker() -> Response:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/brand/{key}")
def brand_asset(key: str, request: Request) -> Response:
    try:
        path = request.app.state.brand.asset(key)
    except BrandAssetError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path)
