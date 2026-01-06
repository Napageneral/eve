"""User Repository - Database operations for users and user contacts."""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from .core.generic import GenericRepository

logger = logging.getLogger(__name__)


class UserRepository(GenericRepository):
    """Repository for all user and user contact database operations"""
    
    TABLE = "users"
    
    @classmethod
    def update_user_contact_name(cls, session: Session, name: str) -> Dict[str, Any]:
        """Update user contact name."""
        new_name = name.strip()
        if not new_name:
            raise ValueError("No name provided")
            
        # Update contact name
        contact_update_result = session.execute(text("""
            UPDATE contacts
            SET name = :name, last_updated = datetime('now')
            WHERE is_me = TRUE
            RETURNING id, name
        """), {"name": new_name})
        
        updated_contact_row = contact_update_result.fetchone()
        if not updated_contact_row:
            raise ValueError("User contact (is_me=TRUE) not found")
        
        # Sync to user table
        user_id_to_ensure = 1
        contact_name = updated_contact_row.name

        # Check if user exists
        if not cls.exists(session, id=user_id_to_ensure):
            # Create user record
            from datetime import datetime
            current_time = datetime.utcnow()
            cls.create(session, {
                "id": user_id_to_ensure,
                "username": contact_name,
                "created_at": current_time,
                "updated_at": current_time
            })
        else:
            # Update existing user record
            cls.update(session, user_id_to_ensure, {"username": contact_name})
        
        return {
            "success": True,
            "contact_id": updated_contact_row.id,
            "name": updated_contact_row.name
        }
    
    @classmethod
    def get_user_contact_id(cls, session: Session) -> Optional[int]:
        """Get user contact ID."""
        return cls.fetch_scalar(session, "SELECT id FROM contacts WHERE is_me = TRUE")
    
    @classmethod
    def get_user_primary_identifier(cls, session: Session) -> Optional[Dict[str, str]]:
        """Retrieve the primary identifier(s) (phone/email) for the current user contact (`is_me = 1`).

        Returns a dict with keys `phone` and/or `email`, or `None` if no identifiers are found.
        Consolidated from duplicate definitions during Phase 2 cleanup.
        """
        # Find the contact marked as 'is_me'
        me_contact = cls.fetch_one(session, "SELECT id FROM contacts WHERE is_me = 1 LIMIT 1")
        
        if not me_contact:
            return None
        
        contact_id = me_contact['id']
        identifiers = {}
        
        try:
            # Get phone number
            phone_identifier = cls.fetch_scalar(session, """
                SELECT identifier
                FROM contact_identifiers 
                WHERE contact_id = :contact_id AND type = 'Phone'
                LIMIT 1
            """, {"contact_id": contact_id})
            
            if phone_identifier:
                identifiers['phone'] = phone_identifier
            
            # Get email
            email_identifier = cls.fetch_scalar(session, """
                SELECT identifier
                FROM contact_identifiers 
                WHERE contact_id = :contact_id AND type = 'Email'
                LIMIT 1
            """, {"contact_id": contact_id})
            
            if email_identifier:
                identifiers['email'] = email_identifier
                
        except Exception as identifier_error:
            logger.error(f"Error retrieving identifiers: {str(identifier_error)}")
            
            # Try alternative schema where identifiers might be directly in contacts table
            try:
                contact_data = cls.fetch_one(session, """
                    SELECT phone_number, email
                    FROM contacts
                    WHERE id = :contact_id
                    LIMIT 1
                """, {"contact_id": contact_id})
                
                if contact_data:
                    if contact_data.get('phone_number'):
                        identifiers['phone'] = contact_data['phone_number']
                    if contact_data.get('email'):
                        identifiers['email'] = contact_data['email']
                        
            except Exception as alt_error:
                logger.error(f"Error retrieving identifiers with alternative schema: {str(alt_error)}")
        
        return identifiers if identifiers else None
    
    @classmethod
    def create_or_update_user_contact(cls, session: Session, name: str) -> Dict[str, Any]:
        """Create or update user contact."""
        # Determine which name column to use (name or display_name)
        name_column = "name"
        
        try:
            session.execute(text("SELECT name FROM contacts LIMIT 1"))
        except Exception:
            try:
                session.execute(text("SELECT display_name FROM contacts LIMIT 1"))
                name_column = "display_name"
            except Exception:
                return {
                    "success": False,
                    "error": "Database schema incompatible - no suitable name column found in contacts table"
                }
        
        # Check if user contact already exists
        existing_contact = cls.fetch_one(session, "SELECT id FROM contacts WHERE is_me = 1 LIMIT 1")
        
        if existing_contact:
            contact_id = existing_contact['id']
            
            # Update existing contact
            session.execute(
                text(f"""
                    UPDATE contacts 
                    SET {name_column} = :name, last_updated = datetime('now')
                    WHERE id = :contact_id
                """),
                {"name": name, "contact_id": contact_id}
            )
        else:
            # Create new contact
            result = session.execute(
                text(f"""
                    INSERT INTO contacts ({name_column}, is_me, last_updated)
                    VALUES (:name, 1, datetime('now'))
                """),
                {"name": name}
            )
            
            contact_id = result.lastrowid
        
        return {"success": True, "contactId": contact_id}
    

    
    @classmethod
    def get_me_contact_id(cls, session: Session) -> int:
        """Get the user's contact ID, with fallback to 1."""
        # Import here to avoid circular imports
        from .contacts import ContactRepository
        
        contact = ContactRepository.get_user_contact(session)
        return contact["id"] if contact else 1 