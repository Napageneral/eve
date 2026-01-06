"""
Conversation analysis workflow - single conversation end-to-end processing
"""
from backend.celery_service.constants import (
    CA_STATUS_PROCESSING,
    CA_DEFAULT_PROMPT_NAME,
    CA_DEFAULT_PROMPT_VERSION,
    CA_DEFAULT_PROMPT_CATEGORY,
)
from backend.services.core.constants import TaskDefaults
import re
import json
from backend.services.llm import LLMService, LLMConfigResolver
import logging

logger = logging.getLogger(__name__)


class ConversationAnalysisWorkflow:
    """High-level workflow that encapsulates the end-to-end conversation analysis.

    This class intentionally contains *no* Celery specific logic – it can therefore be
    called directly by tests or wrapped by a thin Celery task.
    """

    @staticmethod
    def run(
        convo_id: int,
        chat_id: int,
        ca_row_id: int,
        encoded_text: str | None = None,
        publish_global: bool = False,
        **kwargs,
    ) -> dict:
        """Execute the full analysis and return saved result metadata."""

        # Note: We intentionally skip early DB status writes here to avoid extra I/O on hot path.

        # ------------------------------------------------------------------
        # 2. Ensure we have encoded conversation text
        # ------------------------------------------------------------------
        _diag = False
        _preview_len = 600
        if encoded_text is None:
            prompt_name = kwargs.get("prompt_name", CA_DEFAULT_PROMPT_NAME)
            is_commitment = "commitment" in prompt_name.lower()

            if is_commitment:
                # Commitments are disabled (commitments_live pass has enabled=False)
                # If somehow triggered, raise error
                raise ValueError(
                    f"Commitment analysis not yet supported with Eve service. "
                    f"Commitments pass is disabled in analysis_passes.py"
                )
            else:
                # ALWAYS use Eve encoding service for standard analysis
                import requests
                
                logger.info(f"[CA] Using EVE encoding for convo={convo_id} chat={chat_id}")
                resp = requests.post(
                    'http://127.0.0.1:3032/engine/encode',
                    json={'conversation_id': convo_id, 'chat_id': chat_id},
                    timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
                encoded_text = data.get('encoded_text', '')
                
                if not encoded_text:
                    raise ValueError(f"Eve encoding returned empty text for conversation {convo_id}")
                
                logger.info(
                    f"[CA] ✅ Eve encoding: {len(encoded_text)} chars, "
                    f"{data.get('token_count', 0)} tokens, {data.get('message_count', 0)} messages"
                )
        if _diag:
            try:
                logger.debug(
                    f"[CA] Encoded text length={len(encoded_text or '')}. Preview=\n{(encoded_text or '')[:_preview_len]}"
                )
            except Exception:
                pass

        # ------------------------------------------------------------------
        # 3. Compile prompt + context via Eve Context Engine
        # ------------------------------------------------------------------
        from backend.services.eve.client import get_eve_client
        
        # Map legacy prompt names to Eve IDs
        prompt_name = kwargs.get("prompt_name", CA_DEFAULT_PROMPT_NAME)
        prompt_id_map = {
            "ConvoAll": "convo-all-v1",
            "CommitmentExtractionLive": "commitment-extraction-live-v2",
            "CommitmentReconciliation": "commitment-reconciliation-v1",
        }
        eve_prompt_id = prompt_id_map.get(prompt_name, prompt_name)
        
        logger.debug(
            f"[CA] Compiling prompt via Eve: {eve_prompt_id} (legacy_name={prompt_name}, chat={chat_id}, convo={convo_id})"
        )
        
        try:
            eve = get_eve_client()
            
            logger.info(f"[CA] Compiling prompt via Eve: {eve_prompt_id}")
            
            eve_result = eve.execute_prompt(
                prompt_id=eve_prompt_id,
                source_chat=chat_id,
                vars={
                    "conversation_id": convo_id,
                    "chat_id": chat_id,
                    "conversation_text": encoded_text,  # Pass encoded text as var
                },
                budget_tokens=kwargs.get("budget_tokens", 200000)
            )
            
            final_prompt = eve_result["visiblePrompt"]
            response_schema = eve_result.get("responseSchema")
            execution_config = eve_result.get("execution", {})
            fallback_models = execution_config.get("fallbackModels", [])
            retry_on_parse_failure = execution_config.get("retryOnParseFailure", False)
            
            logger.info(
                f"[CA] ✅ Eve compiled: {len(final_prompt)} chars, "
                f"retry_enabled={retry_on_parse_failure}, fallbacks={fallback_models}"
            )
            
            if _diag:
                try:
                    logger.debug(
                        f"[CA] Compiled prompt length={len(final_prompt)}. Preview=\n{final_prompt[:_preview_len]}"
                    )
                    # Log context slices used
                    items = eve_result.get("ledger", {}).get("items", [])
                    if items:
                        slice_summary = ", ".join([f"{item.get('slice')}({item.get('estTokens')}t)" for item in items[:5]])
                        logger.debug(f"[CA] Context items: {slice_summary}")
                except Exception:
                    pass
            
            # Create a minimal prompt_dict for compatibility with downstream code
            prompt_dict = {
                "id": None,  # No database ID (Eve prompts)
                "name": prompt_name,
                "response_schema": response_schema,  # JSON schema from Eve prompt frontmatter
                "default_llm_config": None,  # Use base config below
            }
            
        except Exception as e:
            logger.error(f"[CA] ❌ Eve compilation failed for {eve_prompt_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to compile prompt via Eve: {e}") from e

        logger.info(f"[CA] Step 1/4: Eve compilation complete")
        
        # ------------------------------------------------------------------
        # 5. Resolve LLM config (base → prompt → override)
        # ------------------------------------------------------------------
        logger.info(f"[CA] Step 2/4: Resolving LLM config")
        prompt_llm_config = prompt_dict.get("default_llm_config")
        # Convert Pydantic model to dict if needed, excluding None values
        if prompt_llm_config and hasattr(prompt_llm_config, 'dict'):
            prompt_llm_config = prompt_llm_config.dict(exclude_none=True)
        
        llm_config = LLMConfigResolver.resolve_config(
            base_config={
                "model_name": TaskDefaults.CA_MODEL,
                "temperature": TaskDefaults.CA_TEMPERATURE,
                "max_tokens": TaskDefaults.CA_MAX_TOKENS,
            },
            prompt_config=prompt_llm_config,
            user_override=kwargs.get("llm_config_override"),
        )

        # Dynamic max_tokens based on encoded text CHARACTER length
        try:
            content_length = len(encoded_text or "")
            # INCREASED LIMITS to prevent truncation
            if content_length <= 2_000:  # ~500 tokens
                dyn_ceiling = 4000  # Increased from 1500
            elif content_length <= 8_000:  # ~2000 tokens  
                dyn_ceiling = 6000  # Increased from 3000
            elif content_length <= 15_000:  # ~3750 tokens
                dyn_ceiling = 8000  # Increased from 5000
            else:
                dyn_ceiling = 10_000  # Increased from 8_000
            base_max = llm_config.get("max_tokens") or 10_000
            # Set minimum to 4000 to ensure enough space for JSON
            effective_max = max(4000, min(base_max, dyn_ceiling))
            llm_config = {**llm_config, "max_tokens": effective_max}
            logger.debug(f"[CA] Set max_tokens={effective_max} for content_length={content_length}")
        except Exception:
            # On any failure, proceed with resolved llm_config as-is
            pass

        # ------------------------------------------------------------------
        # 6. Call LLM (with fallback retry on parse failure)
        # ------------------------------------------------------------------
        logger.info(f"[CA] Step 3/4: Calling LLM (model={llm_config.get('model_name')}, max_tokens={llm_config.get('max_tokens')})")
        
        llm_response = None
        models_tried = [llm_config.get('model_name')]
        
        try:
            llm_response = LLMService.call_llm(
                prompt_str=final_prompt,
                llm_config_dict=llm_config,
                response_schema_dict=prompt_dict.get("response_schema"),
            )
            
            # Check if we got valid content or need to retry
            content = llm_response.get("content")
            is_empty_response = (
                content is None or 
                content == {} or 
                content == "" or
                (isinstance(content, dict) and not content)
            )
            
            if is_empty_response and retry_on_parse_failure and fallback_models:
                logger.warning(
                    f"[CA] ⚠️ Primary model returned empty/invalid JSON, retrying with fallbacks: {fallback_models}"
                )
                
                # Try each fallback model
                for fallback_model in fallback_models:
                    models_tried.append(fallback_model)
                    logger.info(f"[CA] Retrying with fallback model: {fallback_model}")
                    
                    fallback_config = llm_config.copy()
                    fallback_config["model_name"] = fallback_model
                    
                    try:
                        llm_response = LLMService.call_llm(
                            prompt_str=final_prompt,
                            llm_config_dict=fallback_config,
                            response_schema_dict=prompt_dict.get("response_schema"),
                        )
                        
                        # Check if this fallback gave us valid content
                        content = llm_response.get("content")
                        is_empty_response = (
                            content is None or 
                            content == {} or 
                            content == "" or
                            (isinstance(content, dict) and not content)
                        )
                        
                        if not is_empty_response:
                            logger.info(f"[CA] ✅ Fallback model {fallback_model} succeeded with valid JSON")
                            break  # Success! Use this response
                        else:
                            logger.warning(f"[CA] ⚠️ Fallback model {fallback_model} also returned empty/invalid JSON")
                    except Exception as fallback_error:
                        logger.warning(f"[CA] ⚠️ Fallback model {fallback_model} failed: {fallback_error}")
                        continue  # Try next fallback
            
            logger.info(
                f"[CA] ✅ LLM call completed (cost=${llm_response.get('usage', {}).get('total_cost', 0):.4f}, "
                f"models_tried={models_tried})"
            )
        except Exception as llm_error:
            logger.error(f"[CA] ❌ LLM call failed after trying models {models_tried}: {llm_error}", exc_info=True)
            raise

        # ------------------------------------------------------------------
        # 7. Persist & return
        # ------------------------------------------------------------------
        logger.info(f"[CA] Step 4/4: Persisting results")
        from .analysis import ConversationAnalysisService
        result_metadata = ConversationAnalysisService.save_analysis_results(
            llm_response_content_str=(llm_response.get("content") or "{}"),
            conversation_id=convo_id,
            chat_id=chat_id,
            cost=llm_response.get("usage", {}).get("total_cost", 0.0),
            input_tokens=llm_response.get("usage", {}).get("input_tokens", 0),
            output_tokens=llm_response.get("usage", {}).get("output_tokens", 0),
            model_name=llm_config["model_name"],
            prompt_template_db_id=prompt_dict.get("id"),  # Now None for Eve prompts
            conversation_analysis_row_id=ca_row_id,
            compiled_prompt_for_llm=final_prompt,
        )
        if _diag:
            try:
                _content = llm_response.get("content")
                if isinstance(_content, (dict, list)):
                    import json as _json
                    _content_preview = (_json.dumps(_content)[:_preview_len])
                else:
                    _content_preview = str(_content)[:_preview_len]
                logger.info(
                    f"[CA] Persisted analysis_result_id={result_metadata.get('id')} content_type={type(_content).__name__} content_preview=\n{_content_preview}"
                )
            except Exception:
                pass

        logger.info(
            f"[CA] Completed: convo_id={convo_id} chat_id={chat_id} cost={llm_response.get('usage', {}).get('total_cost', 0.0)} eve_prompt={eve_prompt_id}"
        )
        # Lifecycle hooks will publish global counters on success/failure
        return {
            "success": True,
            "analysis_id": result_metadata.get("id"),
            "cost": llm_response.get("usage", {}).get("total_cost", 0.0),
            "eve_prompt_id": eve_prompt_id,  # Include in return value
        }

    @staticmethod
    def run_llm_only(
        convo_id: int,
        chat_id: int,
        ca_row_id: int,
        encoded_text: str | None = None,
        **kwargs,
    ) -> dict:
        """Execute only the LLM portion and return payload for persistence.

        Returns a dict payload including llm_response, compiled prompt, and metadata needed for persistence.
        """
        import time as _time
        from backend.services.metrics.runtime_metrics import RuntimeMetrics as _RM
        # Ensure we have encoded text
        if encoded_text is None:
            _t_enc0 = _time.monotonic()
            prompt_name = kwargs.get("prompt_name", CA_DEFAULT_PROMPT_NAME)
            is_commitment = "commitment" in prompt_name.lower()

            if is_commitment:
                # Commitments are disabled (commitments_live pass has enabled=False)
                raise ValueError(
                    f"Commitment analysis not yet supported with Eve service. "
                    f"Commitments pass is disabled in analysis_passes.py"
                )
            
            # ALWAYS use Eve encoding service for standard analysis
            import requests
            
            logger.info(f"[CA.LLM] Using EVE encoding for convo={convo_id} chat={chat_id}")
            resp = requests.post(
                'http://127.0.0.1:3031/engine/encode',
                json={'conversation_id': convo_id, 'chat_id': chat_id},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            encoded_text = data.get('encoded_text', '')
            
            if not encoded_text:
                raise ValueError(f"Eve encoding returned empty text for conversation {convo_id}")
            
            logger.info(
                f"[CA.LLM] ✅ Eve encoding: {len(encoded_text)} chars, "
                f"{data.get('token_count', 0)} tokens, {data.get('message_count', 0)} messages"
            )
            _RM.record_stage("encode_ms", (_time.monotonic() - _t_enc0) * 1000.0)
        _diag = False
        _preview_len = 600
        if _diag:
            try:
                logger.debug(
                    f"[CA.LLM] Encoded text length={len(encoded_text or '')}. Preview=\n{(encoded_text or '')[:_preview_len]}"
                )
            except Exception:
                pass

        # Compile prompt + context via Eve Context Engine
        from backend.services.eve.client import get_eve_client
        
        # Map legacy prompt names to Eve IDs
        prompt_name = kwargs.get("prompt_name", CA_DEFAULT_PROMPT_NAME)
        prompt_id_map = {
            "ConvoAll": "convo-all-v1",
            "CommitmentExtractionLive": "commitment-extraction-live-v2",
            "CommitmentReconciliation": "commitment-reconciliation-v1",
        }
        eve_prompt_id = prompt_id_map.get(prompt_name, prompt_name)
        
        logger.debug(
            f"[CA.LLM] Compiling prompt via Eve: {eve_prompt_id} (legacy_name={prompt_name}, chat={chat_id}, convo={convo_id})"
        )
        
        try:
            eve = get_eve_client()
            
            # VERBOSE: Log what we're sending to Eve
            logger.info(
                f"[CA.LLM] Calling Eve with prompt_id={eve_prompt_id}, "
                f"chat_id={chat_id}, convo_id={convo_id}, "
                f"encoded_text_length={len(encoded_text or '')} chars"
            )
            
            eve_result = eve.execute_prompt(
                prompt_id=eve_prompt_id,
                source_chat=chat_id,
                vars={
                    "conversation_id": convo_id,
                    "chat_id": chat_id,
                    "conversation_text": encoded_text,  # Pass encoded text as var
                },
                budget_tokens=kwargs.get("budget_tokens", 200000)
            )
            
            final_prompt = eve_result["visiblePrompt"]
            context_ledger = eve_result.get("ledger", {})
            response_schema = eve_result.get("responseSchema")  # JSON schema from Eve
            execution_config = eve_result.get("execution", {})
            fallback_models = execution_config.get("fallbackModels", [])
            retry_on_parse_failure = execution_config.get("retryOnParseFailure", False)
            total_tokens = context_ledger.get("totalTokens", 0)
            
            # Log compilation success
            logger.info(
                f"[CA.LLM] ✅ Eve compiled: prompt_length={len(final_prompt)} chars, context_items={len(context_ledger.get('items', []))}, "
                f"retry_enabled={retry_on_parse_failure}, fallbacks={fallback_models}"
            )
            
            # Create a minimal prompt_dict for compatibility with downstream code
            prompt_dict = {
                "id": None,  # No database ID (Eve prompts)
                "name": prompt_name,
                "response_schema": response_schema,  # JSON schema from Eve prompt frontmatter
                "default_llm_config": None,  # Use base config below
            }
            
        except Exception as e:
            logger.error(f"[CA.LLM] ❌ Eve compilation failed for {eve_prompt_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to compile prompt via Eve: {e}") from e

        logger.info(f"[CA.LLM] Step 1/4: Eve compilation complete")
        
        # Resolve LLM config
        logger.info(f"[CA.LLM] Step 2/4: Resolving LLM config")
        llm_config = LLMConfigResolver.resolve_config(
            base_config={
                "model_name": TaskDefaults.CA_MODEL,
                "temperature": TaskDefaults.CA_TEMPERATURE,
                "max_tokens": TaskDefaults.CA_MAX_TOKENS,
            },
            prompt_config=prompt_dict.get("default_llm_config"),
            user_override=kwargs.get("llm_config_override"),
        )

        # Dynamic max_tokens based on encoded text CHARACTER length
        try:
            content_length = len(encoded_text or "")
            # INCREASED LIMITS to prevent truncation
            if content_length <= 2_000:  # ~500 tokens
                dyn_ceiling = 4000  # Increased from 1500
            elif content_length <= 8_000:  # ~2000 tokens  
                dyn_ceiling = 6000  # Increased from 3000
            elif content_length <= 15_000:  # ~3750 tokens
                dyn_ceiling = 8000  # Increased from 5000
            else:
                dyn_ceiling = 10_000  # Increased from 8_000
            base_max = llm_config.get("max_tokens") or 10_000
            # Set minimum to 4000 to ensure enough space for JSON
            effective_max = max(4000, min(base_max, dyn_ceiling))
            llm_config = {**llm_config, "max_tokens": effective_max}
            logger.debug(f"[CA] Set max_tokens={effective_max} for content_length={content_length}")
        except Exception:
            # On any failure, proceed with resolved llm_config as-is
            pass

        # Call LLM (with fallback retry on parse failure)
        logger.info(f"[CA.LLM] Step 3/4: Calling LLM (model={llm_config.get('model_name')}, max_tokens={llm_config.get('max_tokens')})")
        _t_llm0 = _time.monotonic()
        
        llm_response = None
        models_tried = [llm_config.get('model_name')]
        
        try:
            llm_response = LLMService.call_llm(
                prompt_str=final_prompt,
                llm_config_dict=llm_config,
                response_schema_dict=prompt_dict.get("response_schema"),
            )
            llm_duration = (_time.monotonic() - _t_llm0) * 1000.0
            _RM.record_stage("llm_ms", llm_duration)
            
            # Check if we got valid content or need to retry
            content = llm_response.get("content")
            is_empty_response = (
                content is None or 
                content == {} or 
                content == "" or
                (isinstance(content, dict) and not content)
            )
            
            if is_empty_response and retry_on_parse_failure and fallback_models:
                logger.warning(
                    f"[CA.LLM] ⚠️ Primary model returned empty/invalid JSON, retrying with fallbacks: {fallback_models}"
                )
                
                # Try each fallback model
                for fallback_model in fallback_models:
                    models_tried.append(fallback_model)
                    logger.info(f"[CA.LLM] Retrying with fallback model: {fallback_model}")
                    
                    fallback_config = llm_config.copy()
                    fallback_config["model_name"] = fallback_model
                    
                    try:
                        llm_response = LLMService.call_llm(
                            prompt_str=final_prompt,
                            llm_config_dict=fallback_config,
                            response_schema_dict=prompt_dict.get("response_schema"),
                        )
                        
                        # Check if this fallback gave us valid content
                        content = llm_response.get("content")
                        is_empty_response = (
                            content is None or 
                            content == {} or 
                            content == "" or
                            (isinstance(content, dict) and not content)
                        )
                        
                        if not is_empty_response:
                            logger.info(f"[CA.LLM] ✅ Fallback model {fallback_model} succeeded with valid JSON")
                            break  # Success! Use this response
                        else:
                            logger.warning(f"[CA.LLM] ⚠️ Fallback model {fallback_model} also returned empty/invalid JSON")
                    except Exception as fallback_error:
                        logger.warning(f"[CA.LLM] ⚠️ Fallback model {fallback_model} failed: {fallback_error}")
                        continue  # Try next fallback
                
                # After trying all fallbacks, check if we still have empty response
                final_content = llm_response.get("content")
                is_still_empty = (
                    final_content is None or 
                    final_content == {} or 
                    final_content == "" or
                    (isinstance(final_content, dict) and not final_content)
                )
                
                if is_still_empty:
                    # All fallbacks failed - now log the full raw content
                    raw_content = llm_response.get("raw", "")
                    logger.warning(
                        f"[CA.LLM] ❌ All models ({models_tried}) returned empty/invalid JSON. "
                        f"Full raw response from last attempt follows:\n{raw_content}"
                    )
            
            llm_duration = (_time.monotonic() - _t_llm0) * 1000.0
            _RM.record_stage("llm_ms", llm_duration)
            
            logger.info(
                f"[CA.LLM] ✅ LLM call completed ({llm_duration:.0f}ms, "
                f"cost=${llm_response.get('usage', {}).get('total_cost', 0):.4f}, "
                f"models_tried={models_tried})"
            )
        except Exception as llm_error:
            logger.error(f"[CA.LLM] ❌ LLM call failed after trying models {models_tried}: {llm_error}", exc_info=True)
            raise

        return {
            "convo_id": convo_id,
            "chat_id": chat_id,
            "ca_row_id": ca_row_id,
            "prompt_template_db_id": prompt_dict.get("id"),  # None for Eve prompts
            "eve_prompt_id": eve_prompt_id,  # Track Eve prompt ID
            "model_name": llm_config["model_name"],
            "llm_response": {
                "content": llm_response.get("content"),
                "usage": llm_response.get("usage", {}),
                "model": llm_response.get("model"),
                "timings": llm_response.get("timings", {}),
            },
            "llm_finish_ts": _time.time(),
            "compiled_prompt": final_prompt,
        }

    @staticmethod
    def persist_only(payload: dict) -> dict:
        """Persist the results only, returning final metadata for UI/metrics."""
        import time as _time
        from backend.services.metrics.runtime_metrics import RuntimeMetrics as _RM
        convo_id = int(payload["convo_id"])
        chat_id = int(payload["chat_id"])
        ca_row_id = int(payload["ca_row_id"])
        prompt_template_db_id = int(payload["prompt_template_db_id"])
        model_name = payload.get("model_name", "")
        llm_response = payload.get("llm_response", {}) or {}

        if payload.get("llm_finish_ts"):
            try:
                _RM.record_stage("db_queue_lag_ms", (_time.time() - float(payload["llm_finish_ts"])) * 1000.0)
            except Exception:
                pass

        _t_persist0 = _time.monotonic()

        from .analysis import ConversationAnalysisService
        result_metadata = ConversationAnalysisService.save_analysis_results(
            llm_response_content_str=(llm_response.get("content") or "{}"),
            conversation_id=convo_id,
            chat_id=chat_id,
            cost=llm_response.get("usage", {}).get("total_cost", 0.0),
            input_tokens=llm_response.get("usage", {}).get("input_tokens", 0),
            output_tokens=llm_response.get("usage", {}).get("output_tokens", 0),
            model_name=model_name,
            prompt_template_db_id=prompt_template_db_id,
            conversation_analysis_row_id=ca_row_id,
            compiled_prompt_for_llm=payload.get("compiled_prompt"),
        )
        # Quiet persist log
        _RM.record_stage("persist_ms", (_time.monotonic() - _t_persist0) * 1000.0)

        return {
            "success": True,
            "analysis_id": result_metadata.get("id"),
            "conversation_id": convo_id,
            "chat_id": chat_id,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _process_prompt(prompt_dict: dict, encoded_text: str) -> str:
        """Replace conversation placeholders in prompt text."""
        final_prompt: str = prompt_dict.get("prompt_text", "")

        mapping = prompt_dict.get("placeholder_mapping") or {}
        # Coerce JSON string to dict if necessary
        if isinstance(mapping, str):
            try:
                mapping = json.loads(mapping)
            except Exception:
                mapping = {}

        replaced_any = False
        _diag = True
        _preview_len = 1200
        if _diag:
            try:
                logger.info(
                    f"[PROMPT] Starting substitution. initial_len={len(final_prompt)} mapping={mapping} encoded_preview=\n{encoded_text[:_preview_len]}"
                )
            except Exception:
                pass

        found_any = []
        for placeholder_key, context_type in mapping.items():
            if context_type in {"conversation_text", "EncodedConversationData"}:
                # Robust patterns: allow optional whitespace inside braces
                # IMPORTANT: match longest (triple) first to avoid replacing only the inner braces
                regexes = (
                    rf"\{{\{{\{{\s*{re.escape(placeholder_key)}\s*\}}\}}\}}",  # {{{placeholder}}}
                    rf"\{{\{{\s*{re.escape(placeholder_key)}\s*\}}\}}",          # {{placeholder}}
                    rf"\{{\s*{re.escape(placeholder_key)}\s*\}}",                    # {placeholder}
                )
                for rx in regexes:
                    # Use a function replacement to avoid backslash-escape processing in repl
                    new_prompt, num = re.subn(rx, lambda _m: encoded_text, final_prompt)
                    if num > 0:
                        final_prompt = new_prompt
                        replaced_any = True
                        found_any.append(rx)
                        if _diag:
                            try:
                                logger.info(f"[PROMPT] Replaced {num} occurrence(s) via regex='{rx}' for key='{placeholder_key}'")
                            except Exception:
                                pass

        # Fallback: if nothing matched from mapping, try well-known tokens directly
        if not replaced_any:
            fallback_keys = ("conversation_text", "EncodedConversationData")
            for key in fallback_keys:
                # Longest-first here as well
                regexes = (
                    rf"\{{\{{\{{\s*{re.escape(key)}\s*\}}\}}\}}",
                    rf"\{{\{{\s*{re.escape(key)}\s*\}}\}}",
                    rf"\{{\s*{re.escape(key)}\s*\}}",
                )
                for rx in regexes:
                    new_prompt, num = re.subn(rx, lambda _m: encoded_text, final_prompt)
                    if num > 0:
                        final_prompt = new_prompt
                        replaced_any = True
                        found_any.append(rx)
                        if _diag:
                            try:
                                logger.info(f"[PROMPT] Fallback replaced {num} occurrence(s) via regex='{rx}'")
                            except Exception:
                                pass

        # Loud log if placeholders appear to remain unreplaced
        try:
            if (not replaced_any) and any(tok in final_prompt for tok in ("EncodedConversationData", "conversation_text")):
                logger.error(
                    "[PROMPT] Encoded text was NOT injected. Placeholders remain in prompt. keys=%s found_any=%s",
                    list(mapping.keys()),
                    found_any,
                )
        except Exception:
            pass

        return final_prompt 


__all__ = ["ConversationAnalysisWorkflow"] 