from datetime import datetime, timedelta, timezone
import sqlite3
import logging
from typing import List, Dict, Tuple, Optional
from backend.etl.utils import _safe_timestamp, normalize_phone_number
from backend.db.session_manager import db
from backend.etl.etl_contacts import find_contact_by_identifier  # kept for fallback

logger = logging.getLogger(__name__)

def etl_chats(source_db: str) -> Dict[str, int]:
    """ETL chats from source database."""
    logger.info(f"Starting chat ETL from: {source_db}")
    
    raw_chats = extract_chats(source_db)
    # Build a single contact lookup once to avoid per-participant DB hits
    contact_map = _build_contact_lookup()
    transformed_data = [transform_chat(chat, contact_map=contact_map) for chat in raw_chats]
    stats = load_chats(transformed_data)
    
    logger.info(f"Chat ETL complete. New: {stats['new_chats']}, Updated: {stats['updated_chats']}")
    return stats

def extract_chats(source_db: str) -> List[Dict]:
    """Extract chats and their participants from source database."""
    # Fast path: do NOT join chat_message_join (it blows up runtime).
    # We will fill created/last message dates later from our own DB.
    query = """
    SELECT 
        c.ROWID                      AS rowid,
        c.guid                       AS guid,
        c.chat_identifier            AS chat_identifier,
        c.display_name               AS display_name,
        c.service_name               AS service_name,
        GROUP_CONCAT(DISTINCT h.id)  AS participants,
        CASE WHEN COUNT(DISTINCT chj.handle_id) > 1 THEN 1 ELSE 0 END AS is_group
    FROM chat c
    LEFT JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
    LEFT JOIN handle h            ON chj.handle_id = h.ROWID
    GROUP BY c.ROWID
    """
    try:
        # Read-only + immutable hints speed up reads of Apple's DB
        with sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            chats = [dict(row) for row in cursor.fetchall()]
            logger.info(f"Extracted {len(chats)} chats from {source_db}")
            return chats
    except sqlite3.Error as e:
        logger.error(f"Error querying chat database {source_db}: {e}", exc_info=True)
        return []

def transform_chat(chat: Dict, contact_map: Optional[Dict[str, Dict]] = None) -> Tuple[Dict, List[Dict]]:
    participants = transform_participants(chat.get('participants', ''), contact_map=contact_map)
    display_name = chat['display_name']
    if not display_name:
        participant_names = [p['name'] if p['name'] != 'Unknown' else p['identifier'] for p in participants]
        # rowid alias name changed above; handle both keys defensively
        row_id = chat.get('ROWID', chat.get('rowid', ''))
        display_name = ', '.join(participant_names) if participant_names else f"Chat {row_id}"
    display_name = display_name[:100] + '...' if len(display_name) > 100 else display_name
    
    # Use the same timestamp conversion as etl_messages.py
    # These are now filled later from messages; keep None on initial import
    created_date = _convert_apple_timestamp(chat.get('created_date')) if chat.get('created_date') else None
    last_message_date = _convert_apple_timestamp(chat.get('last_message_date')) if chat.get('last_message_date') else None
    
    sorted_identifiers = []
    for p in participants:
        identifier = p['identifier']
        if '@' in identifier:
            sorted_identifiers.append(identifier.lower())
        else:
            sorted_identifiers.append(normalize_phone_number(identifier))
    sorted_identifiers = sorted(set(sorted_identifiers))
    custom_identifier = ','.join(sorted_identifiers)
    transformed_chat = {
        'chat_identifier': custom_identifier,
        'chat_name': display_name,
        'created_date': created_date,
        'last_message_date': last_message_date,
        'is_group': chat['is_group'],
        'service_name': chat['service_name']
    }
    return transformed_chat, participants

def _convert_apple_timestamp(apple_timestamp):
    """Convert Apple's timestamp format to datetime."""
    try:
        if apple_timestamp > 1e12:  # If in nanoseconds
            apple_timestamp /= 1e9
        return datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=apple_timestamp)
    except Exception as e:
        logger.error(f"Error converting timestamp: {apple_timestamp}")
        return None

