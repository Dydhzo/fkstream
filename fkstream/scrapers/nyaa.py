from typing import Dict, List, Any, Optional, Tuple
import feedparser
from urllib.parse import quote
import re
import bencode
import hashlib

from fkstream.utils.general import is_video
from fkstream.utils.http_client import HttpClient
from fkstream.utils.size_utils import bytes_to_size, parse_size_to_bytes
from fkstream.utils.http_constants import DEFAULT_USER_AGENT
from fkstream.utils.database import get_torrent_from_cache, save_torrent_to_cache
from fkstream.utils.common_logger import logger
from fkstream.utils.base_client import BaseClient
from fkstream.utils.magnet_store import store_magnet_link
from fkstream.utils.rate_limiter import nyaa_rate_limiter
from fkstream.utils.models import settings

class NyaaAPI(BaseClient):
    """API pour interagir avec Nyaa.si."""
    
    def __init__(self):
        super().__init__()
        self.base_url = "https://nyaa.si"
        self.client = HttpClient(base_url=self.base_url, timeout=settings.GET_TORRENT_TIMEOUT, user_agent=DEFAULT_USER_AGENT)
    
    async def search_torrents(self, query: str, user: str = "Fan-Kai", user_ip: str = None) -> List[Dict[str, Any]]:
        """Recherche des torrents sur Nyaa.si en utilisant le flux RSS."""
        # Rate limiting par IP utilisateur
        if user_ip:
            await nyaa_rate_limiter.wait_for_user(user_ip)
        
        try:
            encoded_user = quote(user)
            encoded_query = quote(query)
            rss_url = f"{self.base_url}/?page=rss&u={encoded_user}&q={encoded_query}"
            
            logger.info(f"Recherche sur Nyaa.si: {rss_url}")
            
            response = await self.client.get(rss_url)
            response.raise_for_status()
            
            feed = feedparser.parse(response.text)
            torrents = [torrent for item in feed.entries if (torrent := self._parse_rss_item(item))]
            
            logger.info(f"{len(torrents)} torrents finaux pour la requete: {query}")
            return torrents
        except Exception as e:
            error_type = type(e).__name__
            if "HTTPStatusError" in error_type:
                logger.error(f"Erreur HTTP lors de la recherche sur Nyaa.si: {getattr(e, 'response', {}).get('status_code', 'inconnu')}")
            elif "RequestError" in error_type:
                logger.error(f"Erreur de requete lors de la recherche sur Nyaa.si: {str(e)}")
            else:
                logger.error(f"Erreur lors de la recherche sur Nyaa.si: {str(e)}")
            return []
    
    def _parse_rss_item(self, item) -> Optional[Dict[str, Any]]:
        """Analyse un élément RSS pour en extraire les données du torrent."""
        try:
            hash = getattr(item, 'nyaa_infohash', None)
            if not hash or not re.match(r'^[a-fA-F0-9]{40}$', hash):
                logger.warning(f"Format de hash invalide pour {item.title}: {hash}")
                return None

            return {
                'infoHash': hash,
                'title': item.title,
                'size': parse_size_to_bytes(getattr(item, 'nyaa_size', '0 MB')),
                'seeders': item.get('nyaa_seeders', 'N/A'),
                'link': item.link,
                'page_link': item.guid,
            }
        except Exception as e:
            title = getattr(item, 'title', 'titre inconnu')
            logger.warning(f"Impossible d'analyser l'element RSS Nyaa '{title}': {e}")
            return None

    async def get_file_list(self, torrent_url: str, user_ip: str = None, hash: str = None) -> List[Dict[str, Any]]:
        """Récupère la liste des fichiers d'un torrent en analysant le fichier .torrent."""
        # Si on a le hash, vérifier le cache d'abord AVANT le rate limiting et le téléchargement
        if hash:
            try:
                cached_data = await get_torrent_from_cache(hash)
                if cached_data:
                    logger.info(f"✅ TORRENT CACHE HIT: {hash} - Utilisation des donnees en cache")
                    # Recréer et stocker le lien magnet depuis le cache
                    tracker_params = ''.join([f"&tr={quote(tracker)}" for tracker in cached_data['trackers']])
                    # On n'a pas le titre ici, mais ce n'est pas critique pour le magnet
                    magnet_link = f"magnet:?xt=urn:btih:{hash.lower()}{tracker_params}"
                    store_magnet_link(hash, magnet_link)
                    logger.info(f"{len(cached_data['files'])} fichiers trouves dans le cache (pas de telechargement necessaire)")
                    return cached_data['files']
            except Exception as e:
                logger.warning(f"Erreur lors de la verification du cache pour {hash}: {e}")
        
        # Rate limiting par IP utilisateur SEULEMENT si on doit télécharger
        if user_ip:
            await nyaa_rate_limiter.wait_for_user(user_ip)
        
        try:
            logger.info(f"Recuperation de la liste des fichiers depuis: {torrent_url}")
            torrent_file_list, _ = await self._get_file_list_from_torrent(torrent_url, expected_hash=hash)
            
            if torrent_file_list:
                logger.info(f"{len(torrent_file_list)} fichiers trouves dans le torrent")
                return torrent_file_list
            else:
                logger.warning("Echec de l'analyse du fichier torrent, aucune liste de fichiers disponible")
                return []
        except Exception as e:
            error_type = type(e).__name__
            if "HTTPStatusError" in error_type:
                logger.error(f"Erreur HTTP lors de la recuperation de la liste des fichiers: {getattr(e, 'response', {}).get('status_code', 'inconnu')}")
            elif "RequestError" in error_type:
                logger.error(f"Erreur de requete lors de la recuperation de la liste des fichiers: {str(e)}")
            else:
                logger.error(f"Erreur lors de la recuperation de la liste des fichiers: {str(e)}")
            return []

    async def _get_file_list_from_torrent(self, torrent_download_url: str, expected_hash: str = None) -> Tuple[List[Dict[str, Any]], str]:
        """Télécharge et analyse le fichier .torrent pour obtenir la liste des fichiers."""
        try:
            logger.info(f"📦 TORRENT CACHE MISS: {expected_hash or 'unknown'} - Telechargement necessaire")
            torrent_response = await self.client.get(torrent_download_url)
            torrent_response.raise_for_status()
            
            torrent_data = bencode.decode(torrent_response.content)
            if 'info' not in torrent_data:
                logger.warning("Fichier torrent invalide - pas de section 'info'")
                return [], ""
            
            info = torrent_data['info']
            hash = hashlib.sha1(bencode.encode(info)).hexdigest()
            
            # Vérifier que le hash correspond à celui attendu du RSS
            if expected_hash and hash != expected_hash:
                logger.warning(f"Hash du torrent ne correspond pas: attendu {expected_hash}, obtenu {hash}")
            
            def decode_if_bytes(val):
                return val.decode(errors='ignore') if isinstance(val, bytes) else str(val)

            announce_list = [decode_if_bytes(t) for tier in torrent_data.get('announce-list', []) for t in tier]
            if 'announce' in torrent_data:
                announce_list.append(decode_if_bytes(torrent_data['announce']))

            title_raw = info.get('name', 'Unknown')
            title = decode_if_bytes(title_raw)
            tracker_params = ''.join([f"&tr={quote(tracker)}" for tracker in announce_list])
            magnet_link = f"magnet:?xt=urn:btih:{hash.lower()}&dn={quote(title)}{tracker_params}"
            store_magnet_link(hash, magnet_link)
            logger.info(f"Lien magnet cree et stocke avec {len(announce_list)} traqueurs reels depuis le torrent")
            
            files = []
            if 'files' in info:
                for i, file_info in enumerate(info['files']):
                    file_path = '/'.join(decode_if_bytes(p) for p in file_info['path'])
                    if is_video(file_path):
                        files.append({'name': file_path, 'size': file_info['length'], 'torrent_index': i})
            else:
                if is_video(title):
                    files.append({'name': title, 'size': info['length'], 'torrent_index': 0})
            
            await save_torrent_to_cache(hash, files, announce_list)
            logger.info(f"{len(files)} fichiers video analyses depuis le fichier torrent")
            return files, hash
        except bencode.BencodeDecodeError as e:
            logger.error(f"Le decodage Bencode a echoue pour le fichier torrent: {e}")
            return [], ""
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'analyse du fichier torrent: {str(e)}")
            return [], ""
