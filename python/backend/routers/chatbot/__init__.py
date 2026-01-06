from __future__ import annotations

from backend.routers.common import APIRouter
from . import (
    llm,
    chats,
    messages,
    documents,
    votes,
    streams,
    users,
    suggestions_history,
    document_displays,
)

# Aggregator router composing modular subrouters under /chatbot/*
router = APIRouter()

router.include_router(llm.router)
router.include_router(chats.router)
router.include_router(chats.legacy_router)
router.include_router(messages.router)
router.include_router(documents.router)
router.include_router(votes.router)
router.include_router(streams.router)
router.include_router(users.router)
router.include_router(suggestions_history.router)
router.include_router(document_displays.router)


