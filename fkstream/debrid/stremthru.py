import asyncio
import re
from urllib.parse import unquote

from RTN import parse
from fkstream.utils.models import settings
from fkstream.utils.general import is_video, find_best_file_for_episode
from fkstream.utils.database import get_debrid_from_cache, save_debrid_to_cache, get_metadata_from_cache, set_metadata_to_cache
from fkstream.utils.logger import logger
from fkstream.utils.magnet_store import get_magnet_link
from fkstream.utils.http_client import HttpClient
from fkstream.scrapers.fankai import FankaiAPI, get_or_fetch_anime_details


class StremThru:
    """
    Client pour interagir avec l'API StremThru, qui agit comme un proxy pour divers services debrid.
    """
    def __init__(self, session: HttpClient, video_id: str, media_only_id: str, token: str, ip: str):
        store, token = self.parse_store_creds(token)
        
        self.default_headers = {
            "X-StremThru-Store-Name": store,
            "X-StremThru-Store-Authorization": f"Bearer {token}",
            "User-Agent": "fkstream"
        }

        self.session = session
        self.base_url = f"{settings.STREMTHRU_URL}/v0/store"
        self.name = f"StremThru-{store}"
        self.real_debrid_name = store
        self.client_ip = ip
        self.sid = video_id
        self.media_only_id = media_only_id

    def parse_store_creds(self, token: str):
        """Analyse les informations d'identification du magasin à partir du jeton."""
        if ":" in token:
            parts = token.split(":", 1)
            return parts[0], parts[1]
        return token, ""

    async def check_premium(self):
        """Vérifie si l'utilisateur a un abonnement premium."""
        try:
            user_req = await self.session.get(f"{self.base_url}/user?client_ip={self.client_ip}", headers=self.default_headers)
            user = user_req.json()
            return user["data"]["subscription_status"] == "premium"
        except Exception as e:
            logger.warning(f"Exception lors de la verification du statut premium sur {self.name}: {e}")
        return False

    async def get_instant(self, magnets: list):
        """Vérifie la disponibilité instantanée d'une liste de magnets."""
        try:
            url = f"{self.base_url}/magnets/check?magnet={','.join(magnets)}&client_ip={self.client_ip}&sid={self.sid}"
            magnet_req = await self.session.get(url, headers=self.default_headers)
            return magnet_req.json()
        except Exception as e:
            logger.warning(f"Exception lors de la verification de la disponibilite instantanee du hash sur {self.name}: {e}")

    async def _process_availability_result(self, torrent: dict, seeders_map: dict, tracker_map: dict, sources_map: dict, cached_anime: dict = None):
        """
        Traite un seul résultat de torrent de la vérification de disponibilité.
        Renvoie simplement le statut brut pour être traité plus tard.
        """
        status = torrent.get("status")
        if not status:
            return []

        return [{
            "hash": torrent["hash"],
            "status": status
        }]


    async def _fetch_availability_in_chunks(self, torrent_hashes: list):
        """Récupère la disponibilité auprès de StremThru par lots pour éviter les limites de longueur d'URL."""
        chunk_size = 50
        chunks = [torrent_hashes[i:i + chunk_size] for i in range(0, len(torrent_hashes), chunk_size)]
        tasks = [self.get_instant(chunk) for chunk in chunks]
        responses = await asyncio.gather(*tasks)
        availability = []
        for response in responses:
            if response and response.get("data", {}).get("items"):
                availability.extend(response["data"]["items"])
        return availability

    async def get_availability(self, torrent_hashes: list, seeders_map: dict, tracker_map: dict, sources_map: dict):
        """Logique principale pour obtenir la disponibilité des torrents, en utilisant le cache et l'API."""
        logger.info(f"🔍 StremThru get_availability - Recherche de {len(torrent_hashes)} torrents")
        if not await self.check_premium(): return []

        cached_files, unknown_hashes = [], []
        
        # Paralléliser les requêtes cache pour améliorer les performances
        cache_tasks = [
            get_debrid_from_cache(self.sid, hash, self.real_debrid_name) 
            for hash in torrent_hashes
        ]
        cache_results = await asyncio.gather(*cache_tasks, return_exceptions=True)
        
        # Traiter les résultats des requêtes cache parallèles
        for i, hash in enumerate(torrent_hashes):
            cached_status = cache_results[i]
            if cached_status and not isinstance(cached_status, Exception):
                status = cached_status["status"]
                logger.info(f"✅ CACHE HIT: {hash} = {status}")
                cached_files.append({"hash": hash, "status": status, "title": "", "size": 0})
            else:
                unknown_hashes.append(hash)
        logger.info(f"✅ CACHE HIT: {len(cached_files)}/{len(torrent_hashes)} torrents trouves en cache.")
        
        newly_processed_files = []
        if unknown_hashes:
            logger.info(f"❓ INCONNU: {len(unknown_hashes)} torrents vraiment inconnus, appel a l'API StremThru.")
            availability_results = await self._fetch_availability_in_chunks(unknown_hashes)
            logger.info(f"🔍 StremThru reponse brute: {availability_results}")

            # Récupérer les métadonnées anime une seule fois pour éviter les requêtes redondantes
            cached_anime = None
            if self.sid and self.sid.startswith("fk:"):
                try:
                    parts = self.sid.split(":")
                    if len(parts) >= 3 and parts[1] != "playback_filename":
                        anime_id = parts[1]
                        fankai_api = FankaiAPI(self.session)
                        cached_anime = await get_or_fetch_anime_details(fankai_api, anime_id)
                        if cached_anime:
                            logger.info(f"📚 StremThru: Metadonnees anime recuperees une seule fois pour {anime_id} (evite {len(availability_results)} requetes redondantes)")
                except Exception as e:
                    logger.warning(f"⚠️ StremThru: Erreur lors de la recuperation des metadonnees anime: {e}")

            process_tasks = [self._process_availability_result(torrent, seeders_map, tracker_map, sources_map, cached_anime) for torrent in availability_results]
            processed_files_list = await asyncio.gather(*process_tasks)
            for file_list in processed_files_list:
                if file_list: newly_processed_files.extend(file_list)

            # Préparer les écritures cache en parallèle pour améliorer les performances
            cache_save_tasks = []
            for file_info in newly_processed_files:
                hash = file_info["hash"]
                api_status = file_info.get("status", "unknown")
                db_status = "cached" if api_status == "cached" else "downloading" if api_status in ["downloading", "queued", "failed"] else None
                if db_status:
                    if "playback_filename" in self.sid:
                        logger.info(f"⏩ Sauvegarde du cache ignoree pour media_id de type playback_filename: {self.sid}")
                        file_info["status"] = db_status
                        continue
                    # Ajouter la tâche de sauvegarde cache à la liste
                    cache_save_tasks.append(save_debrid_to_cache(self.sid, hash, self.real_debrid_name, db_status))
                    logger.info(f"💾 ECRITURE BD: {hash} → {db_status}")
                file_info["status"] = db_status or "unknown"
            
            # Exécuter toutes les écritures cache en parallèle
            if cache_save_tasks:
                await asyncio.gather(*cache_save_tasks, return_exceptions=True)

        final_files = cached_files + newly_processed_files
        logger.log("SCRAPER", f"{self.name}: Trouve {len(final_files)} fichiers valides au total ({len(cached_files)} en cache, {len(newly_processed_files)} nouveaux).")
        return final_files

    async def generate_download_link(self, hash: str, index: str, name: str, torrent_name: str, season: int, episode: int):
        """Génère un lien de téléchargement pour un fichier spécifique d'un torrent."""
        try:
            logger.info(f"🎬 StremThru generate_download_link pour hash: {hash}")
            cached_result = await get_debrid_from_cache(self.sid, hash, self.real_debrid_name)
            cached_status = cached_result["status"] if cached_result else None
            if cached_status: logger.info(f"✅ CACHE HIT: Hash {hash} = {cached_status}")

            magnet, status = await self._handle_magnet_status(hash, await self._get_magnet_status(hash))
            if not magnet:
                if cached_status not in ["downloading", "cached"]:
                    if "playback_filename" not in self.sid:
                        logger.info(f"🔄 ECHEC STREAMING: Le hash {hash} ne fonctionne pas ! Marque comme en telechargement")
                        await save_debrid_to_cache(self.sid, hash, self.real_debrid_name, "downloading")
                        logger.info(f"⬇️ Cache mis a jour: {hash} → en telechargement")
                return status

            target_file = await self._find_target_file(magnet, hash, name, torrent_name, season, episode, index)
            if not target_file:
                logger.warning(f"❌ Aucun fichier cible trouve pour le hash {hash}")
                return

            try:
                link_req = await self.session.post(f"{self.base_url}/link/generate?client_ip={self.client_ip}", json={"link": target_file["link"]}, headers=self.default_headers)
                link = link_req.json()
                if link.get("data", {}).get("link"):
                    logger.info(f"✅ Lien direct genere avec succes pour le hash {hash}")
                    if cached_status != "cached":
                        if "playback_filename" not in self.sid:
                            await save_debrid_to_cache(self.sid, hash, self.real_debrid_name, "cached")
                            logger.info(f"✅ Cache mis a jour: {hash} → en cache (streaming reussi)")
                    return link["data"]["link"]
                else:
                    logger.warning(f"⚠️ Pas de lien direct dans la reponse pour le hash {hash}: {link}")
                    raise Exception("Pas de lien direct disponible")
            except Exception as link_error:
                logger.warning(f"❌ Echec de la generation du lien direct pour le hash {hash}: {link_error}")
                if cached_status not in ["downloading", "cached"]:
                    if "playback_filename" not in self.sid:
                        logger.info(f"🔄 ECHEC STREAMING: Le hash {hash} ne fonctionne pas ! Marque comme en telechargement")
                        await save_debrid_to_cache(self.sid, hash, self.real_debrid_name, "downloading")
                        logger.info(f"⬇️ Cache mis a jour: {hash} → en telechargement")
                if status in ["unknown", "not_cached"]:
                    logger.info(f"🚀 Tentative de demarrage du telechargement pour le hash {hash}")
                    try:
                        await asyncio.sleep(2)
                        magnet_link = get_magnet_link(hash) or f"magnet:?xt=urn:btih:{hash}"
                        status_check = await self.session.get(f"{self.base_url}/magnets/check?magnet={magnet_link}&client_ip={self.client_ip}&sid={self.sid}", headers=self.default_headers)
                        status_response = status_check.json()
                        new_status = status_response["data"]["items"][0]["status"] if status_response.get("data", {}).get("items") else "unknown"
                        logger.info(f"🔄 Nouveau statut apres tentative de telechargement: {new_status}")
                        if new_status == "downloading":
                            logger.info(f"✅ Telechargement demarre pour le hash {hash}")
                            return f"https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4#message=Telechargement en cours pour {hash[:8]}..."
                        else:
                            logger.warning(f"❌ Telechargement non demarre, statut: {new_status}")
                            return None
                    except Exception as download_error:
                        logger.warning(f"❌ Erreur lors du demarrage du telechargement pour le hash {hash}: {download_error}")
                        return None
                else:
                    logger.warning(f"❌ Torrent suppose etre en cache mais lien indisponible pour le hash {hash}")
                    return None
        except Exception as e:
            logger.warning(f"Exception lors de la recuperation du lien de telechargement pour {hash}: {e}")

    async def _find_target_file(self, magnet: dict, hash: str, name: str, torrent_name: str, season: int, episode: int, index: str):
        """Trouve le bon fichier dans la liste des fichiers d'un torrent en fonction des métadonnées."""
        name = unquote(name)
        torrent_name = unquote(torrent_name)
        debrid_files = magnet.get("data", {}).get("files", [])
        if not debrid_files:
            return None

        for file in debrid_files:
            filename = file.get("name", "")
            if not is_video(filename) or "sample" in filename.lower(): continue
            if filename == torrent_name: return file
            filename_parsed = parse(filename)
            file_season = filename_parsed.seasons[0] if filename_parsed.seasons else None
            file_episode = filename_parsed.episodes[0] if filename_parsed.episodes else None
            if season == file_season and episode == file_episode: return file

        files_with_link = [file for file in debrid_files if file.get("link")]
        if not files_with_link: return None

        if self.sid and self.sid.startswith("fk:"):
            logger.info(f"Mode anime StremThru detecte: {self.sid}")
            try:
                parts = self.sid.split(":")
                if len(parts) >= 3:
                    if parts[1] == "playback_filename":
                        target_filename = parts[2]
                        for file in files_with_link:
                            if file.get('name', '').endswith(target_filename): return file
                        for file in files_with_link:
                            if target_filename in file.get('name', ''): return file
                    else:
                        target_episode_id = int(parts[2])
                        logger.info(f"⚠️ StremThru _find_target_file: Recherche de l'episode {target_episode_id} avec nfo_filename")
                        
                        # Récupérer le nfo_filename pour cet épisode
                        nfo_filename = await self._get_nfo_filename_for_episode(parts[1], target_episode_id)
                        if not nfo_filename:
                            logger.warning(f"⚠️ StremThru: Impossible de récupérer nfo_filename pour l'episode {target_episode_id}")
                            return None
                        
                        formatted_files = [{"title": f.get("name", "").split("/")[-1], "size": f.get("size", 0), "original_file": f} for f in files_with_link]
                        episode_info = {"nfo_filename": nfo_filename}
                        best_file = find_best_file_for_episode(formatted_files, episode_info)
                        if best_file: return best_file["original_file"]
            except (ValueError, IndexError):
                pass
        return max(files_with_link, key=lambda x: x.get("size", 0))

    async def _get_nfo_filename_for_episode(self, anime_id: str, episode_id: int):
        """Récupère le nfo_filename pour un épisode donné."""
        try:
            # Utiliser la même fonction que stream.py pour uniformité
            fankai_api = FankaiAPI(self.session)
            
            anime_data = await get_or_fetch_anime_details(fankai_api, anime_id)
            if not anime_data:
                logger.warning(f"❌ StremThru: Impossible de récupérer les métadonnées pour {anime_id}")
                return None
                
            return self._extract_nfo_filename_from_metadata(anime_data, episode_id)
            
        except Exception as e:
            logger.error(f"❌ StremThru: Erreur lors de la récupération du nfo_filename: {e}")
            return None

    def _extract_nfo_filename_from_metadata(self, anime_data: dict, episode_id: int):
        """Extrait le nfo_filename depuis les métadonnées."""
        try:
            seasons = anime_data.get("seasons", [])
            for season in seasons:
                episodes = season.get("episodes", [])
                for episode in episodes:
                    if episode.get("id") == episode_id:
                        return episode.get("nfo_filename")
            return None
        except Exception as e:
            logger.error(f"❌ StremThru: Erreur lors de l'extraction du nfo_filename: {e}")
            return None

    async def _handle_magnet_status(self, hash: str, magnet: dict):
        """Gère les différents statuts d'un lien magnet et retourne un objet magnet mis à jour ou une réponse finale."""
        status = magnet.get("data", {}).get("status")
        logger.info(f"🎬 Statut du magnet StremThru: {status} pour le hash {hash}")

        if status in ["cached", "downloaded"]:
            logger.info(f"✅ Torrent pret pour le streaming: {hash} (statut: {status})")
            return magnet, status
        if status == "downloading":
            logger.info(f"⏬ Le torrent est en cours de telechargement: {hash}")
            return None, None
        if status == "failed":
            logger.warning(f"❌ Le telechargement a echoue pour le hash: {hash}")
            return None, None
        if status == "queued":
            logger.info(f"📋 Le torrent est en file d'attente: {hash}. Nouvelle verification des fichiers.")
            try:
                magnet_link = get_magnet_link(hash) or f"magnet:?xt=urn:btih:{hash}"
                recheck_response = await self.session.get(f"{self.base_url}/magnets/check?magnet={magnet_link}&client_ip={self.client_ip}&sid={self.sid}", headers=self.default_headers)
                recheck_data = recheck_response.json()
                if recheck_data.get("data", {}).get("items") and recheck_data["data"]["items"][0].get("files"):
                    magnet["data"]["files"] = recheck_data["data"]["items"][0]["files"]
                    logger.info(f"✅ Les fichiers sont maintenant disponibles pour le torrent en file d'attente: {hash}")
                    return magnet, status
                else:
                    logger.info(f"⏳ Les fichiers ne sont pas encore disponibles pour le torrent en file d'attente: {hash}")
                    return None, None
            except Exception as e:
                logger.warning(f"❌ Erreur lors de la nouvelle verification pour le torrent en file d'attente {hash}: {e}")
                return None, None
        if status == "unknown":
            logger.info(f"🤔 Le statut est inconnu pour {hash}. Forcage de l'analyse avec interrogation.")
            try:
                for i in range(10):
                    magnet_link = get_magnet_link(hash) or f"magnet:?xt=urn:btih:{hash}"
                    recheck_response = await self.session.get(f"{self.base_url}/magnets/check?magnet={magnet_link}&client_ip={self.client_ip}&sid={self.sid}", headers=self.default_headers)
                    recheck_data = recheck_response.json()
                    if recheck_data.get("data", {}).get("items") and recheck_data["data"]["items"][0].get("files"):
                        magnet["data"]["files"] = recheck_data["data"]["items"][0]["files"]
                        logger.info(f"✅ Les fichiers sont maintenant disponibles pour le torrent inconnu apres {i+1}s: {hash}")
                        return magnet, status
                    await asyncio.sleep(1)
                logger.warning(f"❌ L'interrogation a expire apres 10s pour {hash}. Aucun fichier trouve.")
                return None, None
            except Exception as e:
                logger.warning(f"❌ Erreur lors de l'interrogation pour le torrent inconnu {hash}: {e}")
                return None, None
        
        logger.warning(f"❓ Statut non gere '{status}' pour le hash {hash}")
        return magnet, status

    async def _get_magnet_status(self, hash: str):
        """Obtient le statut initial d'un lien magnet auprès de StremThru."""
        magnet_link = get_magnet_link(hash) or f"magnet:?xt=urn:btih:{hash}"
        if get_magnet_link(hash):
            logger.info(f"✅ Utilisation du lien magnet avec {magnet_link.count('&tr=')} traqueurs reels pour le hash {hash}")
        else:
            logger.info(f"⚠️ Utilisation du lien magnet de secours sans traqueurs pour le hash {hash}")
        
        magnet_response = await self.session.post(f"{self.base_url}/magnets?client_ip={self.client_ip}", json={"magnet": magnet_link}, headers=self.default_headers)
        return magnet_response.json()
