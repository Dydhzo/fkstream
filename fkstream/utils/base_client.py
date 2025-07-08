class BaseClient:
    """
    Classe de base pour les clients qui nécessitent une fonctionnalité de fermeture asynchrone.
    Standardise la méthode close() pour réduire la duplication de code.
    """
    
    def __init__(self):
        self.client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def close(self):
        """Ferme le client et nettoie les ressources."""
        if hasattr(self, 'client') and self.client:
            if hasattr(self.client, 'close'):
                await self.client.close()
            elif hasattr(self.client, 'aclose'):
                await self.client.aclose()
            self.client = None
