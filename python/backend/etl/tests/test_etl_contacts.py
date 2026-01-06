from backend.etl.etl_contacts import (etl_contacts, etl_live_contacts, extract_contacts, get_address_book_path, transform_contact, load_contacts, find_contact_by_identifier, find_live_address_books, ensure_user_contact)
from backend.etl.iphone_backup import get_first_available_backup, get_address_book_db_path
from backend.test.test_utils import run_tests
from backend.db.session_manager import db
import sqlite3

def test_extract_backup_contacts():
    backup_path = get_first_available_backup()
    db_path = get_address_book_db_path(backup_path)
    contacts = extract_contacts(db_path, False)
    print(f"\nExtracted backup contacts count: {len(contacts)}")
    if len(contacts) > 0: print(f"Sample contact: {contacts[0]}")
    assert len(contacts) > 0

def test_extract_live_contacts():
    live_dbs = find_live_address_books()
    print(f"\nFound live address books: {live_dbs}")
    if not live_dbs:
        print("No live address books found!")
        print(f"Checking path: {get_address_book_path()}")
        return
    total_contacts = 0
    for db_path in live_dbs:
        print(f"\nTrying to extract from: {db_path}")
        contacts = extract_contacts(db_path, True)
        contact_count = len(contacts)
        total_contacts += contact_count
        print(f"Extracted contacts count: {contact_count}")
        if contact_count > 0: print(f"Sample contact: {contacts[0]}")
    print(f"\nTotal contacts found across all DBs: {total_contacts}")
    assert total_contacts > 0

def test_transform_contact():
    backup_path = get_first_available_backup()
    db_path = get_address_book_db_path(backup_path)
    contacts = extract_contacts(db_path, False)
    if len(contacts) > 0:
        print(f"\nTotal contacts to transform: {len(contacts)}")
        transformed_contacts = []
        for contact in contacts:
            transformed = transform_contact(contact, 'backup_addressbook')
            if transformed is not None:
                transformed_contacts.append(transformed)
                assert transformed['name'] is not None
                assert transformed['identifier'] is not None
                assert transformed['identifier_type'] in ['Phone', 'Email']
        print(f"\nTransformed {len(transformed_contacts)} valid contacts out of {len(contacts)} total")
        if transformed_contacts: print(f"Sample transformed contact: {transformed_contacts[0]}")

def test_load_contacts():
    backup_path = get_first_available_backup()
    db_path = get_address_book_db_path(backup_path)
    contacts = extract_contacts(db_path, False)
    print(f"\nExtracted contacts count: {len(contacts)}")
    if len(contacts) > 0: print(f"First raw contact: {contacts[0]}")
    transformed_contacts = [c for c in [transform_contact(c, 'backup_addressbook') for c in contacts[:5]] if c is not None]
    print(f"\nTransformed contacts:")
    for tc in transformed_contacts: print(f"- {tc}")
    count = load_contacts(transformed_contacts)
    print(f"\nLoaded contacts count: {count}")
    assert count >= 0

def test_find_contact():
    backup_path = get_first_available_backup()
    db_path = get_address_book_db_path(backup_path)
    contacts = extract_contacts(db_path, False)
    if len(contacts) > 0:
        valid_contact = next((transform_contact(c, 'backup_addressbook') for c in contacts if transform_contact(c, 'backup_addressbook') is not None), None)
        if valid_contact is None:
            print("\nNo valid contacts found to test")
            return
        load_contacts([valid_contact])
        found = find_contact_by_identifier(valid_contact['identifier'])
        print(f"\nSearching for contact with identifier: {valid_contact['identifier']}")
        print(f"Found contact: {found}")
        assert found is not None
        assert found['identifier'] == valid_contact['identifier']

def test_user_contact():
    user_id = ensure_user_contact()
    print(f"\nUser contact ID: {user_id}")
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT id, name, is_me FROM contacts WHERE id = ?", (user_id,))
        user = dict(zip(['id', 'name', 'is_me'], cursor.fetchone()))
        print(f"User contact: {user}")
        cursor.execute("SELECT identifier, type, is_primary FROM contact_identifiers WHERE contact_id = ?", (user_id,))
        identifiers = [dict(zip(['identifier', 'type', 'is_primary'], row)) for row in cursor.fetchall()]
        print(f"User identifiers: {identifiers}")
        assert user is not None
        assert user['is_me'] == 1
        assert user['name'] == 'Me'

def test_etl_contacts():
    # Test live ETL
    live_imported = etl_live_contacts()
    print(f"Imported {live_imported} contacts from live DBs")

    # Test backup ETL
    backup_path = get_first_available_backup()
    db_path = get_address_book_db_path(backup_path)
    imported_count = etl_contacts(db_path, is_live=False)
    print(f"\nImported {imported_count} contacts from backup")
    
    # Verify results
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM contacts")
        total_contacts = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM contact_identifiers")
        total_identifiers = cursor.fetchone()[0]
        print(f"\nFinal database state:")
        print(f"Total contacts: {total_contacts}")
        print(f"Total identifiers: {total_identifiers}")
        
        # Sample some contacts
        cursor.execute("""
            SELECT c.name, c.data_source, ci.identifier, ci.type
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
            LIMIT 5
        """)
        sample_contacts = cursor.fetchall()
        print("\nSample contacts:")
        for contact in sample_contacts:
            print(f"- {dict(zip(['name', 'source', 'identifier', 'type'], contact))}")
    
    assert total_contacts > 0
    assert total_identifiers > 0

if __name__ == "__main__":
    run_tests([
        (test_extract_backup_contacts, "test_extract_backup_contacts"),
        (test_extract_live_contacts, "test_extract_live_contacts"),
        (test_transform_contact, "test_transform_contact"),
        (test_load_contacts, "test_load_contacts"),
        (test_find_contact, "test_find_contact"),
        (test_user_contact, "test_user_contact"),
        (test_etl_contacts, "test_etl_contacts"),
    ])
