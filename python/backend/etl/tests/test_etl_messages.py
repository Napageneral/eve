from backend.etl.etl_messages import extract_messages, transform_message, load_messages, etl_messages
from backend.etl.iphone_backup import get_first_available_backup, get_sms_db_path
from backend.test.test_utils import run_tests, TEST_OUTPUT_DIR
from backend.db.session_manager import db
from datetime import datetime, timezone
import os
import json
from time import time

def test_extract_messages():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    messages = extract_messages(db_path)
    print(f"Extracted messages count: {len(messages)}")
    print(f"Sample message: {messages[0] if messages else None}")
    assert len(messages) > 0

def test_transform_message():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    messages = extract_messages(db_path)
    print(f"\nExtracted {len(messages)} messages")
    if not messages:
        print("No messages found to transform!")
        return
    
    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
    
    transformed_messages = []
    error_messages = []
    for msg in messages:
        try:
            transformed = transform_message(msg)
            if transformed is not None:
                transformed_messages.append(transformed)
                assert transformed['chat_identifier']
                assert transformed['timestamp']
                assert isinstance(transformed['is_from_me'], bool)
        except Exception as e:
            error_messages.append({
                'error': str(e),
                'message': msg
            })
    
    print(f"Successfully transformed: {len(transformed_messages)}, Errors: {len(error_messages)}")
    if transformed_messages:
        print(f"Sample transformed message: {transformed_messages[0]}")
    
    if error_messages:
        with open(os.path.join(TEST_OUTPUT_DIR, 'failed_message_transforms.txt'), 'w', encoding='utf-8') as f:
            f.write(f"Generated at: {datetime.now().isoformat()}\n")
            f.write(f"Total Errors: {len(error_messages)}\n")
            f.write("-" * 80 + "\n\n")
            for error in error_messages:
                f.write(f"Error: {error['error']}\n")
                f.write(f"Message: {json.dumps(error['message'], indent=2, default=str)}\n")
                f.write("-" * 80 + "\n\n")

def test_load_messages():
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    messages = extract_messages(db_path)[:5]
    transformed = [transform_message(m) for m in messages]
    imported, skipped = load_messages(transformed)
    print(f"Imported: {imported}, Skipped: {skipped}")
    assert imported >= 0
    assert skipped >= 0

def test_etl_messages():
    def log_time(start_time, step):
        elapsed = round(time() - start_time, 2)
        print(f"{step}: {elapsed}s")
        return time()
    
    total_start = time()
    
    # Live ETL
    chat_db = os.path.expanduser('~/Library/Messages/chat.db')
    if os.path.exists(chat_db):
        print("\nRunning live messages ETL...")
        t = time()
        imported, skipped = etl_messages(chat_db)
        t = log_time(t, "Live ETL completed")
        print(f"Live messages ETL results - Imported: {imported}, Skipped: {skipped}")
    
    # Backup ETL
    backup_path = get_first_available_backup()
    db_path = get_sms_db_path(backup_path)
    print("\nRunning backup messages ETL...")
    t = time()
    imported, skipped = etl_messages(db_path)
    t = log_time(t, "Backup ETL completed")
    print(f"Backup messages ETL results - Imported: {imported}, Skipped: {skipped}")
    
    # Verify results
    print("\nVerifying results...")
    t = time()
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        total_messages = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM reactions")
        total_reactions = cursor.fetchone()[0]
        t = log_time(t, "Counts query completed")
        
        print(f"\nFinal database state:")
        print(f"Total messages: {total_messages}")
        print(f"Total reactions: {total_reactions}")
        
        # Sample some messages
        cursor.execute("""
            SELECT m.content, m.is_from_me, m.service_name, m.message_type,
                   c.chat_name, co.name as sender_name
            FROM messages m
            JOIN chats c ON m.chat_id = c.id
            LEFT JOIN contacts co ON m.sender_id = co.id
            ORDER BY m.timestamp DESC
            LIMIT 5
        """)
        sample_messages = cursor.fetchall()
        t = log_time(t, "Sample query completed")
        
        print("\nSample messages:")
        for msg in sample_messages:
            print(f"- {dict(zip(['content', 'is_from_me', 'service', 'type', 'chat', 'sender'], msg))}")
    
    log_time(total_start, "Total test time")
    assert total_messages > 0

if __name__ == "__main__":
    run_tests([
        (test_extract_messages, "test_extract_messages"),
        (test_transform_message, "test_transform_message"),
        (test_load_messages, "test_load_messages"),
        (test_etl_messages, "test_etl_messages"),
    ])