import sqlite3
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from backend.etl.utils import normalize_phone_number
from backend.db.session_manager import db
from time import time

logger = logging.getLogger(__name__)

def get_live_chat_db_path() -> str:
    home = os.path.expanduser('~')
    return os.path.join(home, 'Library', 'Messages', 'chat.db')

def etl_messages(source_db: str, since_date: Optional[datetime] = None, race_mode: bool = False) -> Tuple[int, int]:
    def log_time(start_time, step):
        elapsed = round(time() - start_time, 2)
        print(f"  {step}: {elapsed}s")
        return time()
    
    t = time()
    logger.info(f"Starting message ETL from: {source_db}")
    
    t = time()
    # Precompute chat identifier map to avoid per-message aggregation work
    chat_ident_map = _build_chat_identifier_map(source_db)
    raw_messages = extract_messages(source_db, since_date)
    t = log_time(t, "Extract completed")
    
    transformed_messages = [transform_message(msg, chat_ident_map) for msg in raw_messages]
    t = log_time(t, "Transform completed")
    
    imported_count, skipped_count = load_messages(transformed_messages, race_mode=race_mode)
    t = log_time(t, "Load completed")
    
    if source_db == get_live_chat_db_path():
        update_user_contact_from_account_login(source_db)
    
    logger.info(f"Message ETL complete. Imported: {imported_count}, Skipped: {skipped_count}")
    return imported_count, skipped_count

