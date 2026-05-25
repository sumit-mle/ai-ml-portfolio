"""Self-hosted compliance vendor portal — FastAPI app.

Three vendors. Each has its own login at `/{vendor_id}/login`, a dashboard
at `/{vendor_id}/dashboard`, and document pages under `/{vendor_id}/docs/...`.

Why we self-host: scraping arbitrary public portals is legally fraught
(Reddit/Google sued multiple scrapers in late 2025). A self-hosted target
lets us demonstrate every production pattern (login, MFA, allowlist,
audit, evidence packaging) with zero compliance risk and 100% reproducibility.

Pages are server-rendered HTML so a deterministic Playwright scraper works
exactly the same as the browser-use agent.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from .data import VENDORS, get_vendor
from .pdfs import ensure_pdfs

logger = logging.getLogger(__name__)


def _login_html(vendor_id: str, error: str | None = None) -> str:
    vendor = get_vendor(vendor_id)
    err = f'<div class="err">{error}</div>' if error else ""
    return f"""\
<!doctype html>
<html><head>
  <title>{vendor.name} — Vendor Portal Login</title>
  <style>{_CSS}</style>
</head><body>
<header><h1>{vendor.name}</h1><div class="pill">Vendor Portal</div></header>
<main><div class="card">
  <h2>Sign in</h2>
  {err}
  <form method="post" action="/{vendor_id}/login">
    <label>Email <input name="email" type="email" required autocomplete="username" /></label>
    <label>Password <input name="password" type="password" required autocomplete="current-password" /></label>
    <button type="submit">Continue</button>
  </form>
</div></main>
</body></html>
"""


def _mfa_html(vendor_id: str, hint_code: str, error: str | None = None) -> str:
    vendor = get_vendor(vendor_id)
    err = f'<div class="err">{error}</div>' if error else ""
    # The "test" code is shown in a banner so the agent can find it.
    # In real systems this would come from a TOTP/SMS — but the structural
    # pattern (extract the code, type it, submit) is what we want to test.
    return f"""\
<!doctype html>
<html><head>
  <title>{vendor.name} — MFA</title>
  <style>{_CSS}</style>
</head><body>
<header><h1>{vendor.name}</h1><div class="pill">Step 2 of 2</div></header>
<main><div class="card">
  <h2>Enter your verification code</h2>
  <p class="muted">For this demo only, your code is shown below.</p>
  <div class="banner">Verification code: <b>{hint_code}</b></div>
  {err}
  <form method="post" action="/{vendor_id}/mfa">
    <label>6-digit code <input name="code" inputmode="numeric" autocomplete="one-time-code" required /></label>
    <button type="submit">Continue</button>
  </form>
</div></main>
</body></html>
"""


def _dashboard_html(vendor_id: str) -> str:
    vendor = get_vendor(vendor_id)
    rows = "\n".join(
        f'<tr><td><a href="/{vendor_id}{d.page_path}">{d.title}</a></td>'
        f'<td>{d.kind}</td>'
        f'<td>{d.valid_until or "—"}</td></tr>'
        for d in vendor.documents
    )
    return f"""\
<!doctype html>
<html><head>
  <title>{vendor.name} — Compliance Documents</title>
  <style>{_CSS}</style>
</head><body>
<header><h1>{vendor.name}</h1><div class="pill">Compliance documents</div>
  <a href="/{vendor_id}/logout" class="muted">Sign out</a></header>
<main><div class="card">
  <h2>Available documents</h2>
  <table><thead><tr><th>Document</th><th>Type</th><th>Valid until</th></tr></thead>
  <tbody>{rows}</tbody></table>
  <p class="muted">Click a document to view and download.</p>
</div></main>
</body></html>
"""


def _doc_page_html(vendor_id: str, page_path: str) -> str:
    vendor = get_vendor(vendor_id)
    spec = next((d for d in vendor.documents if d.page_path == page_path), None)
    if spec is None:
        raise HTTPException(404, "document page not found")
    if not spec.is_present or spec.pdf_filename is None:
        return f"""\
