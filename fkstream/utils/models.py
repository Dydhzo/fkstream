from typing import List, Optional
from databases import Database
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """
    Paramètres de l'application, chargés à partir des variables d'environnement.
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ADDON_ID: Optional[str] = "community.fkstream"
    ADDON_NAME: Optional[str] = "FKStream"
    FASTAPI_HOST: Optional[str] = "0.0.0.0"
    FASTAPI_PORT: Optional[int] = 8000
    FASTAPI_WORKERS: Optional[int] = 1
    USE_GUNICORN: Optional[bool] = True
    DATABASE_TYPE: Optional[str] = "sqlite"
    DATABASE_URL: Optional[str] = "username:password@hostname:port"
    DATABASE_PATH: Optional[str] = "data/fkstream.db"
    METADATA_TTL: Optional[int] = 86400  # 1 jour
    DEBRID_AVAILABILITY_TTL: Optional[int] = 86400  # 1 jour
    SCRAPE_LOCK_TTL: Optional[int] = 300  # 5 minutes
    SCRAPE_WAIT_TIMEOUT: Optional[int] = 30  # 30 secondes
    DEBRID_PROXY_URL: Optional[str] = None
    GET_TORRENT_TIMEOUT: Optional[int] = 20 # 20 secondes
    NYAA_RATE_LIMIT_DELAY: Optional[int] = 1 # 1 seconde
    CUSTOM_HEADER_HTML: Optional[str] = None
    PROXY_DEBRID_STREAM: Optional[bool] = False
    PROXY_DEBRID_STREAM_DEBRID_DEFAULT_SERVICE: Optional[str] = "realdebrid"
    PROXY_DEBRID_STREAM_DEBRID_DEFAULT_APIKEY: Optional[str] = None
    PROXY_DEBRID_STREAM_PASSWORD: Optional[str] = None
    STREMTHRU_URL: Optional[str] = "https://stremthru.13377001.xyz"
    LOG_LEVEL: Optional[str] = "DEBUG"

    @field_validator("STREMTHRU_URL")
    def remove_trailing_slash(cls, v):
        if v and v.endswith("/"):
            return v[:-1]
        return v


settings = AppSettings()

DEBRID_SERVICES = [
    "realdebrid", "alldebrid", "premiumize", "torbox", "easydebrid",
    "debridlink", "offcloud", "pikpak", "torrent",
]


class ConfigModel(BaseModel):
    """
    Modèle pour la configuration utilisateur fournie via l'URL.
    """
    streamFilter: Optional[str] = "all"
    debridService: Optional[str] = "torrent"
    debridApiKey: Optional[str] = ""
    debridStreamProxyPassword: Optional[str] = ""
    maxActorsDisplay: Optional[str] = "all"

    @field_validator("debridService")
    def check_debrid_service(cls, v):
        if v not in DEBRID_SERVICES:
            raise ValueError(f"Service debrid invalide. Doit être l'un des suivants: {DEBRID_SERVICES}")
        return v
    
    @field_validator("streamFilter")
    def check_stream_filter(cls, v):
        valid_filters = ["all", "cached_only", "cached_unknown"]
        if v not in valid_filters:
            raise ValueError(f"Filtre de flux invalide. Doit être l'un des suivants: {valid_filters}")
        return v
    
    @field_validator("maxActorsDisplay")
    def check_max_actors_display(cls, v):
        valid_options = ["5", "10", "15", "all"]
        if v not in valid_options:
            raise ValueError(f"Option d'affichage max d'acteurs invalide. Doit être l'une des suivantes: {valid_options}")
        return v


default_config = ConfigModel().model_dump()

web_config = {
    "debridServices": DEBRID_SERVICES,
    "maxActorsDisplayOptions": ["5", "10", "15", "all"]
}

database_url = settings.DATABASE_PATH if settings.DATABASE_TYPE == "sqlite" else settings.DATABASE_URL
database = Database(f"{'sqlite' if settings.DATABASE_TYPE == 'sqlite' else 'postgresql+asyncpg'}://{'/' if settings.DATABASE_TYPE == 'sqlite' else ''}{database_url}")


class Episode(BaseModel):
    """Modèle de données pour un épisode."""
    id: str
    name: str
    number: Optional[int] = None
    season_number: Optional[int] = None
    nfo_filename: Optional[str] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    created_at: Optional[str] = None
    released: Optional[str] = None


class Anime(BaseModel):
    """Modèle de données pour un anime, incluant une liste d'épisodes."""
    id: str
    name: str
    poster: Optional[str] = None
    background: Optional[str] = None
    description: Optional[str] = None
    kaieur: Optional[str] = None
    duration: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    videos: List[Episode] = []
