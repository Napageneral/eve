from backend.etl.etl_chats import etl_chats, extract_chats, load_chats, transform_chat
from backend.etl.iphone_backup import get_first_available_backup, get_sms_db_path
from backend.test.test_utils import run_tests
from backend.db.session_manager import db
import os

def test_extract_backup_chats():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    chats = extract_chats(db_path)
    print(f"Extracted backup chats: {len(chats)}")
    assert len(chats) > 0

def test_extract_live_chats():
    chat_db = os.path.expanduser('~/Library/Messages/chat.db')
    if not os.path.exists(chat_db):
        print("No live chat database found")
        return
    chats = extract_chats(chat_db)
    print(f"Extracted live chats: {len(chats)}")
    assert len(chats) > 0

def test_transform_chat():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    chats = extract_chats(db_path)
    if len(chats) > 0:
        transformed_chat, participants = transform_chat(chats[0])
        assert transformed_chat['chat_identifier'] is not None
        assert transformed_chat['chat_name'] is not None
        assert transformed_chat['created_date'] is not None
        assert transformed_chat['last_message_date'] is not None
        assert isinstance(transformed_chat['is_group'], int)
        assert transformed_chat['service_name'] is not None

def test_load_chats():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    chats = extract_chats(db_path)
    transformed_data = [transform_chat(c) for c in chats[:5]]
    stats = load_chats(transformed_data)
    print(f"Load stats: {stats}")
    assert stats['new_chats'] >= 0
    assert stats['updated_chats'] >= 0

def test_etl_chats():
    chat_db = os.path.expanduser('~/Library/Messages/chat.db')
    if os.path.exists(chat_db):
        print("\nRunning live chat ETL...")
        stats = etl_chats(chat_db)
        print(f"Live stats: {stats}")
    
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    print("\nRunning backup chat ETL...")
    stats = etl_chats(db_path)
    print(f"Backup stats: {stats}")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats")
        total_chats = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM chat_participants")
        total_participants = cursor.fetchone()[0]
        print(f"\nDatabase state:")
        print(f"Chats: {total_chats}")
        print(f"Participants: {total_participants}")
    
    assert total_chats > 0

if __name__ == "__main__":
    run_tests([
        (test_extract_backup_chats, "test_extract_backup_chats"),
        (test_extract_live_chats, "test_extract_live_chats"),
        (test_transform_chat, "test_transform_chat"),
        (test_load_chats, "test_load_chats"),
        (test_etl_chats, "test_etl_chats"),
    ])
