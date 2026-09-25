"""Configuración centralizada y externalizable para cada entorno Cloudera."""

from functools import lru_cache
import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Contrato de despliegue: secretos, alcance, límites y rutas operativas."""
    # `.env.cml` tiene prioridad sobre la configuración local histórica.
    model_config = SettingsConfigDict(env_file=(".env", ".env.cml"), extra="ignore")

    ranger_url: str = "https://go01-aw-dl-gateway.go01-dem.ylcu-atmi.cloudera.site/go01-aw-dl/cdp-proxy/ranger"
    ranger_auth_type: str = "basic"
    ranger_token: str = ""
    ranger_user: str = "admin"
    ranger_password: str = ""
    ranger_verify_ssl: bool = False
    ranger_services: str = "cm_hdfs,cm_knox,cm_atlas,Hadoop SQL"
    ranger_audit_page_size: int = 5000
    ranger_timeout_seconds: int = 60
    ranger_exclude_users: str = "hdfs,hive,impala,kafka,nifi,spark"
    audit_source: str = "ranger"
    solr_server: str = "base2.mole4.local"
    solr_port: int = 8995
    solr_collection: str = "ranger_audits"
    # URL completa opcional. Es la forma recomendada para Knox, por ejemplo:
    # https://gateway:8443/gateway/cdp-proxy-api/solr/ranger_audits/select
    solr_url: str = ""
    solr_auth_type: str = "none"
    solr_user: str = ""
    solr_password: str = ""
    solr_verify_ssl: bool = False
    solr_timeout_seconds: int = 90
    kerberos_enabled: bool = False
    kerberos_user: str = "smerchan"
    kerberos_realm: str = "MOLE4.LOCAL"
    kerberos_kdc: str = "base1.mole4.local"
    kerberos_admin_server: str = "base1.mole4.local"
    kerberos_password: str = ""
    kerberos_keytab: Path | None = None
    kerberos_ccache: Path = Path("data/krb5cc_ranger_solr")
    kerberos_config_file: Path = Path("data/krb5_ranger_solr.conf")
    audit_log_path: Path = Path("data/chat_audit.jsonl")
    geo_csv_path: Path = Path("geolocationDatabaseIPv4.csv")
    geo_db_path: Path = Path("data/geolocation.sqlite")
    cors_origins: str = "http://localhost:5173"
    app_auth_username: str = "admin"
    # Hash scrypt de la contraseña inicial "admin". Puede sustituirse de forma
    # segura mediante APP_AUTH_PASSWORD_HASH en el entorno de despliegue.
    app_auth_password_hash: str = "scrypt$16384$8$1$xXHnI-7u_69KeD3gwjwGAw$q1fzMkffjOWk8dRHX1KyZ5F9r7rpGeJCxYgEzrBlas4"
    app_session_secret: str = ""
    app_session_hours: int = 8
    app_cookie_name: str = "ranger_session"
    app_cookie_secure: bool = True
    ssl_certfile: Path = Path("certs/localhost.crt")
    ssl_keyfile: Path = Path("certs/localhost.key")
    server_ip: str = "127.0.0.1"
    ai_gateway_api_url: str = "https://ml-64288d82-5dd.go01-dem.ylcu-atmi.cloudera.site/namespaces/serving-default/endpoints/mpark-nemotron/v1"
    ai_gateway_token: str = ""
    cdp_token: str = ""
    use_cml_jwt: bool = True
    cml_jwt_path: Path = Path("/tmp/jwt")
    ai_gateway_models: str = "nvidia/nemotron-3-nano"
    ai_gateway_default_model: str = "nvidia/nemotron-3-nano"
    ai_gateway_timeout_seconds: int = 90
    agent_provider: str = "cloudera"
    cloudera_ai_api_url: str = ""
    cloudera_ai_token: str = ""
    cloudera_ai_models: str = ""
    cloudera_ai_default_model: str = ""

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
    def cloudera_models(self) -> list[str]:
        return [item.strip() for item in self.cloudera_ai_models.split(",") if item.strip()]

    @property
    def effective_ai_token(self) -> tuple[str, str]:
        """Token y procedencia, sin exponer el valor fuera del backend."""
        if self.cdp_token.strip():
            return self.cdp_token.strip(), "cdp_token"
        if self.use_cml_jwt and self.cml_jwt_path.is_file():
            try:
                token = str(json.loads(self.cml_jwt_path.read_text(encoding="utf-8")).get("access_token") or "").strip()
                if token:
                    return token, "cml_jwt"
            except (OSError, ValueError, TypeError):
                pass
        if self.ai_gateway_token.strip():
            return self.ai_gateway_token.strip(), "api_key"
        return "", "none"

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
        configured = self.solr_url.strip().split("#", 1)[0].rstrip("/")
        if not configured:
            return f"https://{self.solr_server}:{self.solr_port}/solr/{self.solr_collection}/select"
        if configured.endswith("/select"):
            return configured
        if configured.endswith("/solr"):
            return f"{configured}/{self.solr_collection}/select"
        if configured.endswith("/cdp-proxy-api"):
            return f"{configured}/solr/{self.solr_collection}/select"
        return f"{configured}/select"

    @property
    def effective_solr_auth_type(self) -> str:
        """Conserva compatibilidad con despliegues que solo usaban el flag Kerberos."""
        return "kerberos" if self.kerberos_enabled else self.solr_auth_type.strip().casefold()


@lru_cache
def get_settings() -> Settings:
    return Settings()
