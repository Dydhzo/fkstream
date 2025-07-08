from .realdebrid import RealDebrid
from .alldebrid import AllDebrid
from .premiumize import Premiumize
from .torbox import TorBox
from .debridlink import DebridLink
from .torrent import Torrent
from .stremthru import StremThru
from .easydebrid import EasyDebrid
from .offcloud import Offcloud
from .pikpak import PikPak
from ..utils.http_client import HttpClient

debrid_services = {
    "realdebrid": {"extension": "RD", "cache_availability_endpoint": False, "class": RealDebrid},
    "alldebrid": {"extension": "AD", "cache_availability_endpoint": False, "class": AllDebrid},
    "premiumize": {"extension": "PM", "cache_availability_endpoint": True, "class": Premiumize},
    "torbox": {"extension": "TB", "cache_availability_endpoint": True, "class": TorBox},
    "debridlink": {"extension": "DL", "cache_availability_endpoint": False, "class": DebridLink},
    "stremthru": {"extension": "ST", "cache_availability_endpoint": True, "class": StremThru},
    "easydebrid": {"extension": "ED", "cache_availability_endpoint": True, "class": EasyDebrid},
    "offcloud": {"extension": "OC", "cache_availability_endpoint": False, "class": Offcloud},
    "pikpak": {"extension": "PP", "cache_availability_endpoint": False, "class": PikPak},
    "torrent": {"extension": "TORRENT", "cache_availability_endpoint": False, "class": Torrent},
}

def get_debrid_extension(debrid_service: str):
    """
    Retourne l'extension (abréviation) pour un service debrid donné.
    """
    if debrid_service not in debrid_services:
        raise ValueError(f"Service debrid inconnu: {debrid_service}")
    return debrid_services[debrid_service]["extension"]


def build_stremthru_token(debrid_service: str, debrid_api_key: str):
    """
    Construit le jeton d'authentification pour StremThru.
    """
    return f"{debrid_service}:{debrid_api_key}"


def get_debrid(session: HttpClient, video_id: str, media_only_id: str, debrid_service: str, debrid_api_key: str, ip: str):
    """
    Obtient une instance du client debrid approprié, en utilisant StremThru comme proxy.
    """
    if debrid_service != "torrent":
        return debrid_services["stremthru"]["class"](
            session,
            video_id,
            media_only_id,
            build_stremthru_token(debrid_service, debrid_api_key),
            ip,
        )


async def retrieve_debrid_availability(session: HttpClient, video_id: str, media_only_id: str, debrid_service: str, debrid_api_key: str, ip: str, hashes: list, seeders_map: dict, tracker_map: dict, sources_map: dict):
    """
    Vérifie la disponibilité des fichiers en cache sur le service debrid.
    """
    if debrid_service == "torrent":
        return []

    debrid_instance = get_debrid(session, video_id, media_only_id, debrid_service, debrid_api_key, ip)
    return await debrid_instance.get_availability(hashes, seeders_map, tracker_map, sources_map)
