from helpers import dotenv


async def get_root_password():
    """The runner's root password from the environment. Once fetched from a paired instance over RFC, hence the
    module's name; that link is gone and the value is read here."""
    return dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD) or ""
