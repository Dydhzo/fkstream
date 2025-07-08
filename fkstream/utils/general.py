import base64
import orjson
import re
import unicodedata
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

def _normalize_text(text: str) -> str:
    """Normalise un texte pour la comparaison (minuscules, sans accents, sans ponctuation)."""
    if not text:
        return ""
    text = unicodedata.normalize('NFD', text)
    text = re.sub(r'[\u0300-\u036f]', '', text)
    text = text.replace('œ', 'oe').replace('æ', 'ae')
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.lower().strip()

def normalize_for_search(text: str) -> str:
    """Normalisation agressive pour la recherche : supprime tout sauf les lettres et les chiffres."""
    if not text:
        return ""
    text = unicodedata.normalize('NFD', text)
    text = re.sub(r'[\u0300-\u036f]', '', text)
    text = text.replace('œ', 'oe').replace('æ', 'ae')
    text = re.sub(r'[^a-z0-9]', '', text.lower())
    return text

def normalize_anime_title_for_search(title: str) -> str:
    """Normalise le titre d'un anime pour la recherche Nyaa en retirant les termes spécifiques à Fan-Kai."""
    if not title:
        return ""
    terms_to_remove = ['Kaï', 'Yabai', 'Henshū', 'Fan-Cut']
    pattern = '|'.join(re.escape(term) for term in terms_to_remove)
    normalized = ' '.join(re.sub(pattern, '', title).split())
    return normalized.strip()

def _compare_word_similarity(text1: str, text2: str) -> bool:
    """Compare la similarité de deux chaînes en se basant sur les mots importants."""
    if not text1 or not text2:
        return False
    text1_words = text1.split()
    text2_words = text2.split()
    common_words = ['de', 'du', 'des', 'et', 'a', 'à', 'le', 'la', 'les', 'un', 'une', 'l', 'd', 'au', 'aux', 'ce', 'cette', 'ces', 'se', 'sa', 'son', 'ses']
    important_text2_words = [word for word in text2_words if len(word) > 2 and word.lower() not in common_words]
    if not important_text2_words:
        return False
    matched_words = sum(1 for word2 in important_text2_words if any(word1.lower() == word2.lower() or (len(word1) >= 3 and len(word2) >= 3 and (word1.lower() in word2.lower() or word2.lower() in word1.lower())) for word1 in text1_words))
    # Logique adaptative selon le nombre de mots
    if matched_words >= 1:
        if len(important_text2_words) <= 2:
            return True  # Pour 1-2 mots: 1 correspondance suffit
        else:
            return (matched_words / len(important_text2_words)) >= 0.6  # Pour 3+ mots: 60% minimum
    else:
        return False  # Aucun mot ne correspond

def _match_episode_number_strict(filename: str, episode_number: int) -> bool:
    """Vérifie la présence d'un numéro d'épisode de manière stricte dans un nom de fichier."""
    ep_formats = [f"{episode_number:02d}", f"{episode_number:03d}"]
    patterns = []
    for ep_str in ep_formats:
        patterns.extend([
            rf'[._\s-](?:E|e|EP|ep|episode|Episode|épisode|Épisode)?[._\s-]?{ep_str}[._\s-]',
            rf'\[{ep_str}\]', rf'S\d+E{ep_str}', rf's\d+e{ep_str}', rf'^[\[\(]?{ep_str}[\]\)]?\s-'
        ])
    if any(re.search(p, filename) for p in patterns):
        return True
    
    ep_single = str(episode_number)
    patterns_single = [rf'[._\s-](?:E|e|EP|ep|episode|Episode|épisode|Épisode)?[._\s-]?{ep_single}[._\s-]', rf'\[{ep_single}\]']
    return any(re.search(p, filename) for p in patterns_single)

def find_best_file_for_episode(files: list, episode_info: dict):
    """
    Trouve le meilleur fichier pour un épisode donné en se basant sur le nom, le numéro et la taille.
    Exige une correspondance à la fois sur le nom et le numéro de l'épisode.
    """
    best_file, best_score = None, -1
    episode_number = episode_info.get("episode")
    episode_name = episode_info.get("episode_name")

    logger.info(f"Recherche episode {episode_number}: '{episode_name}' dans {len(files)} fichiers")

    for i, file in enumerate(files):
        filename = file.get("title")
        if not filename or not is_video(filename):
            continue

        name_match_score = 0
        if episode_name and episode_name.strip():
            if _compare_word_similarity(_normalize_text(filename), _normalize_text(episode_name)):
                name_match_score = 100
                logger.info(f"📚 NOM CORRESPONDANT trouve: '{episode_name}' dans '{filename}'")
            else:
                logger.info(f"❌ NOM NON CORRESPONDANT: '{episode_name}' ne correspond pas suffisamment a '{filename}'")
        
        number_match_score = 50 if _match_episode_number_strict(filename, episode_number) else 0
        if number_match_score > 0:
            logger.info(f"🔢 NUMERO CORRESPONDANT trouve: episode {episode_number} dans '{filename}'")
        
        if not episode_name or not episode_name.strip():
            logger.warning(f"⚠️ CONFIGURATION MANQUANTE: Aucun nom d'episode fourni pour l'episode {episode_number} - impossible de faire correspondre correctement")
            continue
            
        if name_match_score == 0 or number_match_score == 0:
            logger.info(f"❌ CORRESPONDANCE INCOMPLETE: '{filename}' - NUMERO OBLIGATOIRE ({number_match_score}/50) ET NOM ({name_match_score:.1f}/100) - IGNORE")
            continue
            
        logger.info(f"✅ CORRESPONDANCE COMPLETE VALIDEE: '{filename}' - le numero ET le nom correspondent parfaitement")
        
        score = name_match_score + number_match_score
        if "sample" in filename.lower():
            score -= 1000
        
        size_bonus = file.get("size", 0) / (1024 * 1024)
        score += min(size_bonus, 50)
        
        logger.info(f"✅ SCORE FINAL: {score:.1f} pour '{filename}' (nom:{name_match_score:.1f} + num:{number_match_score} + taille:{size_bonus:.1f})")

        if score > best_score:
            best_score = score
            best_file = dict(file)
            best_file['index'] = i
            if 'original_torrent_index' in file:
                best_file['original_torrent_index'] = file['original_torrent_index']

    if best_file:
        logger.info(f"🏆 MEILLEURE CORRESPONDANCE: '{best_file['title']}' avec un score de {best_score:.1f}")
    else:
        logger.warning(f"⚠️ AUCUN FICHIER trouve pour l'episode {episode_number}: '{episode_name}'")
        
    return best_file
