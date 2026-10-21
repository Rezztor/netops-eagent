
import re, os, importlib, importlib.util, inspect, sys
from types import ModuleType
from typing import Any, Type, TypeVar
from helpers.files import get_abs_path, is_in_dir, USER_DIR
from fnmatch import fnmatch


T = TypeVar("T")  # Define a generic type variable


def find(file_path: str) -> str:
    """The file a `.py` path names, or "" when there is none."""
    abs_path = get_abs_path(file_path)
    return abs_path if os.path.isfile(abs_path) else ""


def exists(path: str) -> bool:
    return os.path.exists(get_abs_path(path))


def list_modules(folder: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """The `.py` files in a folder, sorted. Nothing for a folder that is not there."""
    found = []
    for directory, _, file_names in os.walk(get_abs_path(folder)):
        found += [
            os.path.join(directory, name)
            for name in file_names
            if name.endswith(".py") and fnmatch(name, pattern)
        ]
        if not recursive:
            break
    return sorted(found)


def import_module(file_path: str) -> ModuleType:
    # Handle file paths with periods in the name using importlib.util
    abs_path = get_abs_path(file_path)
    # usr/users/ holds what accounts upload and what agents write; nothing there is ever product code.
    if is_in_dir(os.path.realpath(abs_path), os.path.realpath(get_abs_path(USER_DIR, "users"))):
        raise ImportError(f"Refusing to import a module from a user's files: {abs_path}")
    module_name = os.path.basename(abs_path).replace(".py", "")

    # Create the module spec and load the module
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {abs_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_classes_from_folder(
    folder: str, name_pattern: str, base_class: Type[T], one_per_file: bool = True
) -> list[Type[T]]:
    classes = []

    # Every module in the folder that matches the pattern, sorted alphabetically
    for file_path in list_modules(folder, name_pattern):
        module = import_module(file_path)

        # Get all classes in the module
        class_list = inspect.getmembers(module, inspect.isclass)

        # Filter for classes that are subclasses of the given base_class
        # iterate backwards to skip imported superclasses
        for cls in reversed(class_list):
            if cls[1] is not base_class and issubclass(cls[1], base_class):
                classes.append(cls[1])
                if one_per_file:
                    break

    return classes


def load_classes_from_file(
    file: str, base_class: type[T], one_per_file: bool = True
) -> list[type[T]]:
    classes = []
    # Use the new import_module function
    module = import_module(file)

    # Get all classes in the module
    class_list = inspect.getmembers(module, inspect.isclass)

    # Filter for classes that are subclasses of the given base_class
    # iterate backwards to skip imported superclasses
    for cls in reversed(class_list):
        if cls[1] is not base_class and issubclass(cls[1], base_class):
            classes.append(cls[1])
            if one_per_file:
                break

    return classes


def purge_namespace(namespace: str):
    to_delete = [name for name in list(sys.modules) if name == namespace or name.startswith(namespace + ".")]

    # delete deepest first just to be tidy
    to_delete.sort(key=lambda n: n.count("."), reverse=True)

    for name in to_delete:
        del sys.modules[name]

    importlib.invalidate_caches()
    return to_delete