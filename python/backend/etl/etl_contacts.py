import sqlite3
import logging
import os
import glob
from typing import List, Dict, Optional
from backend.etl.utils import clean_contact_name, normalize_phone_number
from backend.db.session_manager import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

def etl_contacts(source_db: str, is_live: bool = False) -> int:
    """ETL contacts from a single source database."""
    source_type = 'live_addressbook' if is_live else 'backup_addressbook'
    logger.info(f"Starting ETL from {source_type}: {source_db}")
    
    ensure_user_contact()
    raw_contacts = extract_contacts(source_db, is_live)
    transformed_contacts = [transform_contact(c, source_type) for c in raw_contacts]
    imported_count = load_contacts(transformed_contacts)
            
    logger.info(f"Imported {imported_count} new contacts from {source_db}")
    return imported_count

def etl_live_contacts():
    """ETL contacts from all live AddressBook databases."""
    total_imported = 0
    for db_path in find_live_address_books():
        total_imported += etl_contacts(db_path, is_live=True)
    return total_imported

def get_address_book_path() -> str:
    home = os.path.expanduser('~')
    return os.path.join(home, 'Library', 'Application Support', 'AddressBook')

def find_live_address_books() -> List[str]:
    """Find all AddressBook databases in the live filesystem, including in Sources subdirectories."""
    db_files = []
    address_book_path = get_address_book_path()
    logger.info(f"Searching for AddressBook databases in: {address_book_path}")

    # Step 1: Use glob to find all AddressBook-v22.abcddb files recursively
    pattern = os.path.join(address_book_path, '**', 'AddressBook-v22.abcddb')
    try:
        glob_results = glob.glob(pattern, recursive=True)
        for db_path in glob_results:
            db_files.append(db_path)
            logger.info(f"Found AddressBook database (via glob): {db_path}")
    except Exception as e:
        logger.error(f"Error using glob to find AddressBook databases: {e}", exc_info=True)

    # Step 2: Explicitly check the Sources directory as a fallback
    sources_path = os.path.join(address_book_path, 'Sources')
    if os.path.exists(sources_path):
        logger.info(f"Sources directory found: {sources_path}")
        try:
            for root, dirs, files in os.walk(sources_path):
                for file in files:
                    if file == 'AddressBook-v22.abcddb':
                        db_path = os.path.join(root, file)
                        if db_path not in db_files:  # Avoid duplicates
                            db_files.append(db_path)
                            logger.info(f"Found additional AddressBook database in Sources: {db_path}")
        except Exception as e:
            logger.error(f"Error walking Sources directory: {e}", exc_info=True)
    else:
        logger.warning(f"Sources directory not found: {sources_path}")

    # Step 3: Check the root AddressBook directory explicitly
    root_db_path = os.path.join(address_book_path, 'AddressBook-v22.abcddb')
    if os.path.exists(root_db_path) and root_db_path not in db_files:
        db_files.append(root_db_path)
        logger.info(f"Found AddressBook database in root: {root_db_path}")

    # Step 4: Check for direct Sources database
    sources_db_path = os.path.join(sources_path, 'AddressBook-v22.abcddb')
    if os.path.exists(sources_db_path) and sources_db_path not in db_files:
        db_files.append(sources_db_path)
        logger.info(f"Found AddressBook database directly in Sources: {sources_db_path}")

    if not db_files:
        logger.warning("No AddressBook databases found. This may indicate permission issues or incorrect paths.")
    else:
        logger.info(f"Found {len(db_files)} live AddressBook database(s)")
    
    return db_files

