# Celery package 
from .app import celery_app

# Export as 'app' so celery command can find it
app = celery_app

__all__ = ['app', 'celery_app'] 