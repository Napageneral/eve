import logging
from backend.test.test_utils import run_tests
from backend.etl.iphone_backup import (
    get_backup_info,
    list_available_backups,
    get_first_available_backup,
    get_file_id_from_manifest,
    get_sms_db_path,
    get_address_book_db_path
)
import shutil
import os
from datetime import datetime

logger = logging.getLogger(__name__)

def test_list_available_backups():
    backups = list_available_backups()
    print("\nAvailable backups:")
    for backup in backups:
        print(f"Path: {backup['path']}")
        print(f"Name: {backup['name']}")
        print(f"Date: {backup['date']}\n")
    assert len(backups) > 0, "Should find at least one backup"

def test_get_first_backup():
    backup_path = get_first_available_backup()
    print(f"\nFirst available backup path: {backup_path}")
    assert backup_path is not None, "Should find a backup"
    info = get_backup_info(backup_path)
    keys_to_print = ['Build Version', 'Device Name', 'Display Name', 'GUID', 'ICCID', 'IMEI', 'IMEI 2', 'Last Backup Date', 'MEID', 'Phone Number', 'Product Name', 'Product Type', 'Product Version', 'Serial Number', 'Target Identifier', 'Target Type', 'Unique Identifier', 'iTunes Settings', 'macOS Build Version', 'macOS Version']
    for key in keys_to_print:
        if key in info:
            print(f"{key}: {info[key]}")
    assert len(info) > 0, "Should get backup info"

def test_get_sms_database():
    backup_path = get_first_available_backup()
    file_id = get_file_id_from_manifest(backup_path, 'Library/SMS/sms.db')
    print(f"\nSMS DB file ID: {file_id}")
    assert file_id is not None, "Should find SMS database file ID"
    
    db_path = get_sms_db_path(backup_path)
    print(f"SMS DB path: {db_path}")
    assert db_path is not None, "Should find SMS database path"

def test_get_address_book_database():
    backup_path = get_first_available_backup()
    file_id = get_file_id_from_manifest(backup_path, 'Library/AddressBook/AddressBook.sqlitedb')
    print(f"\nAddress Book DB file ID: {file_id}")
    assert file_id is not None, "Should find Address Book database file ID"
    
    db_path = get_address_book_db_path(backup_path)
    print(f"Address Book DB path: {db_path}")
    assert db_path is not None, "Should find Address Book database path"

def test_copy_sms_database():
    backup_path = get_first_available_backup()
    sms_db_path = get_sms_db_path(backup_path)
    downloads_path = os.path.expanduser("~/Downloads")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = os.path.join(downloads_path, f"sms_backup_{timestamp}.db")
    print(f"\nCopying SMS database from: {sms_db_path}")
    print(f"To: {dest_path}")
    shutil.copy2(sms_db_path, dest_path)
    assert os.path.exists(dest_path), "Database should be copied successfully"
    print(f"SMS database copied successfully")

if __name__ == "__main__":
    run_tests([
        (test_list_available_backups, "test_list_available_backups"),
        (test_get_first_backup, "test_get_first_backup"),
        (test_get_sms_database, "test_get_sms_database"),
        (test_get_address_book_database, "test_get_address_book_database"),
        (test_copy_sms_database, "test_copy_sms_database"),
    ])