def transform_participants(participants_str: str, contact_map: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    if not participants_str:
        return []
    participants = []
    seen_identifiers = set()
    for identifier in participants_str.split(','):
        identifier = identifier.strip()
        if identifier in seen_identifiers:
            continue
        seen_identifiers.add(identifier)
        normalized_identifier = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
        if contact_map is not None:
            contact_info = contact_map.get(normalized_identifier)
        else:
            # Fallback (kept for backwards-compatibility in tests)
            contact_info = find_contact_by_identifier(normalized_identifier)
        if contact_info:
            participants.append({
                'name': contact_info['name'],
                'identifier': identifier,
                'contact_id': contact_info['id']
            })
        else:
            participants.append({
                'name': 'Unknown',
                'identifier': identifier
            })
    return participants

def _build_contact_lookup() -> Dict[str, Dict]:
    """
    Build a normalized identifier -> {id, name} map once so we don't query per participant.
    """
    from backend.etl.utils import normalize_phone_number
    lookup: Dict[str, Dict] = {}
    with db.session_scope() as session:
        cur = session.connection().connection.cursor()
        cur.execute("""
            SELECT c.id, c.name, ci.identifier
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
        """)
        for cid, cname, ident in cur.fetchall():
            key = ident.lower() if '@' in ident else normalize_phone_number(ident)
            lookup[key] = {'id': cid, 'name': cname or ident}
    return lookup

def load_chats(chats_and_participants: List[Tuple[Dict, List[Dict]]]) -> Dict[str, int]:
    """Bulk load chats and participants into database."""
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        stats = {"new_chats": 0, "updated_chats": 0}
        
        # Build both display name and identifier maps
        cursor.execute("SELECT chat_identifier, id, chat_name, is_group FROM chats")
        existing_chat_map = {}
        display_name_map = {}
        for row in cursor.fetchall():
            existing_chat_map[row[0]] = row[1]
            if row[3] and row[2]:  # is_group and has name
                display_name_map[row[2]] = row[1]
        
        for chat, participants in chats_and_participants:
            try:
                chat_id = None
                if chat['is_group'] and chat['chat_name']:
                    # Try matching by display name first for groups
                    chat_id = display_name_map.get(chat['chat_name'])
                
                # Fallback to identifier matching
                if not chat_id:
                    chat_id = existing_chat_map.get(chat['chat_identifier'])
                
                if chat_id:
                    cursor.execute("""
                        UPDATE chats SET
                        chat_name = ?,
                        created_date = MIN(created_date, ?),
                        last_message_date = MAX(last_message_date, ?),
                        service_name = ?
                        WHERE id = ?
                    """, (
                        chat['chat_name'],
                        chat['created_date'],
                        chat['last_message_date'],
                        chat['service_name'],
                        chat_id
                    ))
                    stats["updated_chats"] += 1
                else:
                    cursor.execute("""
                        INSERT INTO chats
                        (chat_identifier, chat_name, created_date, last_message_date, is_group, service_name, total_messages)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                    """, (
                        chat['chat_identifier'],
                        chat['chat_name'],
                        chat['created_date'],
                        chat['last_message_date'],
                        chat['is_group'],
                        chat['service_name']
                    ))
                    chat_id = cursor.lastrowid
                    existing_chat_map[chat['chat_identifier']] = chat_id
                    if chat['is_group'] and chat['chat_name']:
                        display_name_map[chat['chat_name']] = chat_id
                    stats["new_chats"] += 1
                
                for participant in participants:
                    if 'contact_id' not in participant:
                        continue
                    cursor.execute("""
                        INSERT OR IGNORE INTO chat_participants (chat_id, contact_id)
                        VALUES (?, ?)
                    """, (chat_id, participant['contact_id']))
            except Exception as e:
                print(f"Error processing chat {chat['chat_identifier']}: {e}")
                continue
        return stats