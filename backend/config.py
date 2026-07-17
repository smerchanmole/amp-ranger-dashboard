"""Configuración centralizada y externalizable para cada entorno Cloudera."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Contrato de despliegue: secretos, alcance, límites y rutas operativas."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ranger_url: str = "https://base3.mole4.local:6182"
    ranger_user: str = "admin"
    ranger_password: str = ""
    ranger_verify_ssl: bool = False
    ranger_services: str = "cm_hdfs,cm_knox,cm_atlas,Hadoop SQL"
    ranger_audit_page_size: int = 5000
    ranger_timeout_seconds: int = 60
    ranger_exclude_users: str = "hdfs,hive,impala,kafka,nifi,spark"
    audit_log_path: Path = Path("data/chat_audit.jsonl")
    geo_csv_path: Path = Path("geolocationDatabaseIPv4.csv")
    geo_db_path: Path = Path("data/geolocation.sqlite")
    cors_origins: str = "http://localhost:5173"

    @property
    def services(self) -> list[str]:
        return [item.strip() for item in self.ranger_services.split(",") if item.strip()]

    @property
    def excluded_users(self) -> list[str]:
        return [item.strip() for item in self.ranger_exclude_users.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
