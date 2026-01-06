from backend.etl.etl_attachments import extract_attachments, load_attachments, etl_attachments
from backend.etl.iphone_backup import get_first_available_backup, get_sms_db_path
from backend.test.test_utils import run_tests
from backend.db.session_manager import db
import os

def test_extract_attachments():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    attachments = extract_attachments(db_path)
    print(f"Extracted attachments count: {len(attachments)}")
    print(f"Sample attachment: {attachments[0] if attachments else None}")
    assert len(attachments) > 0

def test_load_attachments():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    attachments = extract_attachments(db_path)
    imported, skipped = load_attachments(attachments)
    print(f"Imported: {imported}, Skipped: {skipped}")
    assert imported >= 0
    assert skipped >= 0

def test_etl_attachments():
    # First do live attachments ETL
    chat_db = os.path.expanduser('~/Library/Messages/chat.db')
    if os.path.exists(chat_db):
        print("\nRunning live attachments ETL...")
        imported, skipped = etl_attachments(chat_db)
        print(f"Live attachments ETL results - Imported: {imported}, Skipped: {skipped}")
    
    # Then do backup attachments ETL
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    print("\nRunning backup attachments ETL...")
    imported, skipped = etl_attachments(db_path)
    print(f"Backup attachments ETL results - Imported: {imported}, Skipped: {skipped}")
    
    # Verify results
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM attachments")
        total_attachments = cursor.fetchone()[0]
        print(f"\nFinal database state:")
        print(f"Total attachments: {total_attachments}")
        
        # Sample some attachments - updated column names to match our model
        cursor.execute("""
            SELECT a.file_name, a.mime_type, a.size, a.is_sticker,
                   m.content as message_content, c.chat_name
            FROM attachments a
            JOIN messages m ON a.message_id = m.id
            JOIN chats c ON m.chat_id = c.id
            ORDER BY a.created_date DESC
            LIMIT 5
        """)
        sample_attachments = cursor.fetchall()
        print("\nSample attachments:")
        for att in sample_attachments:
            print(f"- {dict(zip(['file_name', 'mime_type', 'size', 'is_sticker', 'message', 'chat'], att))}")
    
    assert total_attachments > 0

if __name__ == "__main__":
    run_tests([
        (test_extract_attachments, "test_extract_attachments"),
        (test_load_attachments, "test_load_attachments"),
        (test_etl_attachments, "test_etl_attachments"),
    ])
