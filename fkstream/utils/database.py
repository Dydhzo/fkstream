import os
import time
import json
import asyncio

from fkstream.utils.common_logger import logger
from fkstream.utils.models import database, settings

DATABASE_VERSION = "1.1"


async def setup_database():
    """
    Initialise la base de données, effectue les migrations si nécessaire et nettoie les anciennes entrées.
    """
    try:
        if settings.DATABASE_TYPE == "sqlite":
            os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
            if not os.path.exists(settings.DATABASE_PATH):
                open(settings.DATABASE_PATH, "a").close()

        await database.connect()

        await database.execute("CREATE TABLE IF NOT EXISTS db_version (id INTEGER PRIMARY KEY CHECK (id = 1), version TEXT)")
        current_version = await database.fetch_val("SELECT version FROM db_version WHERE id = 1")

        if current_version != DATABASE_VERSION:
            logger.log("FKSTREAM", f"Base de donnees: Migration de la version {current_version} a {DATABASE_VERSION}")

            if settings.DATABASE_TYPE == "sqlite":
                allowed_tables = {'scrape_lock', 'metadata', 'debrid_availability'}
                tables = await database.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('db_version', 'sqlite_sequence')")
                for table in tables:
                    table_name = table['name']
                    if table_name not in allowed_tables:
                        logger.warning(f"Table non autorisee ignoree pendant la migration: {table_name}")
                        continue
                    if not table_name.replace('_', '').isalnum() or len(table_name) > 64:
                        logger.warning(f"Table avec un format de nom invalide ignoree: {table_name}")
                        continue
                    await database.execute(f"DROP TABLE IF EXISTS {table_name}")
                    logger.info(f"🗑️ Table supprimee pendant la migration: {table_name}")
            else:
                await database.execute("""
                    DO $$ DECLARE r RECORD;
                    BEGIN
                        FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename != 'db_version') LOOP
                            EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
                        END LOOP;
                    END $$;
                """)

            if settings.DATABASE_TYPE == "sqlite":
                await database.execute("INSERT OR REPLACE INTO db_version VALUES (1, :version)", {"version": DATABASE_VERSION})
            else:
                await database.execute("INSERT INTO db_version VALUES (1, :version) ON CONFLICT (id) DO UPDATE SET version = :version", {"version": DATABASE_VERSION})
            logger.log("FKSTREAM", f"Base de donnees: Migration vers la version {DATABASE_VERSION} terminee")

        await database.execute("CREATE TABLE IF NOT EXISTS scrape_lock (lock_key TEXT PRIMARY KEY, instance_id TEXT, timestamp INTEGER, expires_at INTEGER)")
        await database.execute("CREATE TABLE IF NOT EXISTS metadata (media_id TEXT PRIMARY KEY, media_data TEXT, timestamp REAL NOT NULL, expires_at REAL)")
        await database.execute("CREATE TABLE IF NOT EXISTS debrid_availability (media_id TEXT NOT NULL, hash TEXT NOT NULL, debrid_service TEXT NOT NULL, status TEXT NOT NULL, timestamp REAL NOT NULL, expires_at REAL, PRIMARY KEY (media_id, hash, debrid_service))")

        if settings.DATABASE_TYPE == "sqlite":
            await database.execute("PRAGMA busy_timeout=30000")
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute("PRAGMA synchronous=NORMAL")
            await database.execute("PRAGMA temp_store=MEMORY")
            await database.execute("PRAGMA cache_size=-2000")
            await database.execute("PRAGMA foreign_keys=ON")

        current_time = time.time()
        cleanup_tasks = [
            database.execute("DELETE FROM metadata WHERE expires_at IS NOT NULL AND expires_at < :current_time;", {"current_time": current_time}),
            database.execute("DELETE FROM debrid_availability WHERE expires_at IS NOT NULL AND expires_at < :current_time;", {"current_time": current_time}),
        ]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    except Exception as e:
        logger.error(f"Erreur lors de la configuration de la base de donnees: {e}")


async def cleanup_expired_locks():
    """Tâche de nettoyage périodique pour les verrous expirés."""
    while True:
        try:
            current_time = int(time.time())
            await database.execute("DELETE FROM scrape_lock WHERE expires_at < :current_time", {"current_time": current_time})
        except Exception as e:
            logger.log("LOCK", f"❌ Erreur lors du nettoyage periodique des verrous: {e}")
        await asyncio.sleep(60)


async def get_metadata_from_cache(media_id: str):
    """Récupère les métadonnées depuis le cache."""
    current_time = time.time()
    query = "SELECT media_data FROM metadata WHERE media_id = :media_id AND expires_at > :current_time"
    result = await database.fetch_one(query, {"media_id": media_id, "current_time": current_time})
    if not result or not result["media_data"]:
        return None
    try:
        return json.loads(result["media_data"])
    except json.JSONDecodeError:
        return None


async def set_metadata_to_cache(media_id: str, data, ttl: int = None):
    """Stocke les métadonnées dans le cache."""
    current_time = time.time()
    expires_at = current_time + (ttl if ttl is not None else settings.METADATA_TTL)
    if settings.DATABASE_TYPE == "sqlite":
        query = "INSERT OR REPLACE INTO metadata (media_id, media_data, timestamp, expires_at) VALUES (:media_id, :media_data, :timestamp, :expires_at)"
    else:
        query = "INSERT INTO metadata (media_id, media_data, timestamp, expires_at) VALUES (:media_id, :media_data, :timestamp, :expires_at) ON CONFLICT (media_id) DO UPDATE SET media_data = :media_data, timestamp = :timestamp, expires_at = :expires_at"
    values = {"media_id": media_id, "media_data": json.dumps(data), "timestamp": current_time, "expires_at": expires_at}
    await database.execute(query, values)


