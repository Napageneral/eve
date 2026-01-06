from logging.config import fileConfig
import warnings
from sqlalchemy.exc import SAWarning

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Suppress warnings about duplicate table definitions
warnings.filterwarnings(
    "ignore",
    message=r"^Table '.*' already exists within the given MetaData",
    category=SAWarning
)

# Import models - imports should now work because of prepend_sys_path in alembic.ini
from backend.db.models import Base
from backend.db.context_models import Base as ContextBase
from backend.db.database import engine # Use the actual engine from database.py

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# All models ultimately subclass the same Base, so a simple reference is sufficient.
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def get_url():
    """Return the database URL from app config."""
    return f"sqlite:///{config.DB_PATH}"

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = str(engine.url) # Use the URL from the imported engine
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    # Use the engine directly from database.py
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