def extract_contacts(source_db: str, is_live: bool) -> List[Dict]:
    """Extract contacts from source database."""
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            
            if is_live:
                required_tables = {'ZABCDRECORD', 'ZABCDPHONENUMBER', 'ZABCDMESSAGINGADDRESS'}
                query = """
                SELECT ZABCDRECORD.Z_PK as id, ZABCDRECORD.ZFIRSTNAME as first_name, ZABCDRECORD.ZLASTNAME as last_name, 
                    ZABCDPHONENUMBER.ZFULLNUMBER as identifier
                FROM ZABCDRECORD
                LEFT JOIN ZABCDPHONENUMBER ON ZABCDPHONENUMBER.ZOWNER = ZABCDRECORD.Z_PK
                WHERE ZABCDPHONENUMBER.ZFULLNUMBER IS NOT NULL
                UNION
                SELECT ZABCDRECORD.Z_PK as id, ZABCDRECORD.ZFIRSTNAME as first_name, ZABCDRECORD.ZLASTNAME as last_name, 
                    ZABCDMESSAGINGADDRESS.ZADDRESS as identifier
                FROM ZABCDRECORD
                LEFT JOIN ZABCDMESSAGINGADDRESS ON ZABCDMESSAGINGADDRESS.ZOWNER = ZABCDRECORD.Z_PK
                WHERE ZABCDMESSAGINGADDRESS.ZADDRESS IS NOT NULL
                """
            else:
                required_tables = {'ABPerson', 'ABMultiValue'}
                query = """
                SELECT ABPerson.ROWID as id, ABPerson.First as first_name, ABPerson.Last as last_name, 
                    ABMultiValue.value as identifier
                FROM ABPerson
                LEFT JOIN ABMultiValue ON ABMultiValue.record_id = ABPerson.ROWID
                WHERE ABMultiValue.value IS NOT NULL
                """
            
            missing_tables = required_tables - tables
            if missing_tables:
                logger.warning(f"Missing required tables in {source_db}: {missing_tables}")
                return []
            
            cursor.execute(query)
            contacts = [dict(row) for row in cursor.fetchall()]
            logger.info(f"Extracted {len(contacts)} contacts from {source_db}")
            return contacts
            
        except sqlite3.Error as e:
            logger.error(f"Error querying AddressBook database {source_db}: {e}", exc_info=True)
            return []

def transform_contact(contact: Dict, source: str) -> Dict:
    """Transform a single contact into the destination format."""
    name = clean_contact_name(f"{contact['first_name']} {contact['last_name']}".strip())
    identifier = contact['identifier']
    
    # Skip system/carrier contacts
    if (name.startswith('#') or 
        identifier.startswith('#') or 
        'VZ' in name or  # Skip Verizon contacts
        'Roadside' in name or 
        'Assistance' in name or
        name.startswith('*') or  # Skip other system contacts
        identifier.startswith('*')):
        return None
        
    identifier_type = 'Phone' if '@' not in identifier else 'Email'
    identifier = normalize_phone_number(identifier) if identifier_type == 'Phone' else identifier.lower()
    
    return {
        'name': name or identifier,
        'identifier': identifier,
        'identifier_type': identifier_type,
        'source': source
    }

def ensure_user_contact():
    """Ensure the user's own contact exists in the database."""
    with db.session_scope() as session:
        user_contact = session.execute(
            text("SELECT id FROM contacts WHERE is_me = 1")
        ).fetchone()
        if user_contact:
            return user_contact[0]
        
        # Create new user contact
        user_name = "Me"  # Could be made configurable
        session.execute(
            text("INSERT INTO contacts (name, is_me) VALUES (:name, :is_me)"),
            {"name": user_name, "is_me": True}
        )
        user_contact_id = session.execute(text("SELECT last_insert_rowid()")).scalar()
        
        # Add user identifier if available
        user_identifier = None  # Could be made configurable
        if user_identifier:
            identifier_type = 'Phone' if '@' not in user_identifier else 'Email'
            session.execute(
                text("""
                INSERT INTO contact_identifiers (contact_id, identifier, type, is_primary)
                VALUES (:contact_id, :identifier, :type, :is_primary)
                """),
                {
                    "contact_id": user_contact_id,
                    "identifier": user_identifier,
                    "type": identifier_type,
                    "is_primary": True
                }
            )
        
        return user_contact_id

