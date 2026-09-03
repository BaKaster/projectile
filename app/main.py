from __future__ import annotations

import base64
import hmac
from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.analysis_worker import AnalysisWorker
from app.api import router
from app.catalog import seed_project_types
from app.commercial_proposal import CommercialProposalService
from app.config import Settings
from app.database import create_database, ensure_schema_compatibility
from app.effort_estimator import AdaptiveEffortEstimator
from app.excel_estimate import ExcelEstimateService
from app.models import Base
from app.rates import current_rates, ensure_rate_catalog
from app.stage_planner import StagePlanner
from app.work_generator import WorkGenerator


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, session_factory = create_database(resolved_settings)
        app.state.settings = resolved_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        stage_planner = StagePlanner.from_files(
            resolved_settings.project_types_path.resolve(),
            resolved_settings.project_stage_templates_path.resolve(),
        )
        app.state.stage_planner = stage_planner
        work_generator = WorkGenerator.from_file(
            resolved_settings.project_work_templates_path.resolve(), stage_planner
        )
        app.state.work_generator = work_generator
        effort_estimator = AdaptiveEffortEstimator.from_file(
            resolved_settings.role_effort_catalog_path.resolve()
        )
        app.state.effort_estimator = effort_estimator
        app.state.excel_estimate_service = ExcelEstimateService(
            resolved_settings.excel_estimate_template_path.resolve(),
            role_catalog_path=resolved_settings.role_effort_catalog_path.resolve(),
            recalculation_command=resolved_settings.excel_recalculation_command,
            recalculation_timeout_seconds=(
                resolved_settings.excel_recalculation_timeout_seconds
            ),
        )
        app.state.commercial_proposal_service = CommercialProposalService(
            resolved_settings.commercial_proposal_template_path.resolve()
        )

        if resolved_settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await ensure_schema_compatibility(engine)
        catalog_data = json.loads(resolved_settings.role_effort_catalog_path.read_text(encoding="utf-8"))
        async with session_factory() as session:
            await ensure_rate_catalog(session, catalog_data["roles"])
            for code, (sale_rate, cost_rate) in (await current_rates(session)).items():
                if role := effort_estimator.roles.get(code):
                    role.sale_rate = sale_rate
                    role.cost_rate = cost_rate
        await seed_project_types(
            session_factory, resolved_settings.project_types_path.resolve()
        )

        worker = AnalysisWorker(
            session_factory,
            resolved_settings,
            stage_planner,
            work_generator,
            effort_estimator,
        )
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
        expose_headers=[
            "Content-Disposition",
            "X-Excel-Recalculation",
            "X-Projectile-Artifact-Attached",
            "X-Projectile-Artifact-Document-Id",
            "X-Projectile-Artifact-Version",
        ],
    )
    application.include_router(router)

    @application.middleware("http")
    async def protect_demo(request, call_next):
        """Require a password when a public demonstration is explicitly enabled."""
        password = resolved_settings.demo_password
        if password:
            supplied_username = ""
            supplied_password = ""
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Basic "):
                try:
                    decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                    supplied_username, supplied_password = decoded.split(":", 1)
                except (UnicodeDecodeError, ValueError):
                    pass
            if not (
                hmac.compare_digest(supplied_username, resolved_settings.demo_username)
                and hmac.compare_digest(supplied_password, password)
            ):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Projectile demo", charset="UTF-8"'},
                )

        return await call_next(request)

    frontend_path = Path(__file__).resolve().parent.parent / "frontend"
    application.mount(
        "/",
        StaticFiles(directory=frontend_path, html=True),
        name="frontend",
    )
    return application


app = create_app()
