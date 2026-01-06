# Conversation Event System

This document describes the new event-driven conversation tracking and analysis system implemented for ChatStats.

## Overview

The conversation event system provides:

1. **Real-time conversation tracking** - Detects when conversations are complete (90 minutes of inactivity)
2. **Event-driven architecture** - Uses Redis pub/sub for decoupled event handling
3. **Multi-pass analysis** - Supports multiple independent analysis passes per conversation
4. **Automatic triggering** - Conversations are automatically analyzed when sealed
5. **Backfill support** - New analysis passes can be run on existing conversations

## System Components

### 1. Event Bus (`backend/celery_service/events.py`)
- Redis pub/sub based event system
- Supports multiple subscribers per event type
- Non-blocking event emission
- Automatic JSON serialization

### 2. Conversation Tracker (`backend/etl/live_sync/conversation_tracker.py`)
- Tracks last message time per chat
- Uses Redis sorted sets for efficient scheduling
- Detects 90-minute conversation gaps
- Emits `conversation_sealed` events

### 3. Analysis Passes (`backend/celery_service/analysis_passes.py`)
- Configurable analysis pass definitions
- Priority-based execution
- Enable/disable individual passes
- Tracks completion status per conversation

### 4. Event Handlers (`backend/celery_service/tasks/conversation_sealing.py`)
- Celery tasks for handling events
- ETL processing for sealed conversations
- Analysis pass triggering
- Subscription checking

### 5. Backfill Utilities (`backend/celery_service/backfill.py`)
- Run new passes on existing conversations
- Coverage statistics
- Dry-run support
- Progress tracking

## Event Flow

```
New Message → WAL Sync → Conversation Tracker → Redis Schedule
                                    ↓
                            (90 minutes later)
                                    ↓
            Event: conversation_sealed → ETL Processing → Event: conversation_ready_for_analysis
                                                                ↓
                                                    Trigger Analysis Passes
```

## Configuration

### Analysis Passes

Edit `backend/celery_service/analysis_passes.py` to configure passes:

```python
ANALYSIS_PASSES = {
    "my_new_pass": {
        "prompt_name": "MyPrompt",
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "description": "My custom analysis",
        "priority": 10,
        "enabled": True
    }
}
```

### Conversation Gap Threshold

Edit `backend/etl/live_sync/conversation_tracker.py`:

```python
self.gap_threshold = timedelta(minutes=90)  # Change threshold here
```

### Beat Schedule

The sealing check runs every minute via Celery Beat. Edit `backend/celery_service/config.py`:

```python
'check-sealed-conversations': {
    'task': 'celery.check_and_seal_conversations',
    'schedule': 60.0,  # Change frequency here
},
```

## Usage

### CLI Management

The system includes a CLI for management and testing:

```bash
# List available analysis passes
python -m backend.cli.conversation_cli list-passes

# Show conversation tracker status
python -m backend.cli.conversation_cli tracker-status -v

# Check for conversations to seal
python -m backend.cli.conversation_cli check-sealing

# Force seal a specific chat (for testing)
python -m backend.cli.conversation_cli force-seal 12345

# Show coverage statistics
python -m backend.cli.conversation_cli stats

# Show missing analyses
python -m backend.cli.conversation_cli missing --chat-id 12345

# Backfill a specific pass
python -m backend.cli.conversation_cli backfill basic --dry-run

# Backfill all passes
python -m backend.cli.conversation_cli backfill-all --limit 100
```

### API Integration

The system automatically integrates with the live sync system. When new messages are detected:

1. WAL watcher syncs messages to database
2. Conversation tracker updates last message time
3. Redis schedules future sealing check
4. After 90 minutes, conversation is sealed
5. ETL creates conversation records
6. Analysis passes are triggered automatically

### Manual Event Emission

For testing or manual operations:

```python
from backend.celery_service.events import event_bus

# Emit a custom event
event_bus.emit("my_event", {
    "data": "value",
    "timestamp": datetime.now().isoformat()
})
```

### Adding New Analysis Passes

1. Add pass configuration to `ANALYSIS_PASSES`:

```python
"sentiment_deep": {
    "prompt_name": "DeepSentiment",
    "prompt_version": 1,
    "prompt_category": "sentiment",
    "description": "Deep sentiment analysis with emotion detection",
    "priority": 15,
    "enabled": True
}
```

2. Create the corresponding prompt template in the database
3. Backfill existing conversations:

```bash
python -m backend.cli.conversation_cli backfill sentiment_deep
```

## Testing

### End-to-End Testing

1. **Start the system:**
   ```bash
   # Start Redis
   redis-server
   
   # Start Celery worker
   celery -A backend.celery_service.app worker --loglevel=info
   
   # Start Celery beat
   celery -A backend.celery_service.app beat --loglevel=info
   
   # Start FastAPI backend
   python backend/main.py
   ```

2. **Send test messages** through your chat application

3. **Monitor conversation tracking:**
   ```bash
   python -m backend.cli.conversation_cli tracker-status -v
   ```

4. **Force seal for testing:**
   ```bash
   python -m backend.cli.conversation_cli force-seal <chat_id>
   ```

5. **Check analysis progress:**
   ```bash
   python -m backend.cli.conversation_cli stats --chat-id <chat_id>
   ```

### Unit Testing

Key test scenarios:

- Conversation tracker updates and scheduling
- Event emission and subscription
- Analysis pass detection and triggering
- Backfill operations
- Error handling and recovery

## Monitoring

### Redis Keys Used

- `chat:last_msg:{chat_id}` - Last message timestamp per chat
- `conversation:check_queue` - Sorted set of scheduled sealing checks
- `events:*` - Event pub/sub channels

### Logs to Monitor

- `[LiveSync]` - Message syncing and conversation tracking
- `[EVENTS]` - Event emission and subscription
- `[ConversationTracker]` - Conversation sealing operations
- `[AnalysisPasses]` - Analysis triggering and completion

### Health Checks

- Conversation tracker Redis connectivity
- Event bus pub/sub functionality
- Celery beat schedule execution
- Analysis pass completion rates

## Troubleshooting

### Common Issues

1. **No conversations being sealed:**
   - Check Redis connectivity
   - Verify Celery beat is running
   - Check conversation tracker logs

2. **Analysis not triggering:**
   - Verify active subscriptions exist
   - Check prompt templates are configured
   - Review analysis pass definitions

3. **High Redis memory usage:**
   - Old conversation tracking keys not expiring
   - Implement key cleanup if needed

4. **Event system not working:**
   - Check Redis pub/sub permissions
   - Verify event subscriptions are set up
   - Monitor event emission logs

### Performance Tuning

- Adjust sealing check frequency based on load
- Tune Redis memory settings for tracking data
- Configure Celery concurrency for analysis tasks
- Monitor conversation tracking overhead

## Future Enhancements

Potential improvements:

1. **Dynamic gap thresholds** per chat type
2. **Smart conversation boundary detection** using content analysis
3. **Priority-based analysis scheduling**
4. **Real-time analysis progress tracking**
5. **Conversation continuation detection** across gaps
6. **Analysis result caching and deduplication** 