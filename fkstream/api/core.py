from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote, urlparse, parse_qs
from datetime import datetime

from fkstream.utils.models import settings, web_config, Episode
from fkstream.utils.config_validator import config_check
from fkstream.debrid.manager import get_debrid_extension
from fkstream.scrapers.fankai import FankaiAPI, get_or_fetch_anime_details
from fkstream.utils.common_logger import logger
from fkstream.utils.database import get_metadata_from_cache, set_metadata_to_cache
from fkstream.utils.dependencies import get_fankai_api

templates = Jinja2Templates("fkstream/templates")
main = APIRouter()


def _translate_status(status: str) -> str:
    """Traduit le statut de l'anime en français."""
    status_translations = {
        "Continuing": "En cours",
        "Ended": "Terminé",
        "Unknown": None,
        "Canceled": "Annulé",
        "Cancelled": "Annulé",
        "En suspens": "En suspens"
    }
    return status_translations.get(status, status)


def _build_genre_links(request: Request, b64config: str, genres: list) -> list:
    """
    Construit les liens de genre pour Stremio.
    """
    if not genres:
        return []
    genre_links = []
    base_url = str(request.base_url).rstrip('/')
    if b64config:
        encoded_manifest = f"{base_url}/{b64config}/manifest.json"
    else:
        encoded_manifest = f"{base_url}/manifest.json"

    encoded_manifest = quote(encoded_manifest, safe='')

    for genre_name in genres:
        genre_links.append({
            "name": genre_name,
            "category": "Genres",
            "url": f"stremio:///discover/{encoded_manifest}/anime/fankai_catalog?genre={quote(genre_name)}"
        })

    return genre_links


@main.get("/")
async def root():
    return RedirectResponse("/configure")


@main.get("/health")
async def health():
    """Endpoint de vérification de l'état de santé de l'application."""
    return {"status": "ok"}


