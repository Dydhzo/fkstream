import orjson

from .models import ConfigModel, default_config, settings
from .general import b64_decode
from .common_logger import logger


def validate_config(b64config: str) -> dict:
    """
    Valide et traite la configuration encodée en base64.
    Centralise la logique des deux modules API.
    """
    try:
        config = orjson.loads(b64_decode(b64config))

        if "indexers" in config:
            return False

        validated_config = ConfigModel(**config).model_dump()

        if (
            settings.PROXY_DEBRID_STREAM
            and settings.PROXY_DEBRID_STREAM_PASSWORD
            and settings.PROXY_DEBRID_STREAM_PASSWORD == validated_config.get("debridStreamProxyPassword")
            and not validated_config.get("debridApiKey")
        ):
            validated_config["debridService"] = settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_SERVICE
            validated_config["debridApiKey"] = settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_APIKEY

        return validated_config
    except (ValueError, TypeError, KeyError) as e:
        logger.warning(f"Configuration utilisateur invalide: {e}. Retour a la configuration par defaut.")
        return default_config
    except Exception as e:
        logger.error(f"Erreur inattendue lors de la validation de la configuration: {e}. Retour a la configuration par defaut.")
        return default_config


def is_valid_config(b64config: str) -> bool:
    """Vérification rapide si la configuration est valide sans traitement complet."""
    try:
        if not b64config:
            return False
        config = orjson.loads(b64_decode(b64config))
        return "indexers" not in config
    except Exception:
        return False