async def get_debrid_from_cache(media_id: str, hash: str, debrid_service: str):
    """Récupère le statut de disponibilité debrid depuis le cache."""
    current_time = time.time()
    query = "SELECT status FROM debrid_availability WHERE media_id = :media_id AND hash = :hash AND debrid_service = :debrid_service AND (expires_at IS NULL OR expires_at > :current_time)"
    values = {"media_id": media_id, "hash": hash, "debrid_service": debrid_service, "current_time": current_time}
    result = await database.fetch_one(query, values)
    return {"status": result["status"]} if result else None


async def save_debrid_to_cache(media_id: str, hash: str, debrid_service: str, status: str):
    """Sauvegarde le statut de disponibilité debrid dans le cache."""
    current_time = time.time()
    expires_at = current_time + settings.DEBRID_AVAILABILITY_TTL
    if settings.DATABASE_TYPE == "sqlite":
        query = "INSERT OR REPLACE INTO debrid_availability (media_id, hash, debrid_service, status, timestamp, expires_at) VALUES (:media_id, :hash, :debrid_service, :status, :timestamp, :expires_at)"
    else:
        query = "INSERT INTO debrid_availability (media_id, hash, debrid_service, status, timestamp, expires_at) VALUES (:media_id, :hash, :debrid_service, :status, :timestamp, :expires_at) ON CONFLICT (media_id, hash, debrid_service) DO UPDATE SET status = :status, timestamp = :timestamp, expires_at = :expires_at"
    values = {"media_id": media_id, "hash": hash, "debrid_service": debrid_service, "status": status, "timestamp": current_time, "expires_at": expires_at}
    await database.execute(query, values)



async def acquire_lock(lock_key: str, instance_id: str, duration: int = None) -> bool:
    """
    Acquiert un verrou distribué pour la clé donnée.
    Retourne True si le verrou est acquis, False s'il est déjà verrouillé.
    """
    try:
        current_time = int(time.time())
        lock_duration = duration if duration is not None else settings.SCRAPE_LOCK_TTL
        expires_at = current_time + lock_duration
        
        if settings.DATABASE_TYPE == "sqlite":
            query = "INSERT OR IGNORE INTO scrape_lock (lock_key, instance_id, timestamp, expires_at) VALUES (:lock_key, :instance_id, :timestamp, :expires_at)"
        else:
            query = "INSERT INTO scrape_lock (lock_key, instance_id, timestamp, expires_at) VALUES (:lock_key, :instance_id, :timestamp, :expires_at) ON CONFLICT (lock_key) DO NOTHING"
        await database.execute(query, {"lock_key": lock_key, "instance_id": instance_id, "timestamp": current_time, "expires_at": expires_at})
        
        existing_lock = await database.fetch_one("SELECT instance_id, expires_at FROM scrape_lock WHERE lock_key = :lock_key", {"lock_key": lock_key})
        
        if existing_lock:
            if existing_lock["expires_at"] < current_time:
                deleted = await database.execute("DELETE FROM scrape_lock WHERE lock_key = :lock_key AND expires_at < :current_time", {"lock_key": lock_key, "current_time": current_time})
                return await acquire_lock(lock_key, instance_id, duration) if deleted else False
            if existing_lock["instance_id"] == instance_id:
                logger.log("LOCK", f"✅ Verrou acquis: {lock_key}")
                return True
            else:
                logger.log("LOCK", f"❌ Verrou deja detenu par une autre instance: {lock_key}")
                return False
        
        logger.log("LOCK", f"✅ Verrou acquis: {lock_key}")
        return True
    except Exception as e:
        logger.warning(f"Echec de l'acquisition du verrou {lock_key}: {e}")
        return False


async def release_lock(lock_key: str, instance_id: str) -> bool:
    """
    Libère un verrou distribué pour la clé donnée.
    Retourne True si le verrou est libéré, False s'il n'appartient pas à cette instance.
    """
    try:
        await database.execute("DELETE FROM scrape_lock WHERE lock_key = :lock_key AND instance_id = :instance_id", {"lock_key": lock_key, "instance_id": instance_id})
        logger.log("LOCK", f"🔓 Verrou libere: {lock_key}")
        return True
    except Exception as e:
        logger.warning(f"Echec de la liberation du verrou {lock_key}: {e}")
        return False


class DistributedLock:
    """Gestionnaire de contexte pour le verrouillage distribué."""
    def __init__(self, lock_key: str, instance_id: str = None, duration: int = None):
        self.lock_key = lock_key
        self.instance_id = instance_id or f"fkstream_{int(time.time())}"
        self.duration = duration if duration is not None else settings.SCRAPE_LOCK_TTL
        self.acquired = False
    
    async def __aenter__(self):
        start_time = time.time()
        timeout = settings.SCRAPE_WAIT_TIMEOUT
        
        while time.time() - start_time < timeout:
            self.acquired = await acquire_lock(self.lock_key, self.instance_id, self.duration)
            if self.acquired:
                logger.log("LOCK", f"✅ Verrou acquis pour {self.lock_key} apres {time.time() - start_time:.2f}s d'attente.")
                return self
            
            logger.log("LOCK", f"⏳ Attente du verrou {self.lock_key}...")
            await asyncio.sleep(1)
            
        raise LockAcquisitionError(f"Impossible d'acquerir le verrou {self.lock_key} apres {timeout}s")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            await release_lock(self.lock_key, self.instance_id)


class LockAcquisitionError(Exception):
    """Levée lorsqu'un verrou ne peut pas être acquis."""
    pass


async def teardown_database():
    """Ferme la connexion à la base de données."""
    try:
        await database.disconnect()
    except Exception as e:
        logger.error(f"Erreur lors de la fermeture de la base de donnees: {e}")
