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
    database_url: str = (
        "postgresql+asyncpg://projectile:projectile@localhost:55432/projectile"
    )
    storage_root: Path = Path("storage")
    project_types_path: Path = Path("data/project-types.json")
    max_upload_size_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    max_files_per_request: int = Field(default=100, gt=0, le=1000)
    upload_chunk_size_bytes: int = Field(default=1024 * 1024, gt=0)
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    auto_create_schema: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
