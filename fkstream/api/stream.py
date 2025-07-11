# fkstream/api/stream.py
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request

from fkstream.debrid.manager import get_debrid_extension
from fkstream.scrapers.fankai import FankaiAPI, get_or_fetch_anime_details
from fkstream.utils.common_logger import logger
from fkstream.utils.dependencies import get_fankai_api
from fkstream.utils.general import b64_encode, config_check
from fkstream.utils.models import Anime, Episode
from fkstream.utils.stream_utils import (bytes_to_size,
                                         find_best_file_for_episode)

from fastapi.responses import RedirectResponse, FileResponse
from fkstream.utils.general import get_client_ip, b64_decode
from fkstream.debrid.stremthru import StremThru
from fkstream.debrid.manager import build_stremthru_token

# --- Définition du routeur ---
streams = APIRouter()


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
                nfo_filename=episode.get('nfo_filename'),
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

def _create_stream_item(request: Request, b64config: str, debrid_service: str, debrid_emoji: str, torrent: dict, media_id: str):
    """Crée un dictionnaire représentant un flux (stream)."""
    file_title = torrent['title']
    hash_val = torrent['infoHash']
    display_title = file_title.split('/')[-1]
    raw_size = torrent.get('size', 0)


    stream_item = {
        "name": f"[{get_debrid_extension(debrid_service)} {debrid_emoji}] FKStream",
        "description": f"📁 {display_title}",
        "behaviorHints": {
            "bingeGroup": f"fkstream|{hash_val}",
            "filename": display_title,
            "videoSize": raw_size
        },
    }

    if debrid_service == "torrent":
        stream_item["infoHash"] = hash_val
        stream_item["fileIdx"] = torrent.get('fileIndex')
    else:
        encoded_filename = quote(display_title, safe='')
        encoded_media_id = b64_encode(media_id)
        stream_item["url"] = f"{request.url.scheme}://{request.url.netloc}/{b64config}/playback/{encoded_media_id}/{hash_val}/{torrent.get('fileIndex')}/{encoded_filename}"
        
        logger.info(f"URL generee ({debrid_emoji}): {stream_item['url']} (Source: Dataset, Fichier: {file_title})")

    return stream_item


async def _create_streams_with_status(request, b64config: str, config: dict, torrents: list, cached_files: list, media_id: str):
    """Crée des éléments de flux en fonction du statut de disponibilité debrid."""
    streams_list = []
    status_map = {f['hash']: f for f in cached_files}
    debrid_service = config.get("debridService", "torrent")

    for torrent in torrents:
        hash_val = torrent['infoHash']
        cached_info = status_map.get(hash_val)
        
        status = cached_info.get('status', 'unknown') if cached_info else "unknown"

        # Logique des emojis en fonction du statut
        if status == "cached":
            debrid_emoji = "⚡"
        elif status in ["downloading", "queued"]:
            debrid_emoji = "⬇️"
        elif status == "magnet":
            debrid_emoji = "🧲"
        elif status == "failed":
            debrid_emoji = "❌"
        else:
            debrid_emoji = "❓"

        stream_item = _create_stream_item(request, b64config, debrid_service, debrid_emoji, torrent, media_id)
        streams_list.append(stream_item)
    
    return streams_list

# --- Route principale de streaming ---

