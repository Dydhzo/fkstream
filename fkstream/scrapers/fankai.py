from typing import List, Optional, Dict, Any

from fkstream.utils.http_client import HttpClient
from fkstream.utils.database import get_metadata_from_cache, set_metadata_to_cache, DistributedLock, LockAcquisitionError
from fkstream.utils.common_logger import logger
from fkstream.utils.base_client import BaseClient


async def _fetch_complete_anime_data(fankai_api: "FankaiAPI", anime_id: str) -> dict:
    """Fonction d'aide pour récupérer les données complètes d'un anime depuis l'API."""
    anime_data = await fankai_api.get_series_details(anime_id)
    if not anime_data:
        return None

    seasons = await fankai_api.get_seasons(anime_id)
    for season in seasons:
        season_episodes = await fankai_api.get_episodes(str(season.get("id")))
        season["episodes"] = season_episodes
    
    actors = await fankai_api.get_actors(anime_id)
    
    anime_data["seasons"] = seasons
    anime_data["actors"] = actors
    return anime_data


class FankaiAPI(BaseClient):
    """Client pour l'API metadata.fankai.fr."""
    
    def __init__(self, client: HttpClient):
        super().__init__()
        self.base_url = "https://metadata.fankai.fr"
        self.client = client

    async def get_all_series(self) -> List[Dict[str, Any]]:
        """Récupère la liste complète de toutes les séries."""
        try:
            logger.info("Recuperation de toutes les series depuis l'API Fankai Metadata...")
            response = await self.client.get(f"{self.base_url}/series?paginate=false")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Echec de la recuperation de toutes les series: {e}")
            return []

    async def get_series_details(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les détails d'une série par son ID."""
        try:
            logger.info(f"Recuperation des details pour la serie ID: {series_id}")
            response = await self.client.get(f"{self.base_url}/series/{series_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Echec de la recuperation des details pour la serie ID {series_id}: {e}")
            return None

    async def get_seasons(self, series_id: str) -> List[Dict[str, Any]]:
        """Récupère les saisons d'une série."""
        try:
            logger.info(f"Recuperation des saisons pour la serie ID: {series_id}")
            response = await self.client.get(f"{self.base_url}/series/{series_id}/seasons")
            response.raise_for_status()
            return response.json().get("seasons", [])
        except Exception as e:
            logger.error(f"Echec de la recuperation des saisons pour l'ID {series_id}: {e}")
            return []

    async def get_episodes(self, season_id: str) -> List[Dict[str, Any]]:
        """Récupère les épisodes d'une saison."""
        try:
            logger.info(f"Recuperation des episodes pour la saison ID: {season_id}")
            response = await self.client.get(f"{self.base_url}/seasons/{season_id}/episodes")
            response.raise_for_status()
            return response.json().get("episodes", [])
        except Exception as e:
            logger.error(f"Echec de la recuperation des episodes pour l'ID {season_id}: {e}")
            return []

    async def get_actors(self, series_id: str) -> List[Dict[str, Any]]:
        """Récupère les acteurs d'une série."""
        try:
            logger.info(f"Recuperation des acteurs pour la serie ID: {series_id}")
            response = await self.client.get(f"{self.base_url}/series/{series_id}/actors")
            response.raise_for_status()
            return response.json().get("actors", [])
        except Exception as e:
            logger.error(f"Echec de la recuperation des acteurs pour l'ID {series_id}: {e}")
            return []


async def get_or_fetch_anime_details(fankai_api: "FankaiAPI", anime_id: str) -> Optional[Dict[str, Any]]:
    """
    Obtient les détails d'un anime depuis le cache si disponible, sinon les récupère
    depuis l'API Fankai, gère le verrouillage pour éviter les conditions de concurrence,
    et met le résultat en cache.
    """
    media_id = f"fk:{anime_id}"
    cached_anime = await get_metadata_from_cache(media_id)

    if cached_anime:
        logger.info(f"✅ CACHE HIT: {media_id}")
        return cached_anime

    lock_key = f"metadata_fetch_{anime_id}"
    try:
        async with DistributedLock(lock_key):
            cached_anime = await get_metadata_from_cache(media_id)
            if cached_anime:
                logger.info(f"✅ CACHE HIT apres verrou: {media_id}")
                return cached_anime

            logger.info(f"📦 CACHE MISS: {media_id} - Recuperation depuis l'API avec verrou")
            anime_data = await _fetch_complete_anime_data(fankai_api, anime_id)
            if anime_data:
                await set_metadata_to_cache(media_id, anime_data)
            return anime_data
            
    except LockAcquisitionError:
        logger.warning(f"Impossible d'acquerir le verrou de metadonnees pour {anime_id}, nouvelle tentative sans verrou.")
        anime_data = await _fetch_complete_anime_data(fankai_api, anime_id)
        if anime_data:
            await set_metadata_to_cache(media_id, anime_data)
        return anime_data
    except Exception as e:
        logger.error(f"Une erreur inattendue s'est produite lors de la recuperation des details de l'anime pour {anime_id}: {e}")
        return None