@main.get("/configure")
@main.get("/{b64config}/configure")
async def configure(request: Request):
    """Affiche la page de configuration de l'addon."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "CUSTOM_HEADER_HTML": settings.CUSTOM_HEADER_HTML or "",
            "webConfig": web_config,
            "proxyDebridStream": settings.PROXY_DEBRID_STREAM,
        },
    )


@main.get("/manifest.json")
@main.get("/{b64config}/manifest.json")
async def manifest(request: Request, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """
    Fournit le manifeste de l'addon à Stremio.
    Personnalise le nom et la description en fonction de la configuration.
    """
    base_manifest = {
        "id": settings.ADDON_ID,
        "name": settings.ADDON_NAME,
        "description": "FKStream – Addon non officiel pour accéder au contenu de Fankai",
        "version": "1.2.0",
        "catalogs": [
            {
                "type": "anime",
                "id": "fankai_catalog",
                "name": "Fankai",
                "extra": [
                    {"name": "skip"},
                    {"name": "search", "isRequired": False},
                    {"name": "genre", "isRequired": False, "options": []},
                    {
                        "name": "sort",
                        "isRequired": False,
                        "options": [
                            "last_update:Dernière mise à jour",
                            "rating_value:Note",
                            "title:Titre",
                            "year:Année"
                        ]
                    }
                ]
            }
        ],
        "resources": [
            "catalog",
            {"name": "meta", "types": ["anime"], "idPrefixes": ["fk"]},
            {"name": "stream", "types": ["anime"], "idPrefixes": ["fk"]}
        ],
        "types": ["anime"],
        "logo": "https://raw.githubusercontent.com/Dydhzo/fkstream/refs/heads/main/fkstream/assets/fkstream-logo.jpg",
        "background": "https://raw.githubusercontent.com/Dydhzo/fkstream/refs/heads/main/fkstream/assets/fkstream-background.jpg",
        "behaviorHints": {"configurable": True, "configurationRequired": False},
    }

    config = config_check(b64config)
    if not config:
        base_manifest["name"] = "❌ | FKStream"
        base_manifest["description"] = (
            f"⚠️ CONFIGURATION OBSELETE, VEUILLEZ RECONFIGURER SUR {request.url.scheme}://{request.url.netloc} ⚠️"
        )
        return base_manifest

    debrid_extension = get_debrid_extension(config["debridService"])
    base_manifest["name"] = f"{settings.ADDON_NAME}{' | ' + debrid_extension if debrid_extension else ''}"

    try:
        unique_genres = await extract_unique_genres(fankai_api)
        base_manifest["catalogs"][0]["extra"][2]["options"] = unique_genres
        logger.info(f"📋 MANIFEST - Ajout de {len(unique_genres)} options de genre")
    except Exception as e:
        logger.error(f"❌ MANIFEST - Echec de l'extraction des genres: {e}")

    return base_manifest


async def extract_unique_genres(fankai_api: FankaiAPI) -> list[str]:
    """
    Extrait tous les genres uniques à partir des données d'anime de l'API Fankai.
    Utilise le même cache que la liste d'animes pour la cohérence.
    Retourne une liste triée de genres uniques.
    """
    animes_data = await get_metadata_from_cache("fk:list")

    if not animes_data:
        logger.debug("📦 CACHE MISS: fk:list - Recuperation depuis l'API")
        animes_data = await fankai_api.get_all_series()
        await set_metadata_to_cache("fk:list", animes_data)
        logger.debug("✅ CACHE SAUVEGARDE: fk:list")
    else:
        logger.debug("✅ CACHE HIT: fk:list")

    unique_genres = set()
    for anime in animes_data:
        genres_raw = anime.get('genres', '')
        if genres_raw:
            genres = [g.strip() for g in genres_raw.split(',') if g.strip()]
            unique_genres.update(genres)

    sorted_genres = sorted(list(unique_genres))

    logger.debug(f"🎭 GENRES - Extraction de {len(sorted_genres)} genres uniques depuis le cache")
    return sorted_genres


@main.get("/catalog/anime/fankai_catalog.json")
@main.get("/{b64config}/catalog/anime/fankai_catalog.json")
@main.get("/catalog/anime/fankai_catalog/search={search}.json")
@main.get("/{b64config}/catalog/anime/fankai_catalog/search={search}.json")
@main.get("/catalog/anime/fankai_catalog/genre={genre}.json")
@main.get("/{b64config}/catalog/anime/fankai_catalog/genre={genre}.json")
@main.get("/catalog/anime/fankai_catalog/search={search}&genre={genre}.json")
@main.get("/{b64config}/catalog/anime/fankai_catalog/search={search}&genre={genre}.json")
@main.get("/catalog/anime/fankai_catalog/sort={sort}.json")
@main.get("/{b64config}/catalog/anime/fankai_catalog/sort={sort}.json")
async def fankai_catalog(request: Request, b64config: str = None, search: str = None, genre: str = None, sort: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """
    Fournit le catalogue d'animes en filtrant par le dataset local.
    """
    if not search and "search" in request.query_params:
        search = request.query_params.get("search")
    if not genre and "genre" in request.query_params:
        genre = request.query_params.get("genre")
    if not sort and "sort" in request.query_params:
        sort = request.query_params.get("sort")

    logger.info(f"🔍 CATALOG - Catalogue Fankai demandé, recherche: {search}, genre: {genre}, tri: {sort}")


    # 1. Obtenir la liste des api_id autorisés depuis le dataset
    dataset_animes = request.app.state.dataset.get('top', [])
    available_api_ids = {str(anime.get('api_id')) for anime in dataset_animes}

    if not available_api_ids:
        logger.warning("Le dataset est vide ou ne contient aucun api_id. Le catalogue sera vide.")
        return {"metas": []}

    animes_data = await get_metadata_from_cache("fk:list")

    if animes_data:
        logger.debug("✅ CACHE HIT: fk:list")
    else:
        logger.debug("📦 CACHE MISS: fk:list - Recuperation depuis l'API")
        animes_data = await fankai_api.get_all_series()
        await set_metadata_to_cache("fk:list", animes_data)
        
        
    # 2. Filtrer la liste d'animes (du cache ou de l'API) pour ne garder que ceux du dataset
    animes_data = [anime for anime in animes_data if str(anime.get('id')) in available_api_ids]
    logger.info(f"Filtrage par dataset : {len(animes_data)} animes valides à traiter.")



    config = config_check(b64config)
    sort_key_from_ui = sort.split(':')[0] if sort else None
    sort_by = sort_key_from_ui if sort_key_from_ui else config.get("defaultSort", "last_update")
    
    logger.info(f"Tri du catalogue par: {sort_by}")

    def get_sort_value(anime, key):
        val = anime.get(key)
        if val is None:
            if key in ['rating_value', 'year']: return -1
            if key == 'last_update': return datetime.min
            return ""
        if key in ['rating_value', 'year']:
            try: return float(val)
            except (ValueError, TypeError): return -1
        if key == 'last_update':
            try: return datetime.fromisoformat(str(val).replace(" ", "T"))
            except (ValueError, TypeError): return datetime.min
        return val

    reverse = sort_by != 'title'
    animes_data.sort(key=lambda x: get_sort_value(x, sort_by), reverse=reverse)

    metas = []
    for anime in animes_data:
        anime_title = anime.get('title', '')
        genres_raw = anime.get('genres', '')
        genres = [g.strip() for g in genres_raw.split(',') if g.strip()] if genres_raw else []

        if search and search.lower() not in anime_title.lower():
            continue

        if genre and genre not in genres:
            continue


        genre_links = _build_genre_links(request, b64config, genres)
        imdb_links = []
        if anime.get('imdb_id'):
            rating_display = str(anime.get('rating_value')) if anime.get('rating_value') else "N/A"
            imdb_links.append({
                "name": rating_display,
                "category": "imdb",
                "url": f"https://imdb.com/title/{anime.get('imdb_id')}"
            })

        meta = {
            "id": f"fk:{anime.get('id')}",
            "type": "anime",
            "logo": anime.get('logo_image'),
            "name": anime_title,
            "poster": anime.get('poster_image'),
            "posterShape": "poster",
            "genres": genres,
            "imdbRating": str(anime.get('rating_value')) if anime.get('rating_value') else None,
            "releaseInfo": str(anime.get('year')) if anime.get('year') else None,
            "runtime": _translate_status(anime.get('status')) if anime.get('status') else None,
            "imdb_id": anime.get('imdb_id'),
            "description": anime.get('plot', '') or "Aucune description disponible",
            "links": genre_links + imdb_links,
        }
        
        # Ajout du trailer si disponible
        trailer_url = anime.get("trailer_url")
        if trailer_url:
            logger.debug(f"TRAILER - URL de bande-annonce trouvee: {trailer_url}")
            if "youtube" in trailer_url:
                try:
                    parsed_url = urlparse(trailer_url)
                    query_params = parse_qs(parsed_url.query)
                    video_id = query_params.get("video_id", query_params.get("v", [None]))[0]

                    if video_id:
                        meta['trailers'] = [{"source": video_id, "type": "Trailer"}]
                        logger.debug(f"TRAILER - Ajout de la propriete 'trailers' au meta-objet: {meta['trailers']}")
                    else:
                        logger.warning(f"TRAILER - Impossible d'extraire le video_id de l'URL: {trailer_url}")
                except Exception as e:
                    logger.warning(f"TRAILER - Impossible de parser l'URL de la bande-annonce '{trailer_url}': {e}")
            else:
                logger.warning(f"TRAILER - L'URL de la bande-annonce n'est pas une URL YouTube: {trailer_url}")
        else:
            logger.debug(f"TRAILER - Pas de 'trailer_url' trouve pour l'anime {anime.get('id')}.")
        metas.append(meta)

    if search and genre:
        logger.info(f"🔍 CATALOG - Recherche '{search}' + Genre '{genre}': {len(metas)} animes trouves")
    elif search:
        logger.info(f"🔍 CATALOG - Recherche '{search}': {len(metas)} animes trouves")
    elif genre:
        logger.info(f"🎭 CATALOG - Genre '{genre}': {len(metas)} animes trouves")
    else:
        logger.info(f"🔍 CATALOG - Retour de tous les {len(metas)} animes valides")

    return {"metas": metas}


def _validate_anime_id(anime_id: str) -> bool:
    """Valide un ID d'anime en s'assurant qu'il est numérique et dans une plage réaliste."""
    if not anime_id.isdigit():
        return False
    try:
        id_num = int(anime_id)
        return 1 <= id_num <= 999999
    except (ValueError, OverflowError):
        return False