def load_contacts(contacts: List[Dict]) -> int:
    contacts = [c for c in contacts if c is not None]
    print(f"\nAttempting to load {len(contacts)} contacts")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        # Build existing map keyed by normalized (identifier, type) → (contact_id, name)
        cursor.execute(
            """
            SELECT c.id, c.name, ci.identifier, ci.type 
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
            """
        )
        existing_by_ident = {}
        for cid, cname, ident, ctype in cursor.fetchall():
            if ident:
                existing_by_ident[(ident, ctype)] = (cid, cname or "")
        
        inserted = 0
        updated_names = 0
        
        for contact in contacts:
            key = (contact['identifier'], contact['identifier_type'])
            existing = existing_by_ident.get(key)
            if existing:
                cid, existing_name = existing
                # If existing name is missing or looks like a number/email/identifier, upgrade it
                existing_name = existing_name or ""
                clean = (existing_name
                         .replace("+", "")
                         .replace("-", "")
                         .replace(" ", "")
                         .replace("(", "")
                         .replace(")", ""))
                looks_like_number = clean.isdigit()
                needs_update = (not existing_name) or (existing_name == contact['identifier']) or looks_like_number
                if needs_update and contact['name'] and contact['name'] != existing_name:
                    cursor.execute("UPDATE contacts SET name = ? WHERE id = ?", (contact['name'], cid))
                    try:
                        from backend.etl.live_sync.sync_contacts import update_chat_names_for_contact
                        update_chat_names_for_contact(cid, contact['name'])
                    except Exception:
                        pass
                    updated_names += 1
                continue
            # New identifier → insert
            try:
                cursor.execute(
                    "INSERT INTO contacts (name, data_source) VALUES (?, ?)",
                    (contact['name'], contact['source'])
                )
                new_cid = cursor.lastrowid
                cursor.execute(
                    """
                    INSERT INTO contact_identifiers (contact_id, identifier, type, is_primary)
                    VALUES (?, ?, ?, 1)
                    """,
                    (new_cid, contact['identifier'], contact['identifier_type'])
                )
                existing_by_ident[key] = (new_cid, contact['name'])
                inserted += 1
            except Exception as e:
                print(f"Error inserting contact {contact['name']}: {e}")
                continue
        if updated_names:
            logger.info(f"Updated {updated_names} contact name(s) from AddressBook")
        return inserted

