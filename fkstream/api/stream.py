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


@streams.get("/stream/{media_type}/{media_id}.json")
@streams.get("/{b64config}/stream/{media_type}/{media_id}.json")
async def stream(request: Request, media_type: str, media_id: str, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """
    Fournit les flux de streaming en vérifiant la disponibilité debrid au préalable.
    """
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
    
    all_torrents_info = []
    hashes_to_check = []
    for source in target_anime_data.get('sources', []):
        magnet = source.get('magnet')
        info_hash_match = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
        if magnet and info_hash_match:
            hash_val = info_hash_match.group(1).lower()
            all_torrents_info.append({
                "source": source,
                "hash": hash_val
            })
            hashes_to_check.append(hash_val)

    if not hashes_to_check:
        return {"streams": []}


    debrid_service = config.get("debridService", "torrent")
    status_map = {}
    if debrid_service != "torrent" and hashes_to_check:
        http_client = request.app.state.http_client
        stremthru_token = build_stremthru_token(debrid_service, config["debridApiKey"])
        debrid_instance = StremThru(
            session=http_client, video_id=media_id, media_only_id=anime_id,
            token=stremthru_token, ip=get_client_ip(request)
        )
        
        seeders_map = {h: 0 for h in hashes_to_check}
        tracker_map = {h: 'dataset' for h in hashes_to_check}
        sources_map = {h: {"filename": "..."} for h in hashes_to_check}

        availability_results = await debrid_instance.get_availability(hashes_to_check, seeders_map, tracker_map, sources_map)
        status_map = {result['hash']: result['status'] for result in availability_results}

    streams_list = []
    for torrent_info in all_torrents_info:
        source = torrent_info["source"]
        hash_val = torrent_info["hash"]
        files_in_torrent = source.get('files', [])

        files_for_matching = [{"title": f} for f in files_in_torrent]
        best_file = await find_best_file_for_episode(request, files_for_matching, selected_episode)

        if best_file:
            try:
                file_index = files_in_torrent.index(best_file['title'])
                
                torrent_data = {
                    'infoHash': hash_val,
                    'title': best_file['title'],
                    'fileIndex': file_index,
                    'size': source.get('size', 0),
                    'seeders': source.get('seeders')
                }
                
                # Parce que les emojis, c'est cool
                status = status_map.get(hash_val, 'unknown')
                if status == "cached":
                    debrid_emoji = "⚡"
                elif status == "magnet":
                    debrid_emoji = "🧲"
                elif status in ["downloading", "queued"]:
                    debrid_emoji = "⬇️"
                else:
                    debrid_emoji = "❓"
                
                stream_item = _create_stream_item(request, b64config, debrid_service, debrid_emoji, torrent_data, media_id)
                streams_list.append(stream_item)


            except (ValueError, AttributeError) as e:
                logger.error(f"Erreur lors de la création du stream pour '{best_file['title']}': {e}")
                continue

    if not streams_list:
        logger.warning(f"Aucun stream n'a pu être généré pour {media_id} depuis le dataset.")

    return {"streams": streams_list}


# Endpoint séparé pour servir les assets
@streams.get("/{b64config}/assets/uncached.mp4")
async def serve_uncached_asset():
    """Sert la vidéo uncached.mp4 depuis un endpoint séparé."""
    return FileResponse("fkstream/assets/uncached.mp4", media_type="video/mp4")

@streams.get("/{b64config}/playback/{b64_media_id}/{hash_val}/{file_index}/{filename:path}")
async def playback(request: Request, b64config: str, b64_media_id: str, hash_val: str, file_index: int, filename: str):
    """
    Gère la lecture du média : contacte le service debrid et redirige vers le lien final
    ou redirige vers l'asset uncached si le torrent est en cours de téléchargement.
    """
    config = config_check(b64config)
    if not config or config.get("debridService") == "torrent":
        uncached_url = f"{request.url.scheme}://{request.url.netloc}/{b64config}/assets/uncached.mp4"
        return RedirectResponse(uncached_url, status_code=302)

    try:
        real_media_id = b64_decode(b64_media_id)
        media_only_id = real_media_id.split(':')[1]
    except Exception:
        logger.error(f"Impossible de décoder le media_id: {b64_media_id}")
        uncached_url = f"{request.url.scheme}://{request.url.netloc}/{b64config}/assets/uncached.mp4"
        return RedirectResponse(uncached_url, status_code=302)

    anime_id, episode_id = await _parse_media_id(real_media_id)
    if not anime_id or not episode_id:
        logger.error(f"Impossible d'analyser l'ID de l'anime/épisode depuis {real_media_id}")
        uncached_url = f"{request.url.scheme}://{request.url.netloc}/{b64config}/assets/uncached.mp4"
        return RedirectResponse(uncached_url, status_code=302)

    #! On récupère les détails complets de l'épisode pour avoir la saison et le numéro
    fankai_api = FankaiAPI(request.app.state.http_client)
    _, selected_episode = await _fetch_anime_and_episode_data(fankai_api, anime_id, episode_id, real_media_id)

    if not selected_episode:
        logger.error(f"Impossible de récupérer les détails de l'épisode pour {real_media_id}")
        uncached_url = f"{request.url.scheme}://{request.url.netloc}/{b64config}/assets/uncached.mp4"
        return RedirectResponse(uncached_url, status_code=302)

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
        # Si le lien n'est pas disponible (en cours de DL ou erreur), on redirige vers l'asset
        logger.warning(f"Redirection vers la vidéo d'attente pour {filename} (téléchargement en cours ou erreur).")
        uncached_url = f"{request.url.scheme}://{request.url.netloc}/{b64config}/assets/uncached.mp4"
        return RedirectResponse(uncached_url, status_code=302)
