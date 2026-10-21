from helpers.extension import Extension
from helpers.print_style import PrintStyle
from helpers.secrets import SecretsManager


class MaskResponseStreamEnd(Extension):
    async def execute(self, **kwargs):
        # Get agent and finalize the streaming filter
        agent = kwargs.get("agent")
        if not agent:
            return

        try:
            # Finalize the response stream filter if it exists
            filter_key = "_resp_stream_filter"
            filter_instance = agent.get_data(filter_key)
            if filter_instance:
                tail = filter_instance.finalize()

                # Print any remaining masked content
                if tail:
                        PrintStyle().stream(tail)

                # Clean up the filter
                agent.set_data(filter_key, None)
        except Exception as e:
            # Not swallowed silently: the response stream end goes on unmasked.
            PrintStyle.error(f"Secret masking failed in the response stream end: {type(e).__name__}")
