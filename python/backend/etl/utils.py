import os
import re
from datetime import datetime, timedelta, timezone
import dateutil
from tzlocal import get_localzone
import time
import sys
import logging

logger = logging.getLogger(__name__)

def normalize_phone_number(phone):
    if phone is None:
        return "Unknown"
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', str(phone))

    # If it's a US number (11 digits starting with 1), remove the leading 1
    if len(digits) == 11 and digits.startswith('1'):
        return digits[1:]
    return digits

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    return os.path.join(base_path, relative_path)

def _safe_timestamp(timestamp):
    if timestamp is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(int(timestamp) / 1000000000, timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)

def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None

def clean_contact_name(name):
    parts = name.split()
    return ' '.join([part for part in parts if part.lower() != 'none'])

def convert_timestamp_to_local(date_value):
    if isinstance(date_value, (int, float)):
        # iOS reference date is January 1, 2001
        reference_date = datetime(2001, 1, 1, tzinfo=get_localzone())
        
        # Check if the value is in seconds or nanoseconds
        if date_value > 1e12:  # Likely nanoseconds
            delta = timedelta(microseconds=date_value / 1000)
        else:  # Likely seconds
            delta = timedelta(seconds=date_value)
        
        parsed_date = reference_date + delta
        
        # Sanity check: if date is before 2010 or after current date, return None
        if parsed_date.year < 2010 or parsed_date > datetime.now(get_localzone()):
            return None
        
        return parsed_date.astimezone(get_localzone())
    return None

def parse_timestamp(timestamp):
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(get_localzone())
    elif isinstance(timestamp, str):
        try:
            dt = dateutil.parser.parse(timestamp)
            return dt.astimezone(get_localzone())
        except ValueError:
            print(f"Unable to parse timestamp: {timestamp}")
            return None
    elif isinstance(timestamp, datetime):
        return timestamp.astimezone(get_localzone())
    else:
        print(f"Unexpected timestamp format: {timestamp}")
        return None
    
def write_requests_to_file(requests, batch_number):
    batch_file_name = f"batch_input_{batch_number}_{int(time.time())}.jsonl"
    try:
        with open(batch_file_name, 'w', encoding='utf-8') as f:
            f.write('\n'.join(requests))
        logger.info(f"Batch {batch_number}: Created input file {batch_file_name}.")
        return batch_file_name
    except Exception as e:
        logger.error(f"Batch {batch_number}: Failed to write input file - {e}")
        return None

def remove_file(file_path):
    try:
        os.remove(file_path)
        logger.debug(f"Removed local file {file_path}.")
    except Exception as e:
        logger.error(f"Failed to remove file {file_path} - {e}")

def convert_for_json(obj):
    """Convert objects to JSON-serializable format."""
    if isinstance(obj, dict):
        return {
            str(key) if isinstance(key, tuple) else key: convert_for_json(value)
            for key, value in obj.items()
        }
    elif isinstance(obj, (list, tuple)):
        return [convert_for_json(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    else:
        return str(obj)
    
def round_stat_values(stats_dict: dict, decimal_places: int = 2) -> dict:
    """Round numerical values in stats dictionary to specified decimal places."""
    if isinstance(stats_dict, dict):
        return {k: round_stat_values(v, decimal_places) for k, v in stats_dict.items()}
    elif isinstance(stats_dict, float):
        return round(stats_dict, decimal_places)
    return stats_dict

