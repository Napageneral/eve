from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, Set, List, Any
import logging
from asyncio import QueueFull, QueueEmpty


class ChatMessageHub:
    """In-process pub-sub for per-chat message updates.

    Each subscriber receives its own asyncio.Queue so that slow consumers do not
    block others. Publishers drop on overflow to avoid backpressure issues in
    the live-sync pipeline.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[int, Set[asyncio.Queue]] = defaultdict(set)
        self._drop_counts: Dict[int, int] = defaultdict(int)
        self._log = logging.getLogger("backend.services.chat_message_hub")
        # Global subscribers for recency/meta updates (chats/contacts)
        self._all_chats_subscribers: Set[asyncio.Queue] = set()
        self._all_contacts_subscribers: Set[asyncio.Queue] = set()

    # ------------- publisher side --------------------------------------
    def publish(self, chat_id: int, messages: List[dict]) -> None:
        self._log.debug(
            "[ChatMessageHub] publish",
            extra={
                "chat_id": chat_id,
                "batch_size": len(messages),
                "first_guid": messages[0].get("guid") if messages else None,
            }
        )
        for q in list(self._subscribers.get(chat_id, ( ))):
            try:
                q.put_nowait(messages)
            except QueueFull:
                # Drop-head: discard oldest then try to enqueue newest
                try:
                    _ = q.get_nowait()
                except QueueEmpty:
                    pass
                try:
                    q.put_nowait(messages)
                except QueueFull:
                    # Still full; count and occasionally log
                    self._drop_counts[chat_id] += 1
                    if self._drop_counts[chat_id] % 100 == 0:
                        self._log.warning(
                            "[ChatMessageHub] Dropped %d batches for chat %s due to slow consumer",
                            self._drop_counts[chat_id],
                            chat_id,
                        )
        # Broadcast lightweight recency signals for global lists
        try:
            # Determine the latest timestamp string among the batch
            last_ts: str | None = None
            for m in messages:
                ts = m.get("timestamp")
                if ts is None:
                    continue
                s = ts if isinstance(ts, str) else (getattr(ts, "isoformat", lambda: str(ts))())
                if last_ts is None or s > last_ts:
                    last_ts = s
            if last_ts and self._all_chats_subscribers:
                payload = {"type": "message", "chat_id": int(chat_id), "last_message_time": last_ts}
                for q in list(self._all_chats_subscribers):
                    try:
                        q.put_nowait(payload)
                    except QueueFull:
                        try:
                            _ = q.get_nowait()
                        except QueueEmpty:
                            pass
                        try:
                            q.put_nowait(payload)
                        except QueueFull:
                            pass
            if self._all_contacts_subscribers and messages:
                last = messages[-1]
                sender_id = last.get("sender_id")
                if sender_id is not None:
                    ts = last.get("timestamp")
                    ts_str = ts if isinstance(ts, str) else (getattr(ts, "isoformat", lambda: str(ts))())
                    payload_c = {"type": "message", "contact_id": int(sender_id), "last_message_time": ts_str}
                    for q in list(self._all_contacts_subscribers):
                        try:
                            q.put_nowait(payload_c)
                        except QueueFull:
                            try:
                                _ = q.get_nowait()
                            except QueueEmpty:
                                pass
                            try:
                                q.put_nowait(payload_c)
                            except QueueFull:
                                pass
        except Exception:
            self._log.exception("Failed to broadcast global recency updates")

    # ------------- consumer side ---------------------------------------
    def subscribe(self, chat_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[chat_id].add(q)
        self._log.debug(
            "[ChatMessageHub] subscriber added → chat %s (total %d)",
            chat_id,
            len(self._subscribers[chat_id]),
        )
        return q

    def unsubscribe(self, chat_id: int, q: asyncio.Queue) -> None:
        self._subscribers.get(chat_id, set()).discard(q)
        self._log.debug(
            "[ChatMessageHub] subscriber removed → chat %s (total %d)",
            chat_id,
            len(self._subscribers.get(chat_id, set())),
        )

    # ---- Global stream helpers --------------------------------------------
    def subscribe_all_chats(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._all_chats_subscribers.add(q)
        return q

    def unsubscribe_all_chats(self, q: asyncio.Queue) -> None:
        self._all_chats_subscribers.discard(q)

    def subscribe_all_contacts(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._all_contacts_subscribers.add(q)
        return q

    def unsubscribe_all_contacts(self, q: asyncio.Queue) -> None:
        self._all_contacts_subscribers.discard(q)

    def publish_chat_update(self, chat_id: int, **fields: Any) -> None:
        if not self._all_chats_subscribers:
            return
        payload = {"type": "chat_update", "chat_id": int(chat_id), **fields}
        for q in list(self._all_chats_subscribers):
            try:
                q.put_nowait(payload)
            except QueueFull:
                try:
                    _ = q.get_nowait()
                except QueueEmpty:
                    pass
                try:
                    q.put_nowait(payload)
                except QueueFull:
                    pass


# Global singleton
message_hub = ChatMessageHub()


