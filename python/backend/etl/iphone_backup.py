import os
import sys
import plistlib
import logging
import sqlite3
from typing import Dict, List, Optional

def get_backup_root_path():
    if sys.platform != "darwin":
        logging.debug("Unsupported platform for automatic backup detection")
        return None

    backup_root = os.path.expanduser("~/Library/Application Support/MobileSync/Backup")
    
    if not os.path.exists(backup_root):
        logging.debug(f"Backup root does not exist: {backup_root}")
        return None
    
    if not os.access(backup_root, os.R_OK):
        logging.debug(f"No read access to backup root: {backup_root}")
        return None
    
    return backup_root

def get_backup_info(backup_path: str) -> Dict[str, str]:
    info_plist_path = os.path.join(backup_path, 'Info.plist')
    if not os.path.exists(info_plist_path):
        logging.error(f"Info.plist not found at {info_plist_path}")
        return {}
    
    try:
        with open(info_plist_path, 'rb') as f:
            return plistlib.load(f)
    except Exception as e:
        logging.error(f"Error reading Info.plist: {str(e)}")
        return {}

def list_available_backups() -> List[Dict[str, str]]:
    backup_root = get_backup_root_path()
    if not backup_root:
        logging.error("Backup root path not found")
        return []
    
    backups = []
    for item in os.listdir(backup_root):
        backup_path = os.path.join(backup_root, item)
        if os.path.isdir(backup_path):
            info = get_backup_info(backup_path)
            if info:
                backups.append({
                    'path': backup_path,
                    'name': info.get('Device Name', 'Unknown Device'),
                    'date': info.get('Last Backup Date', 'Unknown Date')
                })
    
    return backups

def get_first_available_backup() -> Optional[str]:
    backup_root = get_backup_root_path()
    if not backup_root:
        logging.error("Backup root path not found")
        return None
    
    for item in os.listdir(backup_root):
        backup_path = os.path.join(backup_root, item)
        if os.path.isdir(backup_path):
            info = get_backup_info(backup_path)
            if info:
                logging.info(f"Selected backup: {info.get('Device Name', 'Unknown Device')} - {info.get('Last Backup Date', 'Unknown Date')}")
                return backup_path
    
    logging.error("No available backups found")
    return None

def get_file_id_from_manifest(backup_path: str, relative_path: str) -> Optional[str]:
    manifest_path = os.path.join(backup_path, 'Manifest.db')
    if not os.path.exists(manifest_path):
        logging.error("Manifest.db not found")
        return None
    
    try:
        with sqlite3.connect(manifest_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            table_name = next((t for t in ['Files', 'files', 'File', 'file'] if t in tables), None)
            
            if not table_name:
                logging.error(f"No suitable table found. Available tables: {', '.join(tables)}")
                return None
            
            cursor.execute(f"SELECT fileID FROM {table_name} WHERE relativePath = ? AND domain = 'HomeDomain'", (relative_path,))
            result = cursor.fetchone()
            
            return result[0] if result else None
            
    except sqlite3.Error as e:
        logging.error(f"SQLite error: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return None

def get_db_path(backup_path: str, relative_path: str) -> Optional[str]:
    file_id = get_file_id_from_manifest(backup_path, relative_path)
    if not file_id:
        return None
    
    subdirectory = file_id[:2]
    db_path = os.path.join(backup_path, subdirectory, file_id)
    
    if not os.path.exists(db_path):
        logging.error(f"{relative_path} not found in backup at {db_path}")
        return None
    
    return db_path

def get_sms_db_path(backup_path: str) -> Optional[str]:
    return get_db_path(backup_path, 'Library/SMS/sms.db')

def get_address_book_db_path(backup_path: str) -> Optional[str]:
    return get_db_path(backup_path, 'Library/AddressBook/AddressBook.sqlitedb')