@streams.get("/stream/{media_type}/{media_id}.json")
@streams.get("/{b64config}/stream/{media_type}/{media_id}.json")
async def stream(request: Request, media_type: str, media_id: str, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """Fournit les flux de streaming en vérifiant la disponibilité debrid au préalable."""
    config = config_check(b64config)
    if not config:
        return {"streams": []}

    anime_id, episode_id = await _parse_media_id(media_id)
    if not anime_id or not episode_id:
        return {"streams": []}

    anime_info, selected_episode = await _fetch_anime_and_episode_data(fankai_api, anime_id, episode_id, media_id)
    if not anime_info or not selected_episode:
        return {"streams": []}


    
    dataset = request.app.state.dataset.get('top', [])
    target_anime_data = next((item for item in dataset if str(item.get('api_id')) == anime_id), None)

    if not target_anime_data:
        logger.warning(f"Anime avec api_id {anime_id} non trouvé dans le dataset local.")
        return {"streams": []}

    logger.info(f"Anime trouvé dans dataset: '{target_anime_data.get('name')}' pour épisode '{selected_episode.name}'")
    

    all_torrents_for_episode = []
    for source in target_anime_data.get('sources', []):
        magnet = source.get('magnet')
        files_in_torrent = source.get('files', [])
        if not magnet or not files_in_torrent:
            continue

        files_for_matching = [{"title": f} for f in files_in_torrent]
        best_file = await find_best_file_for_episode(request, files_for_matching, selected_episode)

        if best_file:
            try:
                file_index = files_in_torrent.index(best_file['title'])
                info_hash_match = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
                if info_hash_match:
                    all_torrents_for_episode.append({
                        'infoHash': info_hash_match.group(1).lower(),
                        'title': best_file['title'],
                        'fileIndex': file_index,
                        'size': source.get('size', 0),
                        'seeders': source.get('seeders') 
                    })
            except (ValueError, AttributeError):
                continue
    
    if not all_torrents_for_episode:
        logger.warning(f"Aucun torrent correspondant trouvé pour {media_id} dans le dataset.")
        return {"streams": []}


    debrid_service = config.get("debridService", "torrent")
    cached_files = []
    if debrid_service != "torrent":
        hashes = [t['infoHash'] for t in all_torrents_for_episode]
        
        http_client = request.app.state.http_client
        stremthru_token = build_stremthru_token(debrid_service, config["debridApiKey"])
        debrid_instance = StremThru(
            session=http_client,
            video_id=media_id,
            media_only_id=anime_id,
            token=stremthru_token,
            ip=get_client_ip(request)
        )
        

        seeders_map = {t['infoHash']: t.get('seeders', 0) for t in all_torrents_for_episode}
        tracker_map = {t['infoHash']: 'dataset' for t in all_torrents_for_episode}
        sources_map = {t['infoHash']: {"filename": t.get('title')} for t in all_torrents_for_episode}
        
        cached_files = await debrid_instance.get_availability(hashes, seeders_map, tracker_map, sources_map)

    streams_list = await _create_streams_with_status(request, b64config, config, all_torrents_for_episode, cached_files, media_id)
    
    return {"streams": streams_list}


@streams.get("/{b64config}/playback/{b64_media_id}/{hash_val}/{file_index}/{filename:path}")
async def playback(request: Request, b64config: str, b64_media_id: str, hash_val: str, file_index: int, filename: str):
    """
    Gère la lecture du média : contacte le service debrid et redirige vers le lien final
    ou affiche une vidéo de fallback si le torrent est en cours de téléchargement (puis on ferme).
    """
    config = config_check(b64config)
    if not config or config.get("debridService") == "torrent":
        return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4")

    try:
        real_media_id = b64_decode(b64_media_id)
        media_only_id = real_media_id.split(':')[1]
    except Exception:
        logger.error(f"Impossible de décoder le media_id: {b64_media_id}")
        return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4")

    anime_id, episode_id = await _parse_media_id(real_media_id)
    if not anime_id or not episode_id:
        logger.error(f"Impossible d'analyser l'ID de l'anime/épisode depuis {real_media_id}")
        return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4")

    #! On récupère les détails complets de l'épisode pour avoir la saison et le numéro
    fankai_api = FankaiAPI(request.app.state.http_client)
    _, selected_episode = await _fetch_anime_and_episode_data(fankai_api, anime_id, episode_id, real_media_id)

    if not selected_episode:
        logger.error(f"Impossible de récupérer les détails de l'épisode pour {real_media_id}")
        return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4")

    http_client = request.app.state.http_client #
    client_ip = get_client_ip(request) #
    stremthru_token = build_stremthru_token(config["debridService"], config["debridApiKey"]) #
    
    debrid_instance = StremThru(
        session=http_client,
        video_id=real_media_id,
        media_only_id=media_only_id,
        token=stremthru_token,
        ip=client_ip
    )
    
    logger.info(f"Appel de generate_download_link pour S{selected_episode.season_number}E{selected_episode.number}")
    download_url = await debrid_instance.generate_download_link(
        hash=hash_val,
        index=str(file_index),
        name=filename,
        torrent_name=filename,
        season=selected_episode.season_number,
        episode=selected_episode.number
    )
    
    if download_url:
        logger.info(f"Redirection vers le lien debrid final pour {filename}")
        return RedirectResponse(download_url, status_code=302)
    else:
        # Si le lien n'est pas disponible (en cours de DL ou erreur), on affiche la vidéo d'attente
        logger.warning(f"Affichage de la vidéo d'attente pour {filename} (téléchargement en cours ou erreur).")
        return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4", status_code=200)
