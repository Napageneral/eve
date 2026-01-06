"""
Custom exceptions for repository operations.
"""

class RepositoryError(Exception):
    """Base exception for repository errors"""
    pass

class RecordNotFoundError(RepositoryError):
    """Raised when a requested record doesn't exist"""
    pass

class DuplicateRecordError(RepositoryError):
    """Raised when trying to create a duplicate record"""
    pass

class ValidationError(RepositoryError):
    """Raised when data validation fails"""
    pass

class DatabaseError(RepositoryError):
    """Raised when a database operation fails"""
    pass 