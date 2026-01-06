import threading
from contextlib import contextmanager
from sqlalchemy.orm import scoped_session, sessionmaker
from .database import init_engine, init_session_factory

class SessionManager:
    """Manages database sessions across the application."""
    
    _instance = None
    _lock = threading.Lock()
    _thread_local = threading.local()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_default_session()
        return cls._instance
    
    def _init_default_session(self):
        """Initialize the default production session."""
        self.engine = init_engine()  # Store engine as instance variable
        self._default_session_factory = init_session_factory(self.engine)
        self._test_session_factory = None
    
    def set_test_mode(self, test_db_path=None):
        """Switch to test database."""
        if test_db_path:
            self.engine = init_engine(f"sqlite:///{test_db_path}")  # Update engine
            self._test_session_factory = init_session_factory(self.engine)
    
    def clear_test_mode(self):
        """Switch back to production database."""
        self._test_session_factory = None
    
    @property
    def session(self):
        """Get a NEW short-lived session each call.

        Refactor away from thread-local cached sessions to avoid connection pool
        exhaustion under Celery concurrency. Callers should use a contextmanager
        and let it close promptly.
        """
        factory = self._test_session_factory or self._default_session_factory
        return factory()
    
    def close_thread_session(self):
        """Close the session for the current thread."""
        if hasattr(self._thread_local, 'session'):
            self._thread_local.session.close()
            delattr(self._thread_local, 'session')
    
    @contextmanager
    def session_scope(self):
        """
        Provide a transactional scope around a series of operations.
        Creates a new session if one doesn't exist, otherwise uses the existing session.
        """
        session = self.session
        try:
            yield session
            session.commit()
        except:
            session.rollback()
            raise
        finally:
            session.close()

# Global instance
db = SessionManager()

@contextmanager
def new_session():
    """Provide a transactional scope using a new, non-cached session."""
    # Uses the engine from the global `db` instance
    if db.engine is None:
        # This case should ideally not happen if db is initialized properly
        raise RuntimeError("Database engine not initialized in SessionManager.")
    
    SessionLocal = sessionmaker(bind=db.engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

# FastAPI dependency for database sessions
def get_db():
    """FastAPI dependency yielding a short-lived session per request."""
    session = db.session
    try:
        yield session
    finally:
        session.close()