def extract_messages(source_db: str, since_date: Optional[datetime] = None) -> List[Dict]:
    # Build handle map once to avoid joining handle for every row
    handle_map = _build_handle_map(source_db)
    query = """
    SELECT
        m.ROWID AS message_id,
        m.guid,
        m.text,
        m.attributedBody,
        m.handle_id,
        m.service,
        m.date,
        m.is_from_me,
        m.associated_message_guid,
        m.associated_message_type,
        m.reply_to_guid,
        MIN(cmj.chat_id) AS chat_rowid
    FROM message m
    JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    """
    params = []
    if since_date:
        apple_timestamp = int((since_date - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1e9)
        query += " WHERE m.date > ?"
        params.append(apple_timestamp)
    # No ORDER BY here – we don't need sort at extract; we'll sort inside our DB for conversations.
    query += " GROUP BY m.ROWID"
    try:
        with sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            # Fast read-only pragmas
            try:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA synchronous=OFF")
                conn.execute("PRAGMA journal_mode=OFF")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA cache_size=-262144")
                conn.execute("PRAGMA mmap_size=268435456")
            except Exception:
                pass
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            messages = []
            for row in rows:
                d = dict(row)
                # Resolve sender_identifier from handle_id without a join cost
                hid = d.get('handle_id')
                d['sender_identifier'] = handle_map.get(hid, '') if hid is not None else ''
                messages.append(d)
            logger.info(f"Extracted {len(messages)} messages from {source_db}")
            return messages
    except sqlite3.Error as e:
        logger.error(f"Error querying message database {source_db}: {e}", exc_info=True)
        return []

def transform_message(message: Dict, chat_ident_map: Optional[Dict[int, str]] = None) -> Dict:
    # Prefer precomputed chat identifier from chat_rowid → identifier map
    chat_identifier = ""
    if chat_ident_map and ("chat_rowid" in message):
        chat_identifier = chat_ident_map.get(message["chat_rowid"], "")
    else:
        participants = message.get('chat_participants', '')
        parts = participants.split(',') if participants else []
        chat_identifier = generate_chat_identifier(parts)
    sender_identifier = message.get('sender_identifier') or ''
    sender_identifier = normalize_phone_number(sender_identifier) if '@' not in sender_identifier else sender_identifier.lower()
    content = message.get('text')
    if not content and message.get('attributedBody'):
        content = decode_attributed_body(message.get('attributedBody'))
    content = _clean_message_content(content)
    timestamp = _convert_apple_timestamp(message['date'])
    transformed = {
        'chat_identifier': chat_identifier,
        'sender_identifier': sender_identifier,
        'content': content,
        'timestamp': timestamp,
        'is_from_me': bool(message['is_from_me']),
        'message_type': message.get('associated_message_type'),
        'service_name': message['service'],
        'guid': message['guid'],
        # keep raw associated_message_guid to match messages.guid foreign key
        'associated_message_guid': message.get('associated_message_guid'),
        'reply_to_guid': message.get('reply_to_guid')
    }
    return transformed

def _build_chat_identifier_map(source_db: str) -> Dict[int, str]:
    """Build a map of chat ROWID to normalized chat identifier string.
    This avoids per-message GROUP_CONCAT over chat participants.
    """
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.ROWID AS chat_rowid,
                   GROUP_CONCAT(DISTINCT h.id) AS participants
            FROM chat c
            LEFT JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
            LEFT JOIN handle h            ON chj.handle_id = h.ROWID
            GROUP BY c.ROWID
            """
        )
        rows = cur.fetchall()

    mapping: Dict[int, str] = {}
    for row in rows:
        participants = (row["participants"] or "")
        parts = participants.split(",") if participants else []
        mapping[row["chat_rowid"]] = generate_chat_identifier(parts)
    return mapping

def _build_handle_map(source_db: str) -> Dict[int, str]:
    """ROWID -> handle.id (phone/email)"""
    mapping: Dict[int, str] = {}
    with sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
        except Exception:
            pass
        cur = conn.cursor()
        cur.execute("SELECT ROWID, id FROM handle")
        for row in cur.fetchall():
            mapping[row["ROWID"]] = row["id"]
    return mapping

def load_messages(messages: List[Dict], race_mode: bool = False) -> Tuple[int, int]:
    with db.session_scope() as session:
        conn = session.connection().connection
        cursor = conn.cursor()
        # PRAGMAs tuned based on race_mode
        if race_mode:
            try:
                cursor.execute("PRAGMA journal_mode=OFF")
            except Exception:
                pass
            cursor.execute("PRAGMA synchronous=OFF")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
            cursor.execute("PRAGMA foreign_keys=OFF")
            # Make the write transaction deterministic & reduce lock thrash
            cursor.execute("BEGIN IMMEDIATE")
            # Bump cache for this connection only (negative value is KB)
            cursor.execute("PRAGMA cache_size=-262144")  # ~256MB
            cursor.execute("PRAGMA cache_spill=OFF")
        else:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA locking_mode=EXCLUSIVE")
            cursor.execute("PRAGMA foreign_keys=ON")
        print("Building lookup maps...")
        cursor.execute("SELECT COUNT(*), (SELECT COUNT(*) FROM reactions) FROM messages")
        msg_count, rxn_count = cursor.fetchone()
        print(f"Messages in DB before import: {msg_count}")
        if msg_count == 0 and rxn_count == 0:
            # Fresh DB: skip scanning existing GUIDs for speed
            existing_message_guids = set()
            existing_reaction_guids = set()
            # Build chat and contact maps only
            chat_map = {}
            display_name_map = {}
            contact_map = {}
            user_id = None
            # Chats
            cursor.execute("SELECT chat_identifier, id, chat_name, is_group FROM chats")
            for identifier, id_, name, is_group in cursor.fetchall():
                chat_map[identifier] = int(id_)
                if int(is_group) and name:
                    display_name_map[name] = int(id_)
            # Contacts
            cursor.execute("""
                SELECT ci.identifier, c.id 
                FROM contacts c 
                JOIN contact_identifiers ci ON c.id = ci.contact_id
            """)
            for identifier, id_ in cursor.fetchall():
                contact_map[normalize_phone_number(identifier)] = int(id_)
            # User contact id
            cursor.execute("SELECT id FROM contacts WHERE is_me = 1")
            row = cursor.fetchone()
            user_id = int(row[0]) if row else None
            if user_id is None:
                user_id = get_user_contact_id(session)
        else:
            cursor.execute("""
                SELECT 'guid', guid FROM messages 
                UNION SELECT 'reaction', guid FROM reactions
                UNION SELECT 'chat', chat_identifier || '|' || id || '|' || COALESCE(chat_name,'') || '|' || is_group FROM chats
                UNION SELECT 'contact', ci.identifier || '|' || c.id 
                FROM contacts c JOIN contact_identifiers ci ON c.id = ci.contact_id
                UNION SELECT 'user', id || '' FROM contacts WHERE is_me = 1
            """)
            existing_message_guids = set()
            existing_reaction_guids = set()
            chat_map = {}
            display_name_map = {}
            contact_map = {}
            user_id = None
            for type_, value in cursor.fetchall():
                if type_ == 'guid':
                    existing_message_guids.add(value)
                elif type_ == 'reaction':
                    existing_reaction_guids.add(value)
                elif type_ == 'chat':
                    identifier, id_, name, is_group = value.rsplit('|', 3)
                    chat_map[identifier] = int(id_)
                    if int(is_group) and name:
                        display_name_map[name] = int(id_)
                elif type_ == 'contact':
                    identifier, id_ = value.rsplit('|', 1)
                    contact_map[normalize_phone_number(identifier)] = int(id_)
                elif type_ == 'user':
                    user_id = int(value)
            if user_id is None:
                user_id = get_user_contact_id(session)
        print(f"Found {len(existing_message_guids)} existing messages")
        print(f"Found {len(chat_map)} chats")
        print(f"Found {len(contact_map)} contacts")
        messages_to_insert = []
        messages_to_update = []
        reactions_to_insert = []  # defer execution until after all messages are written
        reactions_to_update = []  # defer execution until after all messages are written
        chat_counts = defaultdict(int)
        chat_min_ts = {}
        chat_max_ts = {}
        imported_count = skipped_count = 0
        missing_chat_identifiers = set()
        CHUNK_SIZE = 1000000
        inserted_message_guids = set()
        for i in range(0, len(messages), CHUNK_SIZE):
            chunk = messages[i:i + CHUNK_SIZE]
            for msg in chunk:
                chat_id = None
                if msg.get('is_group') and msg.get('display_name'):
                    chat_id = display_name_map.get(msg['display_name'])
                if not chat_id:
                    chat_id = chat_map.get(msg['chat_identifier'])
                if not chat_id:
                    missing_chat_identifiers.add(msg['chat_identifier'])
                    skipped_count += 1
                    continue
                if msg['is_from_me']:
                    sender_id = user_id
                else:
                    sender_id = contact_map.get(msg['sender_identifier'])
                    if not sender_id:
                        sender_id = create_contact_for_unknown_sender(session, msg['sender_identifier'])
                        contact_map[msg['sender_identifier']] = sender_id
                assoc_type = msg.get('message_type')
                if msg.get('associated_message_guid') and assoc_type not in (None, 0):
                    reaction_data = (
                        msg['associated_message_guid'],
                        msg.get('message_type'),
                        sender_id,
                        msg['timestamp'],
                        chat_id,
                        msg['guid']
                    )
                    if msg['guid'] in existing_reaction_guids:
                        reactions_to_update.append(reaction_data)
                    else:
                        reactions_to_insert.append(reaction_data)
                else:
                    message_data = (
                        chat_id,
                        sender_id,
                        msg['content'],
                        msg['timestamp'],
                        msg['is_from_me'],
                        msg['message_type'],
                        msg['service_name'],
                        msg['guid']
                    )
                    
                    if msg['guid'] in existing_message_guids:
                        messages_to_update.append(message_data)
                    else:
                        messages_to_insert.append(message_data)
                        chat_counts[chat_id] += 1
                        # Track min/max per chat for created/last updates
                        ts = msg['timestamp']
                        if chat_id not in chat_min_ts or ts < chat_min_ts[chat_id]:
                            chat_min_ts[chat_id] = ts
                        if chat_id not in chat_max_ts or ts > chat_max_ts[chat_id]:
                            chat_max_ts[chat_id] = ts
                        imported_count += 1
                        inserted_message_guids.add(msg['guid'])
            if messages_to_insert:
                cursor.executemany("""
                    INSERT INTO messages (chat_id, sender_id, content, timestamp, 
                                       is_from_me, message_type, service_name, guid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, messages_to_insert)
                messages_to_insert = []
            if messages_to_update:
                cursor.executemany("""
                    UPDATE messages 
                    SET chat_id = ?, sender_id = ?, content = ?, timestamp = ?,
                        is_from_me = ?, message_type = ?, service_name = ?
                    WHERE guid = ?
                """, messages_to_update)
                messages_to_update = []
        # After all messages are written, insert/update reactions to satisfy FK(original_message_guid -> messages.guid)
        if reactions_to_insert:
            existing_guid_rows = cursor.execute("SELECT guid FROM messages").fetchall()
            existing_guids = {row[0] for row in existing_guid_rows}
            all_known_message_guids = existing_guids.union(inserted_message_guids)
            norm_to_raw = {}
            for g in all_known_message_guids:
                ng = _clean_guid(g)
                if ng and ng not in norm_to_raw:
                    norm_to_raw[ng] = g

            resolved_inserts = []
            for (orig_guid, rtype, sender_id, ts, cid, rxn_guid) in reactions_to_insert:
                if not orig_guid:
                    continue
                if orig_guid in all_known_message_guids:
                    resolved_inserts.append((orig_guid, rtype, sender_id, ts, cid, rxn_guid))
                    continue
                ng = _clean_guid(orig_guid)
                mapped = norm_to_raw.get(ng)
                if mapped:
                    resolved_inserts.append((mapped, rtype, sender_id, ts, cid, rxn_guid))

            if resolved_inserts:
                cursor.executemany("""
                    INSERT INTO reactions (original_message_guid, reaction_type, 
                                        sender_id, timestamp, chat_id, guid)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, resolved_inserts)
        if reactions_to_update:
            existing_guid_rows = cursor.execute("SELECT guid FROM messages").fetchall()
            existing_guids = {row[0] for row in existing_guid_rows}
            norm_to_raw = {}
            for g in existing_guids.union(inserted_message_guids):
                ng = _clean_guid(g)
                if ng and ng not in norm_to_raw:
                    norm_to_raw[ng] = g

            resolved_updates = []
            for (orig_guid, rtype, sender_id, ts, cid, rxn_guid) in reactions_to_update:
                if orig_guid in existing_guids or orig_guid in inserted_message_guids:
                    resolved_updates.append((orig_guid, rtype, sender_id, ts, cid, rxn_guid))
                    continue
                ng = _clean_guid(orig_guid)
                mapped = norm_to_raw.get(ng)
                if mapped:
                    resolved_updates.append((mapped, rtype, sender_id, ts, cid, rxn_guid))

            if resolved_updates:
                cursor.executemany("""
                    UPDATE reactions 
                    SET original_message_guid = ?, reaction_type = ?, 
                        sender_id = ?, timestamp = ?, chat_id = ?
                    WHERE guid = ?
                """, resolved_updates)
        if chat_counts:
            cursor.executemany(
                "UPDATE chats SET total_messages = total_messages + ? WHERE id = ?",
                [(count, chat_id) for chat_id, count in chat_counts.items()]
            )
        # Update created_date / last_message_date in one pass
        if chat_min_ts or chat_max_ts:
            updates = []
            for cid in set(list(chat_min_ts.keys()) + list(chat_max_ts.keys())):
                min_ts = chat_min_ts.get(cid)
                max_ts = chat_max_ts.get(cid)
                updates.append((min_ts, min_ts, min_ts, max_ts, max_ts, max_ts, cid))
            cursor.executemany("""
                UPDATE chats
                SET
                  created_date = CASE
                                    WHEN created_date IS NULL THEN ?
                                    WHEN ? < created_date THEN ?
                                    ELSE created_date
                                 END,
                  last_message_date = CASE
                                    WHEN last_message_date IS NULL THEN ?
                                    WHEN ? > last_message_date THEN ?
                                    ELSE last_message_date
                                  END
                WHERE id = ?
            """, updates)
        
        return imported_count, skipped_count

def create_contact_for_unknown_sender(session, identifier: str) -> int:
    cursor = session.connection().connection.cursor()
    identifier_type = 'Email' if '@' in identifier else 'Phone'
    # Reuse existing contact if identifier already present
    row = cursor.execute(
        """
        SELECT c.id
        FROM contact_identifiers ci
        JOIN contacts c ON c.id = ci.contact_id
        WHERE ci.identifier = ?
        """,
        (identifier,),
    ).fetchone()
    if row:
        return int(row[0])
    cursor.execute("INSERT INTO contacts (name) VALUES (?)", (identifier,))
    contact_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
    cursor.execute("""
        INSERT INTO contact_identifiers (contact_id, identifier, type, is_primary)
        VALUES (?, ?, ?, ?)
    """, (contact_id, identifier, identifier_type, True))
    return contact_id

def get_user_contact_id(session) -> int:
    cursor = session.connection().connection.cursor()
    result = cursor.execute("SELECT id FROM contacts WHERE is_me = 1").fetchone()
    if result:
        return result[0]
    cursor.execute("INSERT INTO contacts (name, is_me) VALUES (?, ?)", ("Me", True))
    user_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
    return user_id

def generate_chat_identifier(participants: List[str]) -> str:
    normalized = []
    for p in participants:
        p = p.strip()
        if '@' in p:
            normalized.append(p.lower())
        else:
            normalized.append(normalize_phone_number(p))
    normalized = sorted(set(normalized))
    return ','.join(normalized)

def decode_attributed_body(attributed_body) -> str:
    if not attributed_body:
        return ""
    try:
        # First decode with surrogateescape to handle any invalid bytes
        attributed_body = attributed_body.decode('utf-8', errors='surrogateescape')
        
        if "NSNumber" in attributed_body:
            attributed_body = attributed_body.split("NSNumber")[0]
            if "NSString" in attributed_body:
                attributed_body = attributed_body.split("NSString")[1]
                if "NSDictionary" in attributed_body:
                    attributed_body = attributed_body.split("NSDictionary")[0]
                    attributed_body = attributed_body[6:-12]
                    return attributed_body.strip()
        return ""
    except Exception as e:
        print(f"Error decoding attributedBody: {e}")
        return ""

def _convert_apple_timestamp(apple_timestamp):
    try:
        if apple_timestamp > 1e12:
            apple_timestamp /= 1e9
        return datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=apple_timestamp)
    except Exception as e:
        logger.error(f"Error converting timestamp: {apple_timestamp}")
        return None

def _clean_message_content(content: str) -> str:
    if not content:
        return ""
    try:
        # First try direct cleaning
        cleaned = ''.join(char for char in content 
                         if char.isprintable() 
                         or char in [' ', '\n', '\t'])
        
        # Remove problematic characters
        chars_to_remove = ['\uFFFC', '\x01', '\ufffd']
        for char in chars_to_remove:
            cleaned = cleaned.replace(char, '')
            
        return cleaned.strip()
    except Exception as e:
        print(f"Error cleaning message content: {e}")
        # If all else fails, try basic ASCII conversion
        try:
            return content.encode('ascii', 'ignore').decode('ascii').strip()
        except:
            return ""

def _clean_guid(guid):
    if guid:
        if '/' in guid:
            return guid.split('/', 1)[-1]
        if ':' in guid:
            return guid.split(':', 1)[-1]
    return guid

def update_user_contact_from_account_login(source_db: str):
    """
    After we ETL messages, we can run this to update the user's 
    phone/email from the chat.account_login that was used most recently.
    """
    # 1) Query phone and email from source chat.db
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query phone
        cursor.execute("""
            SELECT account_login
            FROM chat
            WHERE account_login LIKE 'P:+%'
              AND account_login != 'P:+'
        """)
        phone_row = cursor.fetchone()
        phone_number = None
        if phone_row and phone_row['account_login']:
            phone_number = phone_row['account_login'].replace("P:+", "").strip()

        # Query email
        cursor.execute("""
            SELECT account_login
            FROM chat
            WHERE account_login LIKE 'E:%'
              AND account_login != 'E:'
        """)
        email_row = cursor.fetchone()
        email_address = None
        if email_row and email_row['account_login']:
            email_address = email_row['account_login'].replace("E:", "").strip().lower()

    # 2) Update our DB with the found identifiers
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        # Check if user contact exists
        cursor.execute("SELECT id FROM contacts WHERE is_me = 1")
        result = cursor.fetchone()
        
        if result:
            user_contact_id = result[0]
        else:
            # Create user contact if it doesn't exist
            cursor.execute(
                "INSERT INTO contacts (name, is_me) VALUES (?, ?)",
                ("Me", True)
            )
            user_contact_id = cursor.lastrowid

        # Update phone if found
        if phone_number:
            cursor.execute(
                "DELETE FROM contact_identifiers WHERE contact_id = ? AND type = 'Phone'",
                (user_contact_id,)
            )
            cursor.execute("""
                INSERT INTO contact_identifiers 
                (contact_id, identifier, type, is_primary, last_used)
                VALUES (?, ?, 'Phone', 1, CURRENT_TIMESTAMP)
            """, (user_contact_id, phone_number))

        # Update email if found
        if email_address:
            cursor.execute(
                "DELETE FROM contact_identifiers WHERE contact_id = ? AND type = 'Email'",
                (user_contact_id,)
            )
            cursor.execute("""
                INSERT INTO contact_identifiers 
                (contact_id, identifier, type, is_primary, last_used)
                VALUES (?, ?, 'Email', 1, CURRENT_TIMESTAMP)
            """, (user_contact_id, email_address))

        logger.info("✅ User contact updated from chat.account_login")
