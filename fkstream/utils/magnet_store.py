from typing import Optional, Dict
import threading

class MagnetStore:
    """Stockage thread-safe pour les liens magnets."""
    
    def __init__(self):
        self._store: Dict[str, str] = {}
        self._lock = threading.RLock()
    
    def store_magnet_link(self, hash: str, magnet_link: str) -> None:
        """Stocke un lien magnet avec de vrais traqueurs pour une récupération ultérieure."""
        with self._lock:
            self._store[hash.lower()] = magnet_link
    
    def get_magnet_link(self, hash: str) -> Optional[str]:
        """Récupère un lien magnet stocké avec de vrais traqueurs."""
        with self._lock:
            return self._store.get(hash.lower())
    
    def clear(self) -> None:
        """Efface tous les liens magnets stockés."""
        with self._lock:
            self._store.clear()
    
    def size(self) -> int:
        """Retourne le nombre de liens magnets stockés."""
        with self._lock:
            return len(self._store)

# Instance globale pour la compatibilité ascendante
_global_magnet_store = MagnetStore()

def store_magnet_link(hash: str, magnet_link: str) -> None:
    """Stocke un lien magnet avec de vrais traqueurs pour une récupération ultérieure."""
    _global_magnet_store.store_magnet_link(hash, magnet_link)

def get_magnet_link(hash: str) -> Optional[str]:
    """Récupère un lien magnet stocké avec de vrais traqueurs."""
    return _global_magnet_store.get_magnet_link(hash)
