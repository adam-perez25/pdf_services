"""Middlewares: request-size limit, cross-site guard, no-cache for static
files and the security headers. main.py adds them in a fixed order (see the
comment there).
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app import config


def max_request_bytes() -> int:
    return int((config.MAX_TOTAL_UPLOAD_MB + config.MULTIPART_OVERHEAD_MB) * 1024 * 1024)


def request_too_big() -> str:
    return f"La petición supera el límite de {config.MAX_TOTAL_UPLOAD_MB} MB."


class UploadSizeLimit:
    """Stop oversized uploads before they are written to temporary files.

    The per-file checks in the handlers only run once Starlette has received
    and spooled the whole body, so this rejects a too-big Content-Length up
    front and cuts off bodies without one (chunked) as soon as they pass the
    limit, instead of filling the temp disk first.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        limit = max_request_bytes()
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            response = JSONResponse({"detail": request_too_big()}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so
                    # this becomes a normal 413 response.
                    raise HTTPException(status_code=413, detail=request_too_big())
            return message

        await self.app(scope, limited_receive, send)


CROSS_SITE_MESSAGE = "Petición rechazada: solo se aceptan peticiones desde las páginas de este servidor."
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_PORTS = {"http": "80", "https": "443"}


def same_origin(origin: str, host: str) -> bool:
    """Whether an Origin header (scheme://host[:port]) names this Host."""
    scheme, separator, authority = origin.partition("://")
    if not separator or not authority or "/" in authority or not host:
        return False  # includes Origin: null (sandboxed pages, file:)

    def normalize(value: str) -> str:
        value = value.lower()
        default = DEFAULT_PORTS.get(scheme.lower())
        if default and value.endswith(f":{default}"):
            value = value[: -len(default) - 1]
        return value

    return normalize(authority) == normalize(host)


def is_cross_site(headers: dict[bytes, str]) -> bool:
    fetch_site = headers.get(b"sec-fetch-site", "").strip().lower()
    if fetch_site:
        # Modern browsers: trust Sec-Fetch-Site ("same-origin" for the app's
        # own pages, "none" when the user typed the URL).
        return fetch_site in {"cross-site", "same-site"}
    origin = headers.get(b"origin", "").strip()
    # Older browsers: compare Origin with Host. "null" is let through: with
    # Referrer-Policy: no-referrer browsers send it even for the app's own
    # POSTs, and every browser that sends Sec-Fetch-Site is handled above.
    if not origin or origin == "null":
        return False
    return not same_origin(origin, headers.get(b"host", "").strip())


class CrossSiteRequestGuard:
    """Reject POST/DELETE... under /api/ sent by pages of other sites (CSRF).

    Without it any web page open in the browser could make it upload files
    to this service or delete organizer documents. Browsers say where a
    request comes from: Sec-Fetch-Site (cross-site or same-site means another
    site, including another port of localhost) and Origin (must match the
    Host). Requests without these headers (curl, scripts, tests) are allowed,
    and so are the app's own pages (same-origin).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["method"] in STATE_CHANGING_METHODS
            and scope["path"].startswith("/api/")
        ):
            headers = {}
            for name, value in scope["headers"]:
                headers.setdefault(name, value.decode("latin-1"))
            if is_cross_site(headers):
                response = JSONResponse({"detail": CROSS_SITE_MESSAGE}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def revalidate_static_files(request, call_next):
    # Make browsers check for a newer CSS/JS after the image is rebuilt instead
    # of reusing a stale copy; unchanged files are answered with a cheap 304.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Everything the pages load comes from this server; blob: covers the local
# previews of selected images.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob:; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response
