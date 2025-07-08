import logging
import httpx
import asyncio

from .models import settings
from .http_constants import DEFAULT_USER_AGENT, JSON_HEADERS
from .base_client import BaseClient


class HttpClient(BaseClient):
    """
    Client HTTP unifié avec configuration commune, nouvelles tentatives automatiques et gestion d'erreurs.
    """
    
    def __init__(self, base_url: str = "", timeout: float = 15.0, retries: int = 3, user_agent: str = None):
        super().__init__()
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.logger = logging.getLogger(f"http_client.{base_url}")
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._setup_client()
    
    def _setup_client(self):
        """Configure le client HTTP avec les options appropriées."""
        headers = JSON_HEADERS.copy()
        headers["User-Agent"] = self.user_agent
        
        proxy = settings.DEBRID_PROXY_URL if settings.DEBRID_PROXY_URL else None
        
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers=headers,
            follow_redirects=True,
            proxy=proxy
        )
    
    @property
    def is_closed(self) -> bool:
        """Vérifie si le client est fermé."""
        return self.client is None or self.client.is_closed
    
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Effectue une requête GET avec nouvelles tentatives automatiques."""
        return await self._request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Effectue une requête POST avec nouvelles tentatives automatiques."""
        return await self._request("POST", url, **kwargs)
    
    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Effectue une requête HTTP avec une logique de nouvelles tentatives et une gestion des erreurs.
        """
        if not url.startswith('http'):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"
        
        last_exception = None
        
        for attempt in range(self.retries):
            try:
                if self.is_closed:
                    self._setup_client()
                
                self.logger.debug(f"{method} {url} (tentative {attempt + 1}/{self.retries})")
                
                response = await self.client.request(method, url, **kwargs)
                response.raise_for_status()
                
                self.logger.debug(f"{method} {url} → {response.status_code}")
                return response
                
            except httpx.TimeoutException as e:
                last_exception = e
                self.logger.warning(f"{method} {url} timeout (tentative {attempt + 1}/{self.retries})")
                if attempt < self.retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    
            except httpx.HTTPStatusError as e:
                last_exception = e
                if e.response.status_code >= 500:
                    self.logger.warning(f"{method} {url} → {e.response.status_code} (tentative {attempt + 1}/{self.retries})")
                    if attempt < self.retries - 1:
                        await asyncio.sleep(1 * (attempt + 1))
                        continue
                else:
                    self.logger.error(f"{method} {url} → {e.response.status_code}")
                    raise
                    
            except Exception as e:
                last_exception = e
                self.logger.error(f"{method} {url} erreur: {str(e)} (tentative {attempt + 1}/{self.retries})")
                if attempt < self.retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
        
        self.logger.error(f"{method} {url} a echoue apres {self.retries} tentatives")
        if last_exception:
            raise last_exception
        else:
            raise httpx.RequestError(f"La requete a echoue apres {self.retries} tentatives")
