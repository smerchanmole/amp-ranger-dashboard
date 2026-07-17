from backend.config import get_settings
from backend.geolocation import GeoDatabase

settings = get_settings()
database = GeoDatabase(settings.geo_csv_path, settings.geo_db_path)
print(f"Indexadas {database.build():,} redes IPv4 en {settings.geo_db_path}")
