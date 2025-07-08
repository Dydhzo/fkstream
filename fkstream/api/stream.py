from urllib.parse import quote, unquote
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, FileResponse

from fkstream.utils.models import settings, Anime, Episode
from fkstream.utils.general import config_check, get_client_ip, b64_encode, b64_decode
from fkstream.scrapers.nyaa_matcher import NyaaMatcher
from fkstream.debrid.manager import get_debrid_extension, get_debrid, retrieve_debrid_availability
from fkstream.utils.common_logger import logger
from fkstream.scrapers.fankai import FankaiAPI, get_or_fetch_anime_details
from fkstream.utils.database import DistributedLock, LockAcquisitionError, save_debrid_to_cache
from fkstream.utils.dependencies import get_fankai_api
from fkstream.utils.http_client import HttpClient
from fkstream.utils.size_utils import bytes_to_size

streams = APIRouter()


def _create_stream_item(request: Request, b64config: str, debrid_service: str, debrid_emoji: str, torrent: dict, cached_file: dict = None, media_id: str = None):
    """Crée un dictionnaire représentant un flux (stream)."""
    file_title = torrent['title']
    raw_size = torrent.get('size', 0)
    file_size_str = bytes_to_size(raw_size)
    hash = torrent['infoHash']
    display_title = file_title.split('/')[-1]
    info_line = f"💾 {file_size_str} | 👤 {torrent.get('seeders', 'N/A')}"

    stream_item = {
        "name": f"[{get_debrid_extension(debrid_service)} {debrid_emoji}] FKStream",
        "description": f"📁 {display_title}\n{info_line}",
        "behaviorHints": {
            "bingeGroup": f"fkstream|{hash}",
            "videoSize": raw_size,
            "filename": display_title
        },
    }

    if debrid_service == "torrent":
        stream_item["infoHash"] = hash
        stream_item["fileIdx"] = torrent.get('fileIndex', 0)
    else:
        encoded_filename = quote(display_title, safe='')
        encoded_media_id = b64_encode(media_id)
        stream_item["url"] = f"{request.url.scheme}://{request.url.netloc}/{b64config}/playback/{encoded_media_id}/{hash}/{encoded_filename}"
        
        status_for_log = cached_file.get('status', 'unknown').upper() if cached_file else "UNKNOWN"
        logger.info(f"URL generee {status_for_log}: {stream_item['url']} (Correspondance Nyaa: {file_title})")

    return stream_item


async def _parse_media_id(media_id: str):
    """Analyse et valide le format du media_id."""
    if "fk:" not in media_id:
        return None, None
        
    try:
        parts = media_id.split(":")
        if len(parts) < 3:
            logger.error(f"Format de media_id invalide (attendu 'fk:anime_id:episode_id', recu '{media_id}')")
            return None, None
        anime_id, episode_id = parts[1], parts[2]
        logger.info(f"media_id analyse: {media_id} -> anime_id: {anime_id}, episode_id: {episode_id}")
        return anime_id, episode_id
    except (IndexError, ValueError) as e:
        logger.error(f"Format de media_id invalide: {media_id}, erreur: {e}")
        return None, None


async def _fetch_anime_and_episode_data(fankai_api: FankaiAPI, anime_id: str, episode_id: str, media_id: str):
    """Récupère les données de l'anime et trouve l'épisode sélectionné."""
    anime_data = await get_or_fetch_anime_details(fankai_api, anime_id)
    if not anime_data:
        return None, None

    seasons = anime_data.get("seasons", [])
    videos = []
    for season_idx, season in enumerate(seasons):
        season_number = season.get('season_number', season.get('number', season_idx + 1))
        episodes = season.get('episodes', [])
        for episode in episodes:
            final_season_number = episode.get('season_number', season_number)
            videos.append(Episode(
                id=f"fk:{anime_id}:{episode.get('id')}",
                name=episode.get('title'),
                number=episode.get('episode_number'),
                season_number=final_season_number,
            ))

    anime_info = Anime(
        id=f"fk:{anime_data.get('id')}",
        name=anime_data.get('title'),
        videos=videos
    )
    
    logger.info(f"Anime trouve: {anime_info.name} avec {len(anime_info.videos)} episodes")

    selected_episode = next((ep for ep in anime_info.videos if ep.id == media_id), None)
    
    if not selected_episode:
        selected_episode = next((ep for ep in anime_info.videos if str(ep.id).endswith(f":{episode_id}")), None)

    if not selected_episode:
        logger.error(f"Aucun episode trouve pour media_id: {media_id}, episode_id: {episode_id}")
        return None, None
        
    logger.info(f"Episode selectionne: {selected_episode.name} (S{selected_episode.season_number}E{selected_episode.number})")
    return anime_info, selected_episode


async def _search_torrents(anime_info: Anime, episode: Episode, current_media_id: str, user_ip: str = None):
    """Recherche des torrents en utilisant NyaaMatcher."""
    title = anime_info.name
    season = episode.season_number
    episode_num = episode.number
    
    logger.info(f"📦 Recuperation de nouveaux torrents sur Nyaa pour {title} S{season}E{episode_num}")
    matcher = NyaaMatcher()
    
    nyaa_lock_key = f"nyaa_search_{current_media_id}"
    try:
        async with DistributedLock(nyaa_lock_key):
            nyaa_torrents = await matcher.get_torrents_for_episode(anime_info, episode, user_ip=user_ip)
    except LockAcquisitionError:
        logger.warning(f"Impossible d'acquerir le verrou de recherche Nyaa pour {current_media_id}, recherche ignoree pour eviter la surcharge.")
        nyaa_torrents = []

    logger.info(f"{len(nyaa_torrents)} nouveaux torrents trouves sur Nyaa pour {title} S{season}E{episode_num}")
    return nyaa_torrents


