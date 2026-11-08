import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.services.gee_service import initialize_gee

log = logging.getLogger("urbanfever")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s...", settings.PROJECT_NAME)
    try:
        initialize_gee()
    except Exception as e:  # keep the API up (health/docs) even if EE auth fails; requests retry init
        log.error("Earth Engine initialisation failed: %s", e)
    yield
    log.info("Shutting down...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root():
    return {"project": settings.PROJECT_NAME, "version": settings.VERSION, "status": "running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "healthy"}
