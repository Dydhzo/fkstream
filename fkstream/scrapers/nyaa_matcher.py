import asyncio
from typing import Dict, List, Any

from .nyaa import NyaaAPI
from fkstream.utils.general import find_best_file_for_episode, normalize_anime_title_for_search
from fkstream.utils.models import Anime, Episode
from fkstream.utils.common_logger import logger

class NyaaMatcher:
    """
    Fait correspondre les torrents de Nyaa à des épisodes d'anime spécifiques.
    """
    def __init__(self):
        self.api = NyaaAPI()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Nettoie la connexion API."""
        if self.api:
            await self.api.close()
            self.api = None

    async def get_torrents_for_episode(self, anime_info: Anime, selected_episode: Episode, user_ip: str = None) -> List[Dict[str, Any]]:
        """
        Récupère et filtre les torrents pour un épisode spécifique d'un anime.
        """
        try:
            normalized_title = normalize_anime_title_for_search(anime_info.name)
            logger.info(f"Recherche de torrents pour '{anime_info.name}' (normalise: '{normalized_title}') episode {selected_episode.number}")
            
            torrents = await self.api.search_torrents(normalized_title, user_ip=user_ip)
            if not torrents:
                logger.warning("Aucun torrent trouve pour le titre de l'anime")
                return []
            
            # Traitement séquentiel pour respecter le rate limiting
            file_lists_from_api = []
            for torrent in torrents:
                try:
                    # Passer le hash du RSS pour optimiser le cache
                    hash = torrent.get('infoHash')
                    file_list = await self.api.get_file_list(torrent['link'], user_ip=user_ip, hash=hash)
                    file_lists_from_api.append(file_list)
                except Exception as e:
                    file_lists_from_api.append(e)
            
            relevant_torrents = []
            for torrent, file_list_result in zip(torrents, file_lists_from_api):
                if isinstance(file_list_result, Exception):
                    logger.warning(f"Echec de la recuperation de la liste des fichiers pour le torrent {torrent.get('title', '')}: {file_list_result}")
                    file_list = [{'name': torrent.get('title', ''), 'size': torrent.get('size', 0)}]
                else:
                    file_list = file_list_result or [{'name': torrent.get('title', ''), 'size': torrent.get('size', 0)}]

                formatted_files = [{
                    "title": file.get("name"),
                    "size": file.get("size", 0),
                    "original_torrent_index": file.get('torrent_index', i)
                } for i, file in enumerate(file_list)]

                episode_info = {"nfo_filename": selected_episode.nfo_filename}
                logger.info(f"Recherche pour l'episode {selected_episode.number}: '{selected_episode.name}' (episode_id: {selected_episode.id.split(':')[-1]})")
                best_file = find_best_file_for_episode(formatted_files, episode_info)

                if best_file:
                    real_torrent_index = best_file.get('original_torrent_index', best_file['index'])
                    logger.info(f"Episode {selected_episode.number} associe au fichier: {best_file['title']} a l'index REEL {real_torrent_index}")
                    
                    torrent['fileIndex'] = real_torrent_index
                    torrent['title'] = best_file['title']
                    relevant_torrents.append(torrent)
                else:
                    logger.warning(f"Aucun fichier approprie trouve pour l'episode {selected_episode.number} (nfo: {selected_episode.nfo_filename}) dans le torrent {torrent.get('title', 'Inconnu')}")
            
            relevant_torrents = [t for t in relevant_torrents if int(t.get('seeders', 0)) > 0]
            relevant_torrents.sort(key=lambda x: int(x.get('seeders', 0)), reverse=True)
            
            logger.info(f"{len(relevant_torrents)} torrents avec des seeders trouves pour l'episode {selected_episode.number}")
            return relevant_torrents
        except Exception as e:
            logger.error(f"Erreur lors de la recherche de torrents pour l'episode: {str(e)}")
            return []
