import base64
import orjson
import re
from fastapi import Request
from fkstream.utils.models import settings
from fkstream.utils.common_logger import logger

# Extensions video supportees - SEULE source de verite
VIDEO_EXTENSIONS = (
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".m2v", ".asf", ".3gp", ".ogv", ".qt", ".rm", 
    ".rmvb", ".f4v", ".f4p", ".roq", ".svi", ".yuv", ".mxf", ".nsv",
    ".ogg", ".gif", ".gifv", ".3g2", ".amv", ".drc", ".f4a", ".f4b",
    ".mp2", ".mpv", ".mng", ".mpe"
)

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

def config_check(b64config: str):
    """Wrapper legacy pour la validation de configuration."""
    from .config_validator import validate_config
    return validate_config(b64config)

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

def normalize_anime_title_for_search(title: str) -> str:
    """Normalise le titre d'un anime pour la recherche Nyaa en retirant les termes spécifiques à Fan-Kai."""
    if not title:
        return ""
    terms_to_remove = ['Kaï', 'Yabai', 'Henshū', 'Fan-Cut']
    pattern = '|'.join(re.escape(term) for term in terms_to_remove)
    normalized = ' '.join(re.sub(pattern, '', title).split())
    return normalized.strip()


def find_best_file_for_episode(files: list, episode_info: dict):
    """
    Trouve le fichier correspondant à un épisode en utilisant le nfo_filename.
    Cette méthode est plus simple et directe : elle cherche le nom exact du fichier
    sans le .nfo.
    """
    nfo_filename = episode_info.get("nfo_filename")
    if not nfo_filename:
        logger.warning("Pas de nfo_filename fourni, impossible de matcher")
        return None
    
    # Enlever l'extension .nfo
    target_filename = nfo_filename
    if target_filename.endswith('.nfo'):
        target_filename = target_filename[:-4]
    
    logger.info(f"Recherche du fichier: '{target_filename}' dans {len(files)} fichiers")
    
    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue
        
        # Vérifier si le nom du fichier contient le nom cible
        if target_filename in filename:
            logger.info(f"✅ CORRESPONDANCE trouvée: '{target_filename}' dans '{filename}'")
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']
            return best_file
    
    # Si pas de correspondance exacte, essayer sans l'extension du fichier torrent
    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue
        
        # Enlever l'extension pour comparer
        filename_no_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
        target_no_ext = target_filename.rsplit('.', 1)[0] if '.' in target_filename else target_filename
        
        if target_no_ext in filename_no_ext:
            logger.info(f"✅ CORRESPONDANCE trouvée (sans extension): '{target_no_ext}' dans '{filename}'")
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']
            return best_file
    
    logger.warning(f"⚠️ AUCUN FICHIER trouvé pour: '{target_filename}'")
    return None
