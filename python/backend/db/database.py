from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker
from backend.db.models import Base
import os
# Use consolidated configuration
from backend.config import DB_PATH
import logging
from backend.db.cleanup import initialize_database_safely, check_database_health

logger = logging.getLogger(__name__)

def init_engine(database_url=None, clean_start=False):
    if database_url is None:
        # Check if your config.DB_PATH is set from environment
        db_path = DB_PATH
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        database_url = f"sqlite:///{db_path}"

        # Use our cleanup utility to ensure database is properly initialized
        try:
            initialize_database_safely(db_path, clean_start=clean_start)
        except Exception as e:
            logger.error(f"Failed to initialize database safely: {e}")
            raise

    # For SQLite, avoid QueuePool contention and allow cross-thread usage
    is_sqlite = database_url.startswith('sqlite:')
    # Busy timeout (ms) used for PRAGMA and native connect timeout
    try:
        busy_ms = int(os.getenv("EVE_SQLITE_BUSY_TIMEOUT_MS") or os.getenv("CHATSTATS_SQLITE_BUSY_TIMEOUT_MS") or "15000")
    except Exception:
        busy_ms = 15000
    engine = create_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=3600,   # Recycle connections every hour
        poolclass=NullPool if is_sqlite else None,  # SQLite connections are lightweight; avoid pooling contention
        connect_args={
            "check_same_thread": False,
            "timeout": (busy_ms / 1000.0),
        } if is_sqlite else {},
    )
    logger.info("Initialized engine with database at %s", database_url)
    
    # Additional SQLite optimization for better concurrent access
    if database_url.startswith('sqlite:'):
        with engine.connect() as conn:
            # These should already be set by our cleanup utility, but ensure they're applied
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text(f"PRAGMA busy_timeout={busy_ms}"))  # increased busy timeout
            conn.execute(text("PRAGMA synchronous=NORMAL"))  # Balance safety and performance
            conn.execute(text("PRAGMA cache_size=-64000"))   # 64MB cache
            conn.execute(text("PRAGMA temp_store=MEMORY"))   # Use memory for temporary tables
            conn.execute(text("PRAGMA mmap_size=268435456")) # 256MB memory-mapped I/O
            conn.commit()
            logger.info("Applied SQLite optimizations for concurrent access")
    
    # Removed: Base.metadata.create_all(engine)  # Let Alembic handle schema creation
    return engine

def init_session_factory(engine):
    # Return a plain session factory; callers should create a new Session per request
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def create_test_database(test_db_path, force_recreate=False):
    os.makedirs(os.path.dirname(test_db_path), exist_ok=True)
    if os.path.exists(test_db_path) and not force_recreate:
        logger.info(f"Using existing test database at {test_db_path}")
        # Ensure schema is up to date even for existing DB
        test_engine = init_engine(f"sqlite:///{test_db_path}")
        Base.metadata.create_all(test_engine)
        return test_engine
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        logger.info(f"Removed existing test database at {test_db_path}")
    test_engine = init_engine(f"sqlite:///{test_db_path}")
    Base.metadata.create_all(test_engine)
    logger.info(f"Created new test database at {test_db_path}")
    return test_engine

def drop_test_database(test_db_path):
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        logger.info(f"Dropped test database at {test_db_path}")

from contextlib import contextmanager

@contextmanager
def get_session(Session):
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

# Initialize the engine and session for production use
engine = init_engine()
Session = init_session_factory(engine)
