from helpers.extension import Extension
from helpers.print_style import PrintStyle
from helpers.secrets import get_secrets_manager

WITHHELD = "[Content withheld: secrets could not be masked in it.]"


class MaskHistoryContent(Extension):

    def execute(self, **kwargs):
        if not self.agent:
            return

        # Get content data from kwargs
        content_data = kwargs.get("content_data")
        if not content_data:
            return

        try:
            secrets_mgr = get_secrets_manager(self.agent.context)

            # Mask the content before adding to history
            content_data["content"] = self._mask_content(content_data["content"], secrets_mgr)
        except Exception as e:
            # Unmasked content would be stored and replayed to the model, so a
            # masking failure stores a refusal in its place.
            PrintStyle.error(f"Secret masking failed, history content withheld: {type(e).__name__}")
            content_data["content"] = WITHHELD

    def _mask_content(self, content, secrets_mgr):
        """Recursively mask secrets in message content."""
        if isinstance(content, str):
            return secrets_mgr.mask_values(content)
        elif isinstance(content, list):
            return [self._mask_content(item, secrets_mgr) for item in content]
        elif isinstance(content, dict):
            return {k: self._mask_content(v, secrets_mgr) for k, v in content.items()}
        else:
            # For other types, return as-is
            return content
