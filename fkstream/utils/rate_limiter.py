import asyncio
import time
from typing import Dict
from fkstream.utils.logger import logger
from fkstream.utils.models import settings


class UserRateLimiter:
    """Limiteur de taux par IP utilisateur pour éviter les erreurs 429 des APIs externes comme Nyaa.si"""
    
    def __init__(self):
        self.delay = float(settings.NYAA_RATE_LIMIT_DELAY)
        self.user_limits: Dict[str, float] = {}
        logger.info(f"🚦 RATE LIMITER - UserRateLimiter initialise avec un delai de {self.delay}s par utilisateur")
    
    async def wait_for_user(self, user_ip: str):
        """
        Attendre si nécessaire pour respecter la limite de taux pour une IP utilisateur spécifique
        
        Args:
            user_ip (str): L'adresse IP de l'utilisateur faisant la requête
        """
        if not user_ip:
            logger.warning("⚠️ RATE LIMITER - Aucune IP utilisateur fournie pour le rate limiting, ignorer")
            return
            
        current_time = time.time()
        
        if user_ip in self.user_limits:
            time_since_last = current_time - self.user_limits[user_ip]
            if time_since_last < self.delay:
                sleep_time = self.delay - time_since_last
                logger.info(f"🚦 RATE LIMITER - Utilisateur {user_ip}: attente de {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)
        
        self.user_limits[user_ip] = time.time()
        logger.debug(f"✅ RATE LIMITER - Requete autorisee pour l'utilisateur {user_ip}")


# Instance globale du rate limiter pour les requêtes Nyaa
nyaa_rate_limiter = UserRateLimiter()