def load_single_contact(contact_data: Dict) -> Optional[int]:
    """
    Load a single contact into the database efficiently.
    Handles contact updates and merging properly.
    
    Args:
        contact_data: Dictionary containing contact data from AddressBook
        
    Returns:
        contact_id if successfully loaded, None otherwise
    """
    try:
        # Transform the contact data using existing transformation logic
        transformed = transform_contact(contact_data, 'live_addressbook')
        if not transformed:
            logger.debug(f"Contact transformation failed for: {contact_data}")
            return None
        
        with db.session_scope() as session:
            cursor = session.connection().connection.cursor()
            
            # Check if contact already exists by identifier
            cursor.execute("""
                SELECT c.id, c.name FROM contacts c
                JOIN contact_identifiers ci ON c.id = ci.contact_id
                WHERE ci.identifier = ? AND ci.type = ?
            """, (transformed['identifier'], transformed['identifier_type']))
            
            existing = cursor.fetchone()
            
            if existing:
                contact_id, existing_name = existing
                
                # Check if the existing contact has no real name (just phone/email)
                needs_name_update = (
                    not existing_name or 
                    existing_name == transformed['identifier'] or 
                    (existing_name.startswith('+') and 
                     existing_name.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit())
                )
                
                if needs_name_update:
                    # Update the name
                    cursor.execute(
                        "UPDATE contacts SET name = ? WHERE id = ?",
                        (transformed['name'], contact_id)
                    )
                    
                    logger.info(f"Updated contact {contact_id} name from '{existing_name}' to '{transformed['name']}'")
                    
                    # Update chat names for this contact
                    from backend.etl.live_sync.sync_contacts import update_chat_names_for_contact
                    update_chat_names_for_contact(contact_id, transformed['name'])
                    
                    # Update cache
                    from .live_sync.cache import CONTACT_MAP_CACHE
                    cache_key = transformed['identifier'].lower() if '@' in transformed['identifier'] else normalize_phone_number(transformed['identifier'])
                    CONTACT_MAP_CACHE[cache_key] = contact_id
                    
                    # Notify about the update (if we're in an async context)
                    try:
                        import asyncio
                        import threading
                        
                        if threading.current_thread() is threading.main_thread():
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    from backend.etl.live_sync.sync_contacts import notify_contact_update
                                    asyncio.create_task(notify_contact_update(
                                        contact_id, 
                                        "name_updated", 
                                        {"old_name": existing_name, "new_name": transformed['name']}
                                    ))
                            except RuntimeError:
                                pass  # No event loop available
                    except Exception as e:
                        logger.debug(f"Could not notify contact update: {e}")
                    
                    return contact_id
                
                # If names differ significantly, check for potential merge
                elif existing_name != transformed['name']:
                    # Check if we also have a contact with the new name
                    cursor.execute(
                        "SELECT id FROM contacts WHERE name = ? AND id != ?",
                        (transformed['name'], contact_id)
                    )
                    new_name_contact = cursor.fetchone()
                    
                    if new_name_contact:
                        # Merge: keep the one with the proper name
                        logger.info(f"Merging duplicate contacts: {contact_id} -> {new_name_contact[0]}")
                        
                        # Import the merge function
                        from backend.etl.live_sync.sync_contacts import merge_duplicate_contacts
                        merge_duplicate_contacts(contact_id, new_name_contact[0])
                        
                        # Update cache
                        from .live_sync.cache import CONTACT_MAP_CACHE
                        cache_key = transformed['identifier'].lower() if '@' in transformed['identifier'] else normalize_phone_number(transformed['identifier'])
                        CONTACT_MAP_CACHE[cache_key] = new_name_contact[0]
                        
                        return new_name_contact[0]
                    else:
                        # Just update the name
                        cursor.execute(
                            "UPDATE contacts SET name = ? WHERE id = ?",
                            (transformed['name'], contact_id)
                        )
                        logger.info(f"Updated contact {contact_id} name from '{existing_name}' to '{transformed['name']}'")
                        
                        # Update chat names for this contact
                        from backend.etl.live_sync.sync_contacts import update_chat_names_for_contact
                        update_chat_names_for_contact(contact_id, transformed['name'])
                        
                        return contact_id
                
                # Name is already correct, just update cache and return
                from .live_sync.cache import CONTACT_MAP_CACHE
                cache_key = transformed['identifier'].lower() if '@' in transformed['identifier'] else normalize_phone_number(transformed['identifier'])
                CONTACT_MAP_CACHE[cache_key] = contact_id
                
                return contact_id
                
            else:
                # Insert new contact
                cursor.execute(
                    "INSERT INTO contacts (name, data_source) VALUES (?, ?)",
                    (transformed['name'], transformed['source'])
                )
                contact_id = cursor.lastrowid
                
                # Insert contact identifier
                cursor.execute("""
                    INSERT INTO contact_identifiers 
                    (contact_id, identifier, type, is_primary)
                    VALUES (?, ?, ?, ?)
                """, (
                    contact_id, 
                    transformed['identifier'], 
                    transformed['identifier_type'], 
                    True
                ))
                
                # Update cache
                from .live_sync.cache import CONTACT_MAP_CACHE
                cache_key = transformed['identifier'].lower() if '@' in transformed['identifier'] else normalize_phone_number(transformed['identifier'])
                CONTACT_MAP_CACHE[cache_key] = contact_id
                
                logger.info(f"Created new contact with ID {contact_id} for {transformed['name']} ({transformed['identifier']})")
                return contact_id
                
    except Exception as e:
        logger.error(f"Error loading single contact: {e}", exc_info=True)
        return None


def find_contact_by_identifier(identifier: str) -> Optional[Dict]:
    """Find a contact by their identifier in the database."""
    identifier = normalize_phone_number(identifier)
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("""
            SELECT c.id, c.name, ci.identifier, ci.type
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
            WHERE ci.identifier = ?
        """, (identifier,))
        result = cursor.fetchone()
        
        if result:
            return dict(zip(['id', 'name', 'identifier', 'type'], result))
        return None