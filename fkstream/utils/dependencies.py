from fastapi import Request, Depends

from .http_client import HttpClient
from fkstream.scrapers.fankai import FankaiAPI


def get_http_client(request: Request) -> HttpClient:
    """
    Dépendance pour obtenir l'instance partagée de HttpClient.
    """
    return request.app.state.http_client


def get_fankai_api(client: HttpClient = Depends(get_http_client)) -> FankaiAPI:
    """
    Dépendance pour obtenir une instance de FankaiAPI avec un HttpClient partagé.
    """
    return FankaiAPI(client)