async def _get_cached_files(http_client, nyaa_torrents: list, debrid_service: str, config: dict, request, current_media_id: str, episode: Episode):
    """Récupère les fichiers cachés du service debrid ou crée des fichiers torrent directs."""
    if not nyaa_torrents:
        return []
        
    hashes = [t['infoHash'] for t in nyaa_torrents]
    seeders_map = {t['infoHash']: t.get('seeders', 0) for t in nyaa_torrents}
    tracker_map = {t['infoHash']: 'nyaa.si' for t in nyaa_torrents}
    sources_map = {t['infoHash']: {"filename": t.get('title', f"Episode_{episode.number}")} for t in nyaa_torrents}
    
    if debrid_service == "torrent":
        cached_files = [{"hash": t['infoHash'], "title": t['title'], "size": t.get('size', 0), "status": "magnet"} for t in nyaa_torrents]
    else:
        cached_files = await retrieve_debrid_availability(
            http_client, current_media_id, current_media_id, debrid_service,
            config["debridApiKey"], get_client_ip(request), hashes,
            seeders_map, tracker_map, sources_map
        )
    
    logger.info(f"{len(cached_files)} fichiers caches trouves pour {episode.season_number}E{episode.number}")
    return cached_files


async def _create_streams(request, b64config: str, debrid_service: str, config: dict, nyaa_torrents: list, cached_files: list, current_media_id: str):
    """Crée des éléments de flux à partir des torrents et des fichiers en cache."""
    streams_list = []
    status_map = {f['hash']: f for f in cached_files}

    for torrent in nyaa_torrents:
        hash = torrent['infoHash']
        cached_file_info = status_map.get(hash)
        
        status = cached_file_info.get('status', 'unknown') if cached_file_info else "unknown"

        stream_filter = config.get('streamFilter', 'all')
        if config.get('cachedOnly'):  # Pour la rétrocompatibilité
            stream_filter = 'cached_only'
        
        if stream_filter == "cached_only" and status != "cached":
            continue
        elif stream_filter == "cached_unknown" and status == "downloading":
            continue

        if status == "cached":
            debrid_emoji = "⚡"
        elif status == "downloading":
            debrid_emoji = "⬇️"
        elif status == "magnet":
            debrid_emoji = "🧲"
        else:
            debrid_emoji = "❓"
        
        stream_item = _create_stream_item(request, b64config, debrid_service, debrid_emoji, torrent, cached_file_info, media_id=current_media_id)
        streams_list.append(stream_item)
    
    return streams_list


@streams.get("/stream/{media_type}/{media_id}.json")
@streams.get("/{b64config}/stream/{media_type}/{media_id}.json")
async def stream(request: Request, media_type: str, media_id: str, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """Endpoint principal pour fournir les flux de streaming."""
    config = config_check(b64config)
    if not config:
        return {"streams": []}

    anime_id, episode_id = await _parse_media_id(media_id)
    if not anime_id or not episode_id:
        return {"streams": []}

    http_client = request.app.state.http_client
    debrid_service = config["debridService"]
    current_media_id = f"fk:{anime_id}:{episode_id}"
    
    anime_info, selected_episode = await _fetch_anime_and_episode_data(fankai_api, anime_id, episode_id, media_id)
    if not anime_info or not selected_episode:
        return {"streams": []}
    
    user_ip = get_client_ip(request)
    nyaa_torrents = await _search_torrents(anime_info, selected_episode, current_media_id, user_ip=user_ip)
    if not nyaa_torrents:
        return {"streams": []}
    
    cached_files = await _get_cached_files(http_client, nyaa_torrents, debrid_service, config, request, current_media_id, selected_episode)
    
    streams_list = await _create_streams(request, b64config, debrid_service, config, nyaa_torrents, cached_files, current_media_id)
    
    return {"streams": streams_list}


@streams.get("/{b64config}/playback/{b64_media_id}/{hash}/{filename_or_index:path}")
async def playback(request: Request, b64config: str, b64_media_id: str, hash: str, filename_or_index: str):
    """Gère la lecture du média, génère le lien de téléchargement et met à jour le cache."""
    decoded_param = unquote(filename_or_index)
    try:
        real_media_id = b64_decode(b64_media_id)
    except ValueError:
        logger.error(f"media_id base64 invalide: {b64_media_id}")
        return FileResponse("fkstream/assets/uncached.mp4")
    logger.info(f"Demande de lecture: media_id={real_media_id}, hash={hash}, nom_ou_index_fichier={decoded_param}")
    
    config = config_check(b64config)
    if not config:
        logger.error("Configuration invalide pour la lecture")
        return FileResponse("fkstream/assets/uncached.mp4")

    http_client = request.app.state.http_client
    video_id = f"fk:playback_filename:{decoded_param}"
    
    debrid = get_debrid(
        http_client, video_id, real_media_id, config["debridService"],
        config["debridApiKey"], get_client_ip(request)
    )
    
    download_url = await debrid.generate_download_link(hash, "0", decoded_param, decoded_param, 1, 1)
    
    if real_media_id != "unknown":
        if download_url:
            await save_debrid_to_cache(real_media_id, hash, config["debridService"], "cached")
            logger.info(f"✅ Cache mis a jour: {hash} -> en cache")
        else:
            await save_debrid_to_cache(real_media_id, hash, config["debridService"], "downloading")
            logger.info(f"❌ Cache mis a jour: {hash} -> en telechargement")
    
    if not download_url:
        logger.warning(f"Aucune URL de telechargement generee pour le hash {hash}")
        return FileResponse("fkstream/assets/uncached.mp4")
        
    logger.info(f"URL de telechargement generee pour le hash {hash}")
    return RedirectResponse(download_url, status_code=302)
