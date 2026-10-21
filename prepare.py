from helpers import dotenv, runtime, settings
import sys
from helpers.print_style import PrintStyle


PrintStyle.standard("Preparing environment...")

try:
    runtime.initialize()

    # The container's own root password used to be set here, because the
    # application SSHed into itself to execute code. It no longer does: code
    # runs in the separate runner container (ADR 0038) and this one has no sshd at
    # all. The call also fails outright now that the application runs
    # unprivileged -- chpasswd needs root -- so every startup logged
    # "Error in preload" for a password nothing would have read.

except Exception as e:
    PrintStyle.error(f"Error in preload: {e}")
