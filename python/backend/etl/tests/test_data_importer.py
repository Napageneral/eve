from backend.etl.data_importer import import_backup_data, import_live_data
from backend.etl.iphone_backup import get_first_available_backup
from backend.test.test_utils import setup_test_db, run_tests
from backend.db.session_manager import db
from time import time

def test_import_live_data():
    import_live_data()
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM contacts) as contact_count,
                (SELECT COUNT(*) FROM chats) as chat_count,
                (SELECT COUNT(*) FROM messages) as message_count,
                (SELECT COUNT(*) FROM attachments) as attachment_count
        """)
        counts = cursor.fetchone()
        print("\nLive import results:")
        print(f"Contacts: {counts[0]}")
        print(f"Chats: {counts[1]}")
        print(f"Messages: {counts[2]}")
        print(f"Attachments: {counts[3]}")
        
        assert counts[0] > 0, "No contacts imported"
        assert counts[1] > 0, "No chats imported"
        assert counts[2] > 0, "No messages imported"
        assert counts[3] >= 0, "Attachment count invalid"

def test_import_backup_data():
    backup_path = get_first_available_backup()
    import_backup_data(backup_path)
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM contacts) as contact_count,
                (SELECT COUNT(*) FROM chats) as chat_count,
                (SELECT COUNT(*) FROM messages) as message_count,
                (SELECT COUNT(*) FROM attachments) as attachment_count
        """)
        counts = cursor.fetchone()
        print("\nBackup import results:")
        print(f"Contacts: {counts[0]}")
        print(f"Chats: {counts[1]}")
        print(f"Messages: {counts[2]}")
        print(f"Attachments: {counts[3]}")
        
        assert counts[0] > 0, "No contacts imported"
        assert counts[1] > 0, "No chats imported"
        assert counts[2] > 0, "No messages imported"
        assert counts[3] >= 0, "Attachment count invalid"

def test_import_live_then_backup():
    total_start = time()
    setup_test_db(force_recreate=True)
    
    # First import live data
    t0 = time()
    import_live_data()
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM contacts) as contact_count,
                (SELECT COUNT(*) FROM chats) as chat_count,
                (SELECT COUNT(*) FROM messages) as message_count,
                (SELECT COUNT(*) FROM attachments) as attachment_count
        """)
        live_counts = cursor.fetchone()
        print("\nLIVE IMPORT RESULTS")
        print("=" * 50)
        print(f"Contacts:    {live_counts[0]:>8}")
        print(f"Chats:       {live_counts[1]:>8}")
        print(f"Messages:    {live_counts[2]:>8}")
        print(f"Attachments: {live_counts[3]:>8}")
        print("=" * 50)
    
    # Then import backup data
    t0 = time()
    backup_path = get_first_available_backup()
    import_backup_data(backup_path)
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM contacts) as contact_count,
                (SELECT COUNT(*) FROM chats) as chat_count,
                (SELECT COUNT(*) FROM messages) as message_count,
                (SELECT COUNT(*) FROM attachments) as attachment_count
        """)
        final_counts = cursor.fetchone()
        print("\nFINAL IMPORT RESULTS")
        print("=" * 50)
        print(f"Contacts:    {final_counts[0]:>8}")
        print(f"Chats:       {final_counts[1]:>8}")
        print(f"Messages:    {final_counts[2]:>8}")
        print(f"Attachments: {final_counts[3]:>8}")
        print("=" * 50)
        
        # Verify counts
        assert final_counts[0] >= live_counts[0], "Contact count decreased"
        assert final_counts[1] >= live_counts[1], "Chat count decreased"
        assert final_counts[2] >= live_counts[2], "Message count decreased"
        assert final_counts[3] >= live_counts[3], "Attachment count decreased"
    
    total_time = round(time() - total_start, 2)
    print(f"\nTotal Import Time: {total_time:>6}s")

if __name__ == "__main__":
    run_tests([
        (test_import_live_data, "test_import_live_data"),
        (test_import_backup_data, "test_import_backup_data"),
        (test_import_live_then_backup, "test_import_live_then_backup"),
    ])
