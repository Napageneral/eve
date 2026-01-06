import openai
import logging

logger = logging.getLogger(__name__)

OPENAI_API_KEY1 = "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"

OPENAI_API_KEY = OPENAI_API_KEY1

# Set your OpenAI API key
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Disable all OpenAI HTTP request logging
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("openai.http_client").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


class OpenAIClient:
    """Manages interactions with OpenAI API."""
    
    def __init__(self, api_key: str = OPENAI_API_KEY):
        self.client = openai.OpenAI(api_key=api_key)
    
    @classmethod
    def get_default_client(cls):
        """Get a client instance with default API key."""
        return cls(OPENAI_API_KEY)

def get_openai_client():
    return openai_client
    
def _delete_openai_files_bulk(self, batch_ids):
    try:
        file_ids = []
        for batch_id in batch_ids:
            batch_info = self.openai_client.batches.retrieve(batch_id)
            input_file_id = getattr(batch_info, 'input_file_id', None)
            output_file_id = getattr(batch_info, 'output_file_id', None)
            if input_file_id:
                file_ids.append(input_file_id)
            if output_file_id:
                file_ids.append(output_file_id)

        # Bulk delete logic (if supported by OpenAI API)
        for file_id in file_ids:
            self.openai_client.files.delete(file_id)
            logging.info(f"Deleted OpenAI File ID {file_id}.")

    except Exception as e:
        logging.error(f"Error during bulk deletion of OpenAI files: {e}")