<!doctype html>
<html><head><title>{vendor.name} — {spec.title}</title><style>{_CSS}</style></head>
<body>
<header><h1>{vendor.name}</h1><div class="pill">{spec.title}</div></header>
<main><div class="card">
  <h2>{spec.title}</h2>
  <div class="warn">This document is not yet uploaded for {vendor.name}.</div>
  <p class="muted">Last reviewed: vendor has been notified.</p>
  <p><a href="/{vendor_id}/dashboard">Back to documents</a></p>
</div></main>
</body></html>
"""
    return f"""\
<!doctype html>
<html><head><title>{vendor.name} — {spec.title}</title><style>{_CSS}</style></head>
<body>
<header><h1>{vendor.name}</h1><div class="pill">{spec.title}</div></header>
<main><div class="card">
  <h2>{spec.title}</h2>
  <p>Document type: <b>{spec.kind}</b></p>
  {f"<p>Valid until: <b>{spec.valid_until}</b></p>" if spec.valid_until else ""}
  <p><a class="dl" href="/{vendor_id}/downloads/{spec.pdf_filename}" download>Download PDF</a></p>
  <p><a href="/{vendor_id}/dashboard">Back to documents</a></p>
</div></main>
</body></html>
"""


_CSS = """
:root { --bg:#f7f8fa; --fg:#1d2129; --muted:#6b7280; --accent:#2563eb;
        --warn:#92400e; --bg-warn:#fef3c7; --err:#991b1b; --bg-err:#fee2e2; }
*{box-sizing:border-box} body{margin:0;font-family:-apple-system,Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--fg);}
header{padding:14px 24px;background:white;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;gap:14px;}
header h1{margin:0;font-size:18px}
.pill{font-size:12px;padding:2px 8px;border-radius:999px;background:#eef2ff;color:#3730a3;}
main{max-width:720px;margin:32px auto;padding:0 16px}
.card{background:white;border:1px solid #e5e7eb;border-radius:8px;padding:24px;}
h2{margin-top:0}
form label{display:block;margin-bottom:12px;font-size:14px;color:#374151}
form input{display:block;width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;}
button{background:var(--accent);color:white;border:none;padding:9px 18px;border-radius:6px;font-weight:600;cursor:pointer;}
.muted{color:var(--muted)}
.err{background:var(--bg-err);color:var(--err);padding:8px 12px;border-radius:6px;margin-bottom:14px;font-size:14px;}
.warn{background:var(--bg-warn);color:var(--warn);padding:8px 12px;border-radius:6px;margin-bottom:14px;font-size:14px;}
.banner{background:#eef2ff;color:#3730a3;padding:8px 12px;border-radius:6px;margin-bottom:14px;font-family:ui-monospace,Menlo,monospace;}
table{border-collapse:collapse;width:100%;margin-top:8px;}
th,td{border-bottom:1px solid #e5e7eb;padding:8px 6px;text-align:left;font-size:14px;}
.dl{display:inline-block;background:#16a34a;color:white;padding:8px 12px;border-radius:6px;text-decoration:none;}
"""


def build_app() -> FastAPI:
    s = get_settings()
    portal_dir = s.data_dir / "portal"
    pdf_paths = ensure_pdfs(portal_dir)

    app = FastAPI(title="Vendor Compliance Portal (demo)", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        rows = "".join(
            f'<li><a href="/{v}/login">{VENDORS[v].name}</a> '
            f'<span class="muted">/{v}/</span></li>'
            for v in VENDORS
        )
        return HTMLResponse(
            f"<!doctype html><html><head><title>Vendor Portals (demo)</title>"
            f"<style>{_CSS}</style></head><body>"
            f"<header><h1>Vendor Portals (demo)</h1></header>"
            f"<main><div class='card'><h2>Vendors</h2><ul>{rows}</ul></div></main>"
            f"</body></html>"
        )

    @app.get("/{vendor_id}/login", response_class=HTMLResponse)
    async def get_login(vendor_id: str) -> HTMLResponse:
        get_vendor(vendor_id)
        return HTMLResponse(_login_html(vendor_id))

    @app.post("/{vendor_id}/login")
    async def post_login(
        vendor_id: str,
        email: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        v = get_vendor(vendor_id)
        if email != v.username or password != v.password:
            return HTMLResponse(_login_html(vendor_id, "Invalid email or password."), status_code=401)
        if v.requires_mfa:
            r = RedirectResponse(url=f"/{vendor_id}/mfa", status_code=303)
            r.set_cookie(f"{vendor_id}_pwd_ok", "1", httponly=True, samesite="lax")
            return r
        r = RedirectResponse(url=f"/{vendor_id}/dashboard", status_code=303)
        r.set_cookie(f"{vendor_id}_session", "1", httponly=True, samesite="lax")
        return r

    @app.get("/{vendor_id}/mfa", response_class=HTMLResponse)
    async def get_mfa(vendor_id: str, request: Request) -> HTMLResponse:
        v = get_vendor(vendor_id)
        if not v.requires_mfa:
            return HTMLResponse(status_code=404, content="not found")
        if request.cookies.get(f"{vendor_id}_pwd_ok") != "1":
            return HTMLResponse(_login_html(vendor_id, "Please log in first."), status_code=401)
        return HTMLResponse(_mfa_html(vendor_id, v.mfa_code or "000000"))

    @app.post("/{vendor_id}/mfa")
    async def post_mfa(vendor_id: str, request: Request, code: str = Form(...)) -> Response:
        v = get_vendor(vendor_id)
        if not v.requires_mfa:
            return HTMLResponse(status_code=404, content="not found")
        if request.cookies.get(f"{vendor_id}_pwd_ok") != "1":
            return HTMLResponse(_login_html(vendor_id, "Please log in first."), status_code=401)
        if code.strip() != (v.mfa_code or ""):
            return HTMLResponse(
                _mfa_html(vendor_id, v.mfa_code or "000000", "Wrong code, try again."),
                status_code=401,
            )
        r = RedirectResponse(url=f"/{vendor_id}/dashboard", status_code=303)
        r.set_cookie(f"{vendor_id}_session", "1", httponly=True, samesite="lax")
        r.delete_cookie(f"{vendor_id}_pwd_ok")
        return r

    def _require_session(vendor_id: str, request: Request) -> None:
        if request.cookies.get(f"{vendor_id}_session") != "1":
            raise HTTPException(401, "not signed in")

    @app.get("/{vendor_id}/dashboard", response_class=HTMLResponse)
    async def get_dashboard(vendor_id: str, request: Request) -> HTMLResponse:
        get_vendor(vendor_id)
        _require_session(vendor_id, request)
        return HTMLResponse(_dashboard_html(vendor_id))

    @app.get("/{vendor_id}/docs/{slug}", response_class=HTMLResponse)
    async def get_doc_page(vendor_id: str, slug: str, request: Request) -> HTMLResponse:
        get_vendor(vendor_id)
        _require_session(vendor_id, request)
        return HTMLResponse(_doc_page_html(vendor_id, f"/docs/{slug}"))

    @app.get("/{vendor_id}/downloads/{filename}")
    async def download(vendor_id: str, filename: str, request: Request) -> Response:
        get_vendor(vendor_id)
        _require_session(vendor_id, request)
        path = portal_dir / filename
        if not path.exists():
            raise HTTPException(404, "file not found")
        return FileResponse(
            str(path),
            media_type="application/pdf",
            filename=filename,
        )

    @app.get("/{vendor_id}/logout")
    async def logout(vendor_id: str) -> Response:
        get_vendor(vendor_id)
        r = RedirectResponse(url=f"/{vendor_id}/login", status_code=303)
        r.delete_cookie(f"{vendor_id}_session")
        return r

    return app


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "src.portal.app:build_app",
        factory=True,
        host=s.portal_host,
        port=s.portal_port,
        log_level="info",
    )
