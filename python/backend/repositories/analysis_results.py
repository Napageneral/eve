"""
Repository for saving conversation analysis results.
Handles entities, topics, emotions, humor, and conversation summaries.
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from .core.generic import GenericRepository
import json
import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class AnalysisResultsRepository(GenericRepository):
    """Repository for saving conversation analysis results."""
    
    TABLE = "conversation_analyses"  # Primary table
    
    @classmethod
    def save_analysis_results(
        cls, 
        session: Session,
        conversation_id: int,
        chat_id: int,
        analysis_data: Dict[str, Any],
        ca_row_id: int,
        raw_completion_id: Optional[int] = None,
        eve_prompt_id: Optional[str] = None  # Track Eve prompt ID
    ) -> Dict[str, Any]:
        """Save all analysis results (summary, entities, topics, etc)."""
        # Update conversation analysis record (with Eve prompt tracking)
        if ca_row_id:
            cls.execute(session, 
                """UPDATE conversation_analyses 
                   SET status = :status, completion_id = :completion_id, 
                       eve_prompt_id = :eve_prompt_id,
                       error_message = NULL, updated_at = :updated_at 
                   WHERE id = :id""",
                {
                    "status": "success",
                    "completion_id": raw_completion_id,
                    "eve_prompt_id": eve_prompt_id,
                    "updated_at": datetime.utcnow(),
                    "id": ca_row_id
                }
            )
        
        # Update conversation summary
        summary = analysis_data.get("summary", "")
        cls.execute(session, 
            "UPDATE conversations SET summary = :summary WHERE id = :conversation_id",
            {"summary": summary, "conversation_id": conversation_id}
        )
        
        # Clear existing analysis data
        for table_name in ["entities", "topics", "emotions", "humor_items"]:
            cls.execute(session,
                f"DELETE FROM {table_name} WHERE conversation_id = :conv_id",
                {"conv_id": conversation_id}
            )
        
        # Get participant look-ups via ContactRepository (deduplicated logic)
        from .contacts import ContactRepository as ContactRepo
        name_map = ContactRepo.get_name_map_for_chat(session, chat_id)
        me_id = ContactRepo.get_me_contact_id_for_chat(session, chat_id)
        
        # ------------------------------------------------------------------
        # Dimension persistence (entities, topics, emotions, humor)
        # ------------------------------------------------------------------

        entity_count = cls._save_dimension(
            session,
            conversation_id,
            chat_id,
            participant_data=analysis_data.get("entities", []),
            sub_key="entities",
            table="entities",
            value_column="title",
            name_map=name_map,
            me_id=me_id,
        )

        topic_count = cls._save_dimension(
            session,
            conversation_id,
            chat_id,
            participant_data=analysis_data.get("topics", []),
            sub_key="topics",
            table="topics",
            value_column="title",
            name_map=name_map,
            me_id=me_id,
        )

        emotion_count = cls._save_dimension(
            session,
            conversation_id,
            chat_id,
            participant_data=analysis_data.get("emotions", []),
            sub_key="emotions",
            table="emotions",
            value_column="emotion_type",
            name_map=name_map,
            me_id=me_id,
        )

        humor_count = cls._save_dimension(
            session,
            conversation_id,
            chat_id,
            participant_data=analysis_data.get("humor", []),
            sub_key="humor",
            table="humor_items",
            value_column="snippet",
            name_map=name_map,
            me_id=me_id,
            clean_snippet=True,
        )
        
        return {
            "summary_length": len(summary),
            "entities_count": entity_count,
            "topics_count": topic_count,
            "emotions_count": emotion_count,
            "humor_count": humor_count
        }

    # ------------------------------------------------------------------
    # Generic dimension saver
    # ------------------------------------------------------------------

    @classmethod
    def _save_dimension(
        cls,
        session,
        conversation_id: int,
        chat_id: int,
        *,
        participant_data: List[Dict[str, Any]],
        sub_key: str,
        table: str,
        value_column: str,
        name_map: Dict[str, int],
        me_id: int,
        clean_snippet: bool = False,
    ) -> int:
        """Generic handler to persist analysis dimension rows.

        Parameters
        ----------
        participant_data: List of dicts returned by the LLM workflow for a
            particular dimension (entities, topics, ...). Each element should
            have keys `participant_name` and the *sub_key* list field.
        sub_key: Name of the list in *participant_data* holding the individual
            items (e.g. "entities", "topics").
        table: Destination DB table.
        value_column: Column in *table* that will hold the extracted value
            ("title", "emotion_type", "snippet", ...).
        clean_snippet: When True, applies extra regex cleanup suitable for the
            humor *snippet* field.
        """

        rows: List[Dict[str, Any]] = []

        def _safe_name_lookup(mapping: Dict[str, int], name: str, me_contact_id: int) -> int:
            if not name:
                return me_contact_id
            # Exact match
            if name in mapping:
                return mapping[name]
            # Case-insensitive match
            lower_map = {k.lower(): v for k, v in mapping.items()}
            return lower_map.get(name.lower(), me_contact_id)

        from collections.abc import Mapping
        import json as _json

        for raw in participant_data or []:
            # Normalize item to a dict with participant_name and list under sub_key
            if isinstance(raw, Mapping):
                p_data = dict(raw)
            elif isinstance(raw, str):
                s = raw.strip()
                # Try to parse JSON if it looks like an object
                if s.startswith("{") and s.endswith("}"):
                    try:
                        p_data = _json.loads(s)
                    except Exception:
                        p_data = {"participant_name": s, sub_key: []}
                else:
                    p_data = {"participant_name": s, sub_key: []}
            else:
                logger.warning("Unexpected %s payload for conversation %s: %r", sub_key, conversation_id, raw)
                continue

            p_name = (p_data.get("participant_name")
                      or p_data.get("name")
                      or "")
            contact_id = _safe_name_lookup(name_map, p_name, me_id)

            items = p_data.get(sub_key) or []
            # Allow a single string as the item list for robustness
            if isinstance(items, str):
                items = [{"name": items}]

            for item in items:
                if isinstance(item, str):
                    item = {"name": item}
                if not isinstance(item, dict):
                    continue

                value = item.get("name", "") if value_column != "snippet" else item.get("message", "")

                if value_column == "snippet" and clean_snippet:
                    value = re.sub(r"\s*\[\[.*?\]\]\s*", "", value).strip()
                    value = re.sub(r"\s*<<.*?>>\s*", "", value).strip()

                if not value:
                    continue

                rows.append({
                    "conversation_id": conversation_id,
                    "chat_id": chat_id,
                    "contact_id": contact_id,
                    value_column: value,
                    "created_at": datetime.utcnow(),
                })

        from .analysis_items.bulk_insert import bulk_insert_items
        return bulk_insert_items(session, table=table, rows=rows)
    
    # ------------------------------------------------------------------
    # Backwards-compat: keep internal helpers as thin delegates so existing
    # imports/tests don’t break. They’re slated for removal after callers migrate.
    # ------------------------------------------------------------------

    @classmethod
    def _get_contact_name_mapping(cls, session: Session, chat_id: int) -> Dict[str, int]:
        from .contacts import ContactRepository as ContactRepo
        return ContactRepo.get_name_map_for_chat(session, chat_id)
    
    @classmethod
    def _get_me_contact_id(cls, session: Session, chat_id: int) -> int:
        from .contacts import ContactRepository as ContactRepo
        return ContactRepo.get_me_contact_id_for_chat(session, chat_id)
    
    @classmethod
    def parse_llm_json_response(cls, content: Any) -> Dict[str, Any]:
        """Parse and fix common JSON errors from LLM responses.

        Accepts dict/str/None and returns a best-effort dict.
        """
        # Handle missing/structured content upfront
        if content is None:
            logger.warning("LLM returned no content; using empty analysis object")
            return {}
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            logger.warning("LLM returned list content; ignoring and using empty analysis object")
            return {}

        # Fast-path for already valid JSON strings
        try:
            if isinstance(content, str):
                return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Normalize to string for tolerant repair path
        s = str(content).strip()
        # Remove illegal control characters that JSON does not permit
        try:
            s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
        except Exception:
            pass
        if s.startswith("```"):
            s = s.strip('`')
            # Remove potential language tag like ```json\n
            if s.startswith("json"):
                s = s[4:]
        # Trim leading/trailing non-JSON noise
        first = min([i for i in [s.find('{'), s.find('[')] if i != -1] or [0])
        last_brace = max(s.rfind('}'), s.rfind(']'))
        if last_brace != -1:
            s = s[first:last_brace + 1]

        # Apply tolerant fixes
        fixed_content = cls._fix_json_errors(s)
        # Attempt to fix common trailing bracket/comma issues iteratively
        for _ in range(2):
            try:
                return json.loads(fixed_content)
            except json.JSONDecodeError as e:
                # If expecting comma delimiter or property name, try inserting missing commas between items
                if 'Expecting' in str(e):
                    try:
                        fixed_content = re.sub(r'\]\s*"', '], "', fixed_content)
                        fixed_content = re.sub(r'\}\s*\{', '}, {', fixed_content)
                        fixed_content = re.sub(r'"\s*\{', '", {', fixed_content)
                    except Exception:
                        pass
                # Balance braces/brackets again just in case
                try:
                    open_obj = fixed_content.count('{'); close_obj = fixed_content.count('}')
                    if open_obj > close_obj:
                        fixed_content += '}' * (open_obj - close_obj)
                    open_arr = fixed_content.count('['); close_arr = fixed_content.count(']')
                    if open_arr > close_arr:
                        fixed_content += ']' * (open_arr - close_arr)
                except Exception:
                    pass
        try:
            return json.loads(fixed_content)
        except json.JSONDecodeError as e:
            logger.error("❌ Final JSON parse failed after repairs: %s", e)
            
            # VERBOSE: Log the problematic content for debugging
            logger.error("Raw LLM response (first 500 chars): %s", raw_content[:500])
            logger.error("Fixed content (first 500 chars): %s", fixed_content[:500])
            logger.error("Content length: raw=%d chars, fixed=%d chars", len(raw_content), len(fixed_content))
            
            # Check for truncation
            if len(raw_content) > 10000:
                logger.warning("⚠️ Large response detected (%d chars) - possible truncation issue?", len(raw_content))
            
            # Degrade gracefully: return empty analysis so persist never fails
            return {}
    
    @classmethod
    def _fix_json_errors(cls, content: str) -> str:
        """Attempt to fix common JSON errors."""
        # Close unmatched quotes inside string values by escaping stray quotes
        content = content.replace('\r', '\n')
        # Insert commas between adjacent objects
        content = re.sub(r'}\s*{', '},{', content)
        # Merge duplicate array keys (entities/topics/emotions/humor) within the same object
        try:
            for key in ("entities", "topics", "emotions", "humor"):
                # Repeatedly merge pairs of duplicate arrays: "key": [A], "key": [B] -> "key": [A, B]
                pattern = re.compile(rf'("{key}"\s*:\s*\[)(.*?)(\]\s*,\s*"{key}"\s*:\s*\[)(.*?)(\])', re.DOTALL)
                while True:
                    new_content, n = pattern.subn(rf'"{key}": [\2, \4]', content)
                    content = new_content
                    if n == 0:
                        break
        except Exception:
            pass
        # Quote unquoted keys
        content = re.sub(r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):', r'\1"\2"\3:', content)
        # Quote obvious unquoted scalar values
        content = re.sub(r':\s*([^"{\[\],\s][^,{}\[\]]*?)(?=\s*[,}])', r': "\1"', content)
        
        open_count = content.count('{')
        close_count = content.count('}')
        if open_count > close_count:
            content += '}' * (open_count - close_count)
        # Balance square brackets as well
        try:
            open_sq = content.count('[')
            close_sq = content.count(']')
            if open_sq > close_sq:
                content += ']' * (open_sq - close_sq)
        except Exception:
            pass
        
        # Close unterminated string
        if re.search(r':\s*"[^"\\]*(?:\\.[^"\\]*)*$', content):
            content += '"'
        
        if content.strip().endswith(','):
            content = content.strip()[:-1]
        # Escape stray backslashes that aren't valid JSON escapes
        try:
            content = re.sub(r'(?<!\\)\\(?![\\/"bfnrtu])', r'\\\\', content)
        except Exception:
            pass
        return content 