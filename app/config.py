from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PROJECTILE_",
        extra="ignore",
    )

    app_name: str = "Projectile API"
    database_url: str = "postgresql+asyncpg://projectile@localhost:55432/projectile"
    storage_root: Path = Path("storage")
    project_types_path: Path = Path("data/project-types.json")
    project_stage_templates_path: Path = Path("data/project-stage-templates.json")
    project_work_templates_path: Path = Path("data/project-work-templates.json")
    role_effort_catalog_path: Path = Path("data/role-effort-catalog.json")
    excel_estimate_template_path: Path = Path(
        "Шаблон.xlsx"
    )
    excel_recalculation_command: str | None = None
    excel_recalculation_timeout_seconds: int = Field(default=120, gt=0, le=600)
    max_upload_size_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    max_files_per_request: int = Field(default=1000, gt=0, le=1000)
    upload_chunk_size_bytes: int = Field(default=1024 * 1024, gt=0)
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    auto_create_schema: bool = True
    analysis_worker_enabled: bool = True
    analysis_poll_interval_seconds: float = Field(default=1.0, gt=0)
    codex_cli: str = "codex"
    codex_timeout_seconds: int = Field(default=300, gt=0, le=1800)
    codex_auth_file: Path | None = None
    codex_persist_auth_file: bool = False
    analysis_model: str = "gpt-5.6-luna"
    analysis_reasoning_effort: str = "medium"
    analysis_ai_direct_estimation: bool = True
    analysis_ai_effort_refinement: bool = True
    analysis_max_input_characters: int = Field(default=300_000, gt=10_000)
    analysis_digest_concurrency: int = Field(default=2, gt=0, le=8)
    recognition_model: str = "small"
    recognition_device: str = "cpu"
    recognition_compute_type: str = "int8"
    archive_max_files: int = Field(default=200, gt=0)
    archive_max_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
