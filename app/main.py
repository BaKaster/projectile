from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis_worker import AnalysisWorker
from app.api import router
from app.catalog import seed_project_types
from app.config import Settings
from app.database import create_database, ensure_schema_compatibility
from app.models import Base


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, session_factory = create_database(resolved_settings)
        app.state.settings = resolved_settings
        app.state.engine = engine
        app.state.session_factory = session_factory

        if resolved_settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await ensure_schema_compatibility(engine)
        await seed_project_types(
            session_factory, resolved_settings.project_types_path.resolve()
        )

        worker = AnalysisWorker(session_factory, resolved_settings)
        app.state.analysis_worker = worker
        if resolved_settings.analysis_worker_enabled:
            await worker.recover_interrupted()
            worker.start()

        yield
        await worker.stop()
        await engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    return application


app = create_app()
