import signal
import threading
import time
import uvicorn
import os
import asyncio

from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from fkstream.api.core import main as core_router
from fkstream.api.stream import streams as stream_router
from fkstream.utils.database import (
    setup_database,
    teardown_database,
    cleanup_expired_locks,
)
from fkstream.utils.http_client import HttpClient
from fkstream.utils.common_logger import logger
from fkstream.utils.models import settings


class LoguruMiddleware(BaseHTTPMiddleware):
    """
    Middleware pour enregistrer les requêtes HTTP avec Loguru.
    """
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = None
        status_code = 500
        
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            logger.exception(f"Exception durant le traitement de la requete: {e}")
            raise
        finally:
            process_time = time.time() - start_time
            logger.log(
                "API",
                f"{request.method} {request.url.path} - {status_code} - {process_time:.2f}s",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gère le cycle de vie de l'application FastAPI.
    Initialise et nettoie les ressources telles que la base de données et le client HTTP.
    """
    await setup_database()
    
    try:
        app.state.http_client = HttpClient()
        logger.debug("Client HTTP initialise avec succes")
    except Exception as e:
        logger.error(f"Echec de l'initialisation du client HTTP: {e}")
        raise RuntimeError(f"L'initialisation du client HTTP a echoue: {e}")

    cleanup_task = asyncio.create_task(cleanup_expired_locks())

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        
        await app.state.http_client.close()
        await teardown_database()


app = FastAPI(
    title="FKStream",
    summary="FKStream – Addon non officiel pour accéder au contenu de Fankai",
    lifespan=lifespan,
    redoc_url=None,
)

app.add_middleware(LoguruMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = "fkstream/templates"
if os.path.exists(static_dir) and os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.debug(f"Fichiers statiques montes depuis {static_dir}")
else:
    logger.warning(f"Repertoire statique {static_dir} non trouve, les fichiers statiques ne seront pas disponibles")

app.include_router(core_router)
app.include_router(stream_router)


class Server(uvicorn.Server):
    """
    Serveur Uvicorn personnalisé pour permettre un démarrage dans un thread séparé.
    """
    def install_signal_handlers(self):
        pass

    @contextmanager
    def run_in_thread(self):
        thread = threading.Thread(target=self.run, name="FKStream")
        thread.start()
        try:
            while not self.started:
                time.sleep(1e-3)
            yield
        except Exception as e:
            logger.error(f"Erreur dans le thread du serveur: {e}")
            raise e
        finally:
            self.should_exit = True
            raise SystemExit(0)


def signal_handler(sig, frame):
    logger.log("FKSTREAM", "Arret en cours...")
    import sys
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def start_log():
    """Affiche les logs de configuration au démarrage."""
    logger.log(
        "FKSTREAM",
        f"Serveur demarre sur http://{settings.FASTAPI_HOST}:{settings.FASTAPI_PORT} - {settings.FASTAPI_WORKERS} workers",
    )
    logger.log(
        "FKSTREAM",
        f"Base de donnees ({settings.DATABASE_TYPE}): {settings.DATABASE_PATH if settings.DATABASE_TYPE == 'sqlite' else settings.DATABASE_URL} - TTL: metadata={settings.METADATA_TTL}s, debrid={settings.DEBRID_AVAILABILITY_TTL}s",
    )
    logger.log("FKSTREAM", f"Proxy Debrid: {settings.DEBRID_PROXY_URL}")
    logger.log("FKSTREAM", f"Timeout pour recuperer le torrent: {settings.GET_TORRENT_TIMEOUT}s")
    logger.log(
        "FKSTREAM",
        f"Proxy de stream Debrid: {bool(settings.PROXY_DEBRID_STREAM)} - Service Debrid par defaut: {settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_SERVICE} - Cle API Debrid par defaut: {'*' * 8 if settings.PROXY_DEBRID_STREAM_DEBRID_DEFAULT_APIKEY else 'Non definie'}",
    )
    logger.log("FKSTREAM", f"URL StremThru: {settings.STREMTHRU_URL}")
    logger.log("FKSTREAM", f"HTML d'en-tete personnalise: {bool(settings.CUSTOM_HEADER_HTML)}")


def run_with_uvicorn():
    """Exécute le serveur avec Uvicorn uniquement."""
    config = uvicorn.Config(
        app,
        host=settings.FASTAPI_HOST,
        port=settings.FASTAPI_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=settings.FASTAPI_WORKERS,
        log_config=None,
    )
    server = Server(config=config)

    with server.run_in_thread():
        start_log()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.log("FKSTREAM", "Serveur arrete par l'utilisateur")
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}")
            logger.exception("Erreur inattendue")
        finally:
            logger.log("FKSTREAM", "Arret du serveur")


def run_with_gunicorn():
    """Exécute le serveur avec Gunicorn et les workers Uvicorn."""
    import gunicorn.app.base

    class StandaloneApplication(gunicorn.app.base.BaseApplication):
        def __init__(self, app, options=None):
            self.options = options or {}
            self.application = app
            super().__init__()

        def load_config(self):
            config = {
                key: value
                for key, value in self.options.items()
                if key in self.cfg.settings and value is not None
            }
            for key, value in config.items():
                self.cfg.set(key.lower(), value)

        def load(self):
            return self.application

    workers = settings.FASTAPI_WORKERS
    if workers < 1:
        workers = min((os.cpu_count() or 1) * 2 + 1, 12)

    options = {
        "bind": f"{settings.FASTAPI_HOST}:{settings.FASTAPI_PORT}",
        "workers": workers,
        "worker_class": "uvicorn.workers.UvicornWorker",
        "timeout": 120,
        "keepalive": 5,
        "preload_app": True,
        "proxy_protocol": True,
        "forwarded_allow_ips": "*",
        "loglevel": "warning",
    }

    start_log()
    logger.log("FKSTREAM", f"Demarrage avec Gunicorn et {workers} workers")

    StandaloneApplication(app, options).run()


if __name__ == "__main__":
    if os.name == "nt" or not settings.USE_GUNICORN:
        run_with_uvicorn()
    else:
        run_with_gunicorn()
