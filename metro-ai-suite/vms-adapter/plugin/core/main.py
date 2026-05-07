"""VMS Plugin Microservice :FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from plugin.core.api.middleware import install_api_key_middleware
from plugin.core.api.routes import (
    analysis,
    cameras,
    config as config_routes,
    core_apps as core_apps_routes,
    events,
    health,
    live_captioning as lvc_routes,
    sessions as sessions_routes,
    vms as vms_routes,
)
from plugin.core.config import load_config
from plugin.core.pipeline.orchestrator import init_orchestrator


@asynccontextmanager
async def lifespan(application: FastAPI):
    orchestrator = await init_orchestrator()
    await orchestrator.startup()
    yield
    await orchestrator.shutdown()


def create_app() -> FastAPI:
    application = FastAPI(
        title="VMS Plugin Microservice",
        description="I/O Plugin for VMS/NVR Integration with Core Apps",
        version="0.1.0",
        lifespan=lifespan,
    )

    try:
        cfg = load_config()
        install_api_key_middleware(application, cfg.api.api_key)
    except SystemExit:
        # Config missing — let the lifespan handler fail loudly.
        pass

    application.include_router(health.router, prefix="/v1", tags=["Health"])
    application.include_router(cameras.router, prefix="/v1", tags=["Cameras"])
    application.include_router(events.router, prefix="/v1", tags=["Events"])
    application.include_router(analysis.router, prefix="/v1", tags=["Analysis"])
    application.include_router(config_routes.router, prefix="/v1", tags=["Config"]) # status of VMS-analytics app
    application.include_router(vms_routes.router, prefix="/v1", tags=["VMS"])
    application.include_router(core_apps_routes.router, prefix="/v1")
    application.include_router(lvc_routes.router, prefix="/v1", tags=["Live Captioning"])
    application.include_router(sessions_routes.router, prefix="/v1")
    return application


app = create_app()
