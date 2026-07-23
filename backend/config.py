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
    audit_source: str = "solr"
    solr_server: str = "base2.mole4.local"
    solr_port: int = 8995
    solr_collection: str = "ranger_audits"
    solr_verify_ssl: bool = False
    solr_timeout_seconds: int = 90
    kerberos_user: str = "smerchan"
    kerberos_realm: str = "MOLE4.LOCAL"
    kerberos_kdc: str = "base1.mole4.local"
    kerberos_admin_server: str = "base1.mole4.local"
    kerberos_password: str = ""
    kerberos_ccache: Path = Path("data/krb5cc_ranger_solr")
    kerberos_config_file: Path = Path("data/krb5_ranger_solr.conf")
    audit_log_path: Path = Path("data/chat_audit.jsonl")
    geo_csv_path: Path = Path("geolocationDatabaseIPv4.csv")
    geo_db_path: Path = Path("data/geolocation.sqlite")
    cors_origins: str = "http://localhost:5173"
    app_auth_username: str = "smerchan"
    app_auth_password_hash: str = ""
    app_session_secret: str = ""
    app_session_hours: int = 8
    app_cookie_name: str = "ranger_session"
    app_cookie_secure: bool = True
    ssl_certfile: Path = Path("certs/localhost.crt")
    ssl_keyfile: Path = Path("certs/localhost.key")
    server_ip: str = "192.168.1.98"
    ai_gateway_api_url: str = "http://127.0.0.1:4000/v1"
    ai_gateway_token: str = ""
    ai_gateway_models: str = "topito,qwen-local"
    ai_gateway_default_model: str = "topito"
    ai_gateway_timeout_seconds: int = 90

    @property
    def services(self) -> list[str]:
        return [item.strip() for item in self.ranger_services.split(",") if item.strip()]

    @property
    def excluded_users(self) -> list[str]:
        return [item.strip() for item in self.ranger_exclude_users.split(",") if item.strip()]

    @property
    def gateway_models(self) -> list[str]:
        """Aliases publicados por LiteLLM; nunca nombres directos de proveedor."""
        return [item.strip() for item in self.ai_gateway_models.split(",") if item.strip()]

    @property
    def kerberos_principal(self) -> str:
        # El cliente Kerberos del sistema suele conocer su realm predeterminado.
        # En ese caso debemos reproducir exactamente `kinit usuario`. Solo se
        # añade `@REALM` cuando el despliegue lo configura expresamente.
        if self.kerberos_realm.strip():
            return f"{self.kerberos_user}@{self.kerberos_realm.strip()}"
        return self.kerberos_user

    @property
    def solr_select_url(self) -> str:
        return f"https://{self.solr_server}:{self.solr_port}/solr/{self.solr_collection}/select"


@lru_cache
def get_settings() -> Settings:
    return Settings()
