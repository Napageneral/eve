# Live-Sync ETL Pipeline

This module implements a near real-time synchronization system that keeps the ChatStats database updated with changes from the macOS Messages app (`chat.db`) and the AddressBook.

## Overview

The live-sync pipeline monitors the SQLite WAL file of `chat.db` for changes and incrementally processes only new messages and attachments since the last sync point. It also periodically syncs contact updates from the AddressBook databases.

## Key Features

1. **WAL-driven**: Uses file system events to detect changes to the `chat.db-wal` file
2. **Incremental**: Only processes new data since the last watermark
3. **Efficient**: Reuses transformation logic from existing ETL components
4. **Low-latency**: Debounces events to handle bursts while maintaining responsiveness
5. **Contact Updates**: Automatically syncs contact name changes from AddressBook every 60 seconds
6. **Contact Merging**: Handles duplicate contacts by merging them intelligently
7. **Real-time Notifications**: WebSocket notifications for contact updates

## Contact Update Features

### Automatic Contact Sync
- **Immediate Startup Sync**: Runs contact sync immediately when the app starts
- Monitors AddressBook databases for changes every 60 seconds after startup
- Only processes modified databases to minimize performance impact
- Handles contact name updates seamlessly
- Ensures users see fresh contact data without waiting

### Contact Merging
- Detects when a nameless contact (e.g., "+1234567890") gets updated with a proper name
- Merges duplicate contacts by updating all message, reaction, and participant references
- Preserves data integrity during merge operations

### WebSocket Notifications
- Notifies connected clients when contact names are updated
- Provides both chat-specific and global contact update notifications
- Enables real-time UI updates without page refresh

### Smart Name Detection
- Identifies contacts that need name updates (phone numbers, emails without names)
- Distinguishes between meaningful name changes and system identifiers
- Preserves existing names when appropriate

## Components

- **state.py**: Manages the synchronization watermark (high-water mark)
- **extractors.py**: Fast queries to extract new data from `chat.db`
- **sync_messages.py**: Processes and imports new messages
- **sync_attachments.py**: Processes and imports new attachments
- **sync_contacts.py**: Handles contact synchronization and merging
- **wal.py**: Watchdog-based file monitor and orchestrator

## Dependencies

- `watchdog`: For the file system monitoring (macOS FSEvents)

## Installation

```
pip install -r requirements.txt
```

## Architecture

```
AX/Overlay          Electron main           backend.live_sync
┌─────────┐  IPC  ┌─────────┐   stdio   ┌───────────────────────┐
│ overlay │◀────▶│ backend │◀──────────│ WAL watcher           │
└─────────┘       └─────────┘           │  ↳ on burst → pull N  │
                                         │  ↳ call mini-ETLs    │
                                         │  ↳ periodic contacts  │
                                         └───────────────────────┘
```

## Contact Update Flow

1. **Startup Sync**: Immediately on app launch, sync all AddressBook changes
2. **Periodic Sync**: Every 60 seconds after startup, check AddressBook databases for modifications
3. **Change Detection**: Compare modification times to detect updated databases
4. **Contact Processing**: Extract and transform contacts from modified databases
5. **Smart Merging**: Identify and merge duplicate contacts intelligently
6. **Database Updates**: Update contact names and merge references
7. **WebSocket Notifications**: Notify connected clients of changes
8. **UI Updates**: Frontend automatically refreshes to show new contact names

## Usage

The watcher starts automatically when the FastAPI application launches. Contact updates happen automatically in the background.

### Testing Contact Updates

1. Open the ChatStats app
2. Have a conversation with a contact that shows as a phone number
3. Add that contact to your system Contacts app with a proper name
4. Within 60 seconds, the contact name should automatically update in ChatStats
5. Check the logs for contact sync activity

## Logging

Contact sync activities are logged with the following levels:
- `INFO`: Successful updates, merges, and sync completions
- `DEBUG`: Detailed sync progress and cache updates
- `WARNING`: Transformation failures and missing contacts
- `ERROR`: Database errors and sync failures

## Configuration

Contact sync behavior can be configured via:
- `SYNC_COOLDOWN_SECONDS`: Minimum time between syncs for the same identifier (default: 60)
- Periodic sync interval: 60 seconds (hardcoded in `wal.py`) 

## Complete Startup Timeline

With the immediate sync feature, here's what happens when the app starts:

1. **T+0s**: App launches, database initialized
2. **T+0.5s**: Live-sync watcher starts
3. **T+1s**: Contact sync runs immediately (no 60-second wait)
4. **T+1.5s**: Contact sync completes, UI shows fresh data
5. **T+2s**: Live message sync begins monitoring
6. **T+61.5s**: Second periodic sync runs
7. **T+121.5s**: Third periodic sync runs (and so on...)

## Benefits

- **Zero Wait Time**: Users see current contact names immediately on startup
- **No Duplicate Work**: The periodic sync handles both startup and ongoing updates
- **Consistent Behavior**: Same sync logic for startup and periodic updates
- **Graceful Degradation**: If sync fails, app still starts normally
- **Smooth User Experience**: No outdated contact information when opening the app 