@main.get("/meta/anime/{id}.json")
@main.get("/{b64config}/meta/anime/{id}.json")
async def fankai_meta(request: Request, id: str, b64config: str = None, fankai_api: FankaiAPI = Depends(get_fankai_api)):
    """
    Fournit les métadonnées détaillées pour un anime spécifique.
    """
    if not id.startswith("fk:"):
        return {"meta": {}}

    anime_id = id.replace("fk:", "")

    if not _validate_anime_id(anime_id):
        logger.warning(f"ID d'anime invalide (non numerique ou hors plage 1-999999): {anime_id}")
        return {"meta": {}}

    anime_data = await get_or_fetch_anime_details(fankai_api, anime_id)

    if not anime_data:
        return {"meta": {}}

    logger.debug(f"ANIME_DATA KEYS - Cles disponibles pour l'anime {anime_id}: {list(anime_data.keys())}")
    
    # Structure de meta (plus conforme)
    meta = {
        "id": f"fk:{anime_data.get('id')}",
        "type": "anime",
        "logo": anime_data.get('logo_image'),
        "name": anime_data.get('title'),
        "poster": anime_data.get('poster_image'),
        "posterShape": "poster",
        "background": anime_data.get('fanart_image'),
        "imdbRating": str(anime_data.get('rating_value')) if anime_data.get('rating_value') else None,
        "releaseInfo": str(anime_data.get('year')) if anime_data.get('year') else None,
        "runtime": _translate_status(anime_data.get('status')) if anime_data.get('status') else None,
        "imdb_id": anime_data.get('imdb_id'),
        "description": anime_data.get('plot'),
        "behaviorHints": {
            "hasScheduledVideos": True
        }
    }

    videos = []

    # Ajout du trailer si disponible
    trailer_url = anime_data.get("trailer_url")
    if trailer_url:
        logger.debug(f"TRAILER - URL de bande-annonce trouvee: {trailer_url}")
        if "youtube" in trailer_url:
            try:
                parsed_url = urlparse(trailer_url)
                query_params = parse_qs(parsed_url.query)
                video_id = query_params.get("video_id", query_params.get("v", [None]))[0]

                if video_id:
                    meta['trailers'] = [{"source": video_id, "type": "Trailer"}]
                    logger.debug(f"TRAILER - Ajout de la propriete 'trailers' au meta-objet: {meta['trailers']}")
                else:
                    logger.warning(f"TRAILER - Impossible d'extraire le video_id de l'URL: {trailer_url}")
            except Exception as e:
                logger.warning(f"TRAILER - Impossible de parser l'URL de la bande-annonce '{trailer_url}': {e}")
        else:
            logger.warning(f"TRAILER - L'URL de la bande-annonce n'est pas une URL YouTube: {trailer_url}")
    else:
        logger.debug(f"TRAILER - Pas de 'trailer_url' trouve pour l'anime {anime_id}.")


    # Add episodes
    seasons = anime_data.get("seasons", [])
    for season_idx, season in enumerate(seasons):
        season_number = season.get('season_number', season.get('number', season_idx + 1))
        episodes = season.get('episodes', [])
        for episode in episodes:
            final_season_number = episode.get('season_number', season_number)
            thumbnail_url = f"https://metadata.fankai.fr/episodes/{episode.get('id')}/image"
            aired_date = episode.get('aired', '2024-01-01')
            videos.append({
                "id": f"fk:{anime_id}:{episode.get('id')}",
                "title": episode.get('title'),
                "season": final_season_number,
                "episode": episode.get('episode_number'),
                "thumbnail": thumbnail_url,
                "overview": episode.get('plot'),
                "released": f"{aired_date}T00:00:00.000Z" if aired_date and aired_date != '2024-01-01' else "2024-01-01T00:00:00.000Z"
            })
    
    meta['videos'] = videos

    # Add links
    genres_raw = anime_data.get('genres', '')
    genres = [g.strip() for g in genres_raw.split(',') if g.strip()] if genres_raw else []
    meta['genres'] = genres
    
    genre_links = _build_genre_links(request, b64config, genres)
    imdb_links = []
    if anime_data.get('imdb_id'):
        rating_display = str(anime_data.get('rating_value')) if anime_data.get('rating_value') else "N/A"
        imdb_links.append({
            "name": rating_display,
            "category": "imdb",
            "url": f"https://imdb.com/title/{anime_data.get('imdb_id')}"
        })

    actor_links = []
    actors = anime_data.get('actors', [])
    if actors:
        config = config_check(b64config)
        max_actors = config.get('maxActorsDisplay', 'all') if config else 'all'
        actors_to_show = actors if max_actors == 'all' else actors[:int(max_actors)]
        for actor in actors_to_show:
            actor_name = actor.get('name', 'Acteur inconnu')
            search_query = quote(actor_name)
            actor_links.append({
                "name": actor_name,
                "category": "Cast",
                "url": f"stremio:///search?search={search_query}"
            })

    meta['links'] = genre_links + imdb_links + actor_links

    return {"meta": meta}
