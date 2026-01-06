from datetime import datetime
import os
from time import time
from sqlalchemy.sql import text
from backend.db.session_manager import db
from backend.etl.etl_conversations import etl_conversations
from backend.test.test_utils import TEST_OUTPUT_DIR

def log_time(start_time: float, step: str) -> float:
    elapsed = round(time() - start_time, 2)
    print(f"  {step}: {elapsed}s")
    return time()

def write_etl_conversations(conversations, filename="test_etl_conversations.txt"):
    t = time()
    filepath = os.path.join(TEST_OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"Generated at: {datetime.now().isoformat()}\n")
        f.write(f"Total Conversations: {len(conversations)}\n\n")
        
        for conv in conversations:
            f.write("="*50 + "\n")
            f.write(f"Chat ID: {conv['chat_id']}\n")
            f.write(f"Messages: {conv['message_count']}\n")
            f.write(f"Time Range: {conv['start_time']} to {conv['end_time']}\n")
            f.write(f"Participants: {len(conv['participant_ids'])}\n\n")
            f.write("Messages:\n")
            
            for msg in conv['messages']:
                f.write(f"[{msg['timestamp']}] {msg['sender_name']}: {msg['content']}\n")
            
            f.write("\n" + "="*50 + "\n\n")
    print(f"  Write output file: {round(time() - t, 2)}s")

def test_etl_conversations():
    total_start = time()
    t = time()
    
    print("\nStarting ETL conversation test...")
    
    # Clear existing conversations
    with db.session_scope() as session:
        session.execute(text("DELETE FROM conversations"))
    t = log_time(t, "Clear conversations")
    
    # Run ETL
    imported, updated = etl_conversations()
    t = log_time(t, "ETL process")
    
    # Fetch results with raw SQL
    with db.session_scope() as session:
        print("\nGathering test results...")
        # Split stats query into separate counts for better performance
        stats = {}
        
        conv_count_query = text("""
            SELECT COUNT(*) FROM conversations
        """)
        stats['conv_count'] = session.execute(conv_count_query).scalar()
        t = log_time(t, "Get conversation count")
        
        msg_count_query = text("""
            SELECT COUNT(*) FROM messages 
            WHERE conversation_id IS NOT NULL
        """)
        stats['msg_count'] = session.execute(msg_count_query).scalar()
        t = log_time(t, "Get message count")
        
        unassigned_query = text("""
            SELECT COUNT(*) FROM messages 
            WHERE conversation_id IS NULL
        """)
        stats['unassigned_count'] = session.execute(unassigned_query).scalar()
        t = log_time(t, "Get unassigned count")
        
        # Optimize conversation details query
        conv_query = text("""
            WITH RECURSIVE 
            conversation_messages AS (
                SELECT 
                    c.id,
                    c.chat_id,
                    c.start_time,
                    c.end_time,
                    c.message_count,
                    json_group_array(
                        json_object(
                            'id', m.id,
                            'sender_name', COALESCE(co.name, 'Unknown'),
                            'content', m.content,
                            'timestamp', m.timestamp
                        )
                    ) as messages,
                    GROUP_CONCAT(DISTINCT co.id) as participant_ids,
                    GROUP_CONCAT(DISTINCT co.name) as participant_names
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                LEFT JOIN contacts co ON m.sender_id = co.id
                GROUP BY c.id
                ORDER BY c.start_time
                LIMIT 1000  -- Add reasonable limit
            )
            SELECT * FROM conversation_messages
        """)
        
        print("\nFetching conversation details...")
        conversations = []
        for row in session.execute(conv_query):
            conversations.append({
                'id': row[0],
                'chat_id': row[1],
                'start_time': row[2],
                'end_time': row[3],
                'message_count': row[4],
                'messages': eval(row[5]) if row[5] else [],
                'participant_ids': row[6].split(',') if row[6] else [],
                'participant_names': row[7].split(',') if row[7] else []
            })
        t = log_time(t, "Get conversation details")
        
        # Write results before assertions
        write_etl_conversations(conversations)
        
        print(f"\nETL Results:")
        print(f"Conversations: {stats['conv_count']}")
        print(f"Messages with conversations: {stats['msg_count']}")
        print(f"Unassigned messages: {stats['unassigned_count']}")
        
        total_time = round(time() - total_start, 2)
        print(f"\nTotal test time: {total_time}s")
        
        assert stats['conv_count'] > 0, "No conversations created"
        assert stats['unassigned_count'] == 0, f"Found {stats['unassigned_count']} unassigned messages"

if __name__ == "__main__":
    test_etl_conversations()
