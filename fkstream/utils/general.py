import base64
import orjson
import re
import httpx
import unicodedata
from functools import lru_cache
from fastapi import Request
from fkstream.utils.models import settings
from fkstream.utils.common_logger import logger

# Extensions video supportees
# Limité aux seuls formats utilisés par Fan-Kai
VIDEO_EXTENSIONS = (
    ".mkv", ".mp4"
)

# URL pour le fichier de renommage
RENAME_FILE_URL = "https://raw.githubusercontent.com/Nackophilz/fankai_utilitaire/refs/heads/main/rename/films.txt"

def b64_encode(s: str) -> str:
    """Encode une chaîne en base64 URL-safe."""
    return base64.urlsafe_b64encode(s.encode()).decode()

def b64_decode(s: str) -> str:
    """Décode une chaîne base64 URL-safe."""
    try:
        if not s or not isinstance(s, str):
            raise ValueError("Entree invalide")
        return base64.urlsafe_b64decode(s).decode()
    except Exception:
        raise ValueError("Chaine base64 invalide")


def get_client_ip(request: Request) -> str:
    """Récupère l'adresse IP du client à partir de la requête."""
    return request.headers.get("cf-connecting-ip", request.client.host)

def is_video(title: str) -> bool:
    """Vérifie si un fichier est un fichier vidéo en fonction de son extension."""
    return title.lower().endswith(VIDEO_EXTENSIONS)

def default_dump(obj):
    """Fonction de sérialisation par défaut pour orjson."""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, 'dict'):
        return obj.dict()
    return str(obj)


@lru_cache(maxsize=1)
def get_rename_map():
    """
    Récupère et analyse le fichier de renommage depuis l'URL, 
    retournant un dictionnaire de mappage. Le résultat est mis en cache.
    """
    rename_map = {}
    try:
        with httpx.Client() as client:
            response = client.get(RENAME_FILE_URL, follow_redirects=True)
            response.raise_for_status()
            content = response.text
            for line in content.splitlines():
                if " -> " in line:
                    original, new = line.split(" -> ", 1)
                    rename_map[original.strip()] = new.strip()
            logger.info(f"Chargement réussi de {len(rename_map)} règles de renommage.")
            return rename_map
    except httpx.RequestError as e:
        logger.error(f"Erreur lors de la récupération du fichier de renommage depuis {RENAME_FILE_URL}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse du fichier de renommage: {e}")
        return {}

def apply_renaming(title: str) -> str:
    """
    Applique les règles de renommage à un titre si une correspondance est trouvée.
    La correspondance se fait sur le titre sans son extension de fichier.
    """
    rename_map = get_rename_map()
    title_no_ext = title.rsplit('.', 1)[0] if '.' in title else title
    
    if title_no_ext in rename_map:
        new_title = rename_map[title_no_ext]
        logger.debug(f"Titre renommé : '{title}' -> '{new_title}'")
        return new_title
    return title

def normalize_for_comparison(title: str) -> str:
    """
    Normalisation d'un titre pour la comparaison (dernière méthode de recherche)
    """
    if not title:
        return ""
    
    original_title = title
    
    # Étape 1: Extraire seulement le nom du fichier
    if '/' in title or '\\' in title:
        title = title.replace('\\', '/').split('/')[-1]
    
    # Étape 2: Enlever l'extension
    title = title.rsplit('.', 1)[0]

    # Étape 3: Passage en minuscules et suppression des accents
    title = title.lower()
    nfkd_form = unicodedata.normalize('NFKD', title)
    title = "".join([c for c in nfkd_form if not unicodedata.combining(c)])

    # Étape 4: Supprime le contenu entre crochets et parenthèses
    title = re.sub(r'\[.*?\]|\(.*?\)', '', title)

    # Étape 5: Supprimer les termes de qualité vidéo et autres "bruits"
    noise = [
        '1080p', '720p', '480p', 'bdrip', 'multi', 'vostfr', 'vf2', 'vf', 'x264', 
        'x265', 'hevc', 'bluray', 'web-dl', 'webrip', 'hdlight', 'dvdrip', 'remux',
        'fan-cut', 'henshu', 'film', 'kai', 'yabai'
    ]
    noise_pattern = r'\b(' + '|'.join(re.escape(term) for term in noise) + r')\b'
    title = re.sub(noise_pattern, '', title, flags=re.IGNORECASE)

    # Étape 6: Remplace les séparateurs et apostrophes par des espaces
    title = re.sub(r'[.\-_'']', ' ', title)

    # Étape 7: Normaliser les numéros (ex: 01 -> 1)
    title = re.sub(r'\b0+(\d+)\b', r'\1', title)
    
    # Étape 8: Remplacer les espaces multiples par un seul et nettoyer les bords
    title = re.sub(r'\s+', ' ', title).strip()
    
    logger.debug(f"Normalisation: '{original_title}' -> '{title}'")
    return title

def find_best_file_for_episode(files: list, episode_info: dict):
    """
    Trouve le fichier correspondant à un épisode en utilisant une logique de correspondance multi étapes
    1. Correspondance exacte (rapide).
    2. Correspondance normalisée (robuste).
    3. Si aucune correspondance, applique les règles de renommage et réessaye l'étape 2.
    """
    nfo_filename = episode_info.get("nfo_filename")
    if not nfo_filename:
        logger.warning("Pas de nfo_filename fourni, impossible de matcher")
        return None

    target_filename_base = nfo_filename[:-4] if nfo_filename.lower().endswith('.nfo') else nfo_filename
    
    # --- Étape 1: Correspondance exacte ---
    logger.info(f"Recherche (étape 1 - exacte) pour: '{target_filename_base}'")
    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue
        
        filename_no_ext = filename.rsplit('.', 1)[0]
        if target_filename_base.lower() == filename_no_ext.lower():
            logger.info(f"✅ CORRESPONDANCE EXACTE TROUVÉE: '{filename}'")
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']
            return best_file

    logger.warning("Aucune correspondance exacte trouvée. Passage à la correspondance normalisée.")

    # --- Étape 2: Correspondance normalisée ---
    normalized_target = normalize_for_comparison(target_filename_base)
    logger.info(f"Recherche (étape 2 - normalisée) pour: '{normalized_target}'")
    
    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue
        
        normalized_filename = normalize_for_comparison(filename)
        if normalized_target == normalized_filename:
            logger.info(f"✅ CORRESPONDANCE TROUVÉE (étape 2 - normalisée): '{filename}'")
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']
            return best_file

    logger.warning(f"Aucune correspondance trouvée à l'étape 2. Passage à l'étape 3 (renommage).")

    # --- Étape 3: Renommage et nouvelle tentative de correspondance normalisée ---
    renamed_target_base = apply_renaming(target_filename_base)
    if renamed_target_base == target_filename_base:
        logger.warning(f"⚠️ AUCUN FICHIER TROUVÉ pour: '{target_filename_base}' (aucune règle de renommage applicable)")
        return None

    normalized_renamed_target = normalize_for_comparison(renamed_target_base)
    logger.info(f"Recherche (étape 3 - renommage) pour: '{normalized_renamed_target}'")

    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue
        
        normalized_filename = normalize_for_comparison(filename)
        if normalized_renamed_target == normalized_filename:
            logger.info(f"✅ CORRESPONDANCE TROUVÉE (étape 3 - renommée): '{filename}'")
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']
            return best_file

    logger.warning(f"⚠️ AUCUN FICHIER TROUVÉ pour: '{target_filename_base}' même après renommage.")
    return None
