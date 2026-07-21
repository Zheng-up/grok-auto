from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import FRONTEND_DIST
from app.runtime import services


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    services.close()


app = FastAPI(
    title="Grok Registration Console",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "grok-registration-console"}


if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        target = candidate if candidate.is_file() and str(candidate).startswith(str(FRONTEND_DIST)) else FRONTEND_DIST / "index.html"
        response = FileResponse(target)
        if target.name == "index.html":
            response.headers["Cache-Control"] = "no-store"
        return response


def run() -> None:
    import uvicorn

    from app.config import runtime

    uvicorn.run("app.main:app", host=runtime.host, port=runtime.port, reload=False)


if __name__ == "__main__":
    run()