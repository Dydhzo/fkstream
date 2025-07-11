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

    # Titre du fichier
    info_line = f"'{display_title}'"

    stream_item = {
        "name": f"[{get_debrid_extension(debrid_service)} {debrid_emoji}] FKStream",
        "description": f"📁 {info_line}",
        "behaviorHints": {
            "bingeGroup": f"fkstream|{hash_val}",
            "filename": display_title
        },
    }

    if debrid_service == "torrent":
        stream_item["infoHash"] = hash_val
        stream_item["fileIdx"] = torrent.get('fileIndex')
    else:
        encoded_filename = quote(display_title, safe='')
        encoded_media_id = b64_encode(media_id)
        stream_item["url"] = f"{request.url.scheme}://{request.url.netloc}/{b64config}/playback/{encoded_media_id}/{hash_val}/{encoded_filename}"
        
        logger.info(f"URL generee: {stream_item['url']} (Source: Dataset, Fichier: {file_title})")

    return stream_item

# --- Route principale de streaming ---

@streams.get("/stream/{media_type}/{media_id}.json")
@streams.get("/{b64config}/stream/{media_type}/{media_id}.json")
async def stream(request: Request, media_type: str, media_id: str, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """
    Fournit les flux de streaming en se basant sur le dataset local.
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
    
    streams_list = []
    debrid_service_name = config.get("debridService", "torrent")

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
                if not info_hash_match:
                    logger.warning(f"Info hash non trouvé dans le magnet: {magnet}")
                    continue
                
                info_hash = info_hash_match.group(1).lower()

                torrent_info = {
                    'infoHash': info_hash,
                    'title': best_file['title'],
                    'fileIndex': file_index
                }

                stream_item = _create_stream_item(
                    request, b64config, debrid_service_name, "📂", torrent_info, media_id
                )
                if stream_item:
                    streams_list.append(stream_item)
                    logger.info("Correspondance trouvée et stream créé. Arrêt de la recherche.")
                    break

            except (ValueError, AttributeError) as e:
                logger.error(f"Erreur lors de la création du stream pour '{best_file['title']}': {e}")
                continue

    if not streams_list:
        logger.warning(f"Aucun stream n'a pu être généré pour {media_id} depuis le dataset.")

    return {"streams": streams_list}