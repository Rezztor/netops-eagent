
import re, os, importlib, importlib.util, inspect, sys, threading
from importlib.machinery import EXTENSION_SUFFIXES
from types import ModuleType
from typing import Any, Type, TypeVar
from helpers.files import get_abs_path, get_base_dir, is_in_dir, USER_DIR
from fnmatch import fnmatch


T = TypeVar("T")  # Define a generic type variable


def find(file_path: str) -> str:
    """The file that holds the module a `.py` path names, or "" when there is none.

    A delivered image carries the product's own modules compiled, with no source
    beside them, and every caller goes on naming a module by its `.py` path. The
    compiled module wins where both are present, as it does for `import`: one
    tree must not run a module from two files, and a `.py` an update left behind
    must not bring the old code back. Under usr/ only source counts: that tree
    is written at run time, and the checks on what may be put there (an imported
    repository, an upload) look for `.py`.
    """
    abs_path = get_abs_path(file_path)
    if abs_path.endswith(".py"):
        for suffix in EXTENSION_SUFFIXES:
            compiled = abs_path[:-3] + suffix
            if os.path.isfile(compiled) and not is_in_dir(
                os.path.realpath(compiled), os.path.realpath(get_abs_path(USER_DIR))
            ):
                return compiled
    return abs_path if os.path.isfile(abs_path) else ""


def exists(path: str) -> bool:
    """os.path.exists, with a module's `.py` path also true for its compiled form."""
    return os.path.exists(get_abs_path(path)) or bool(find(path))


def list_modules(folder: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    """The modules in a folder, each as the `.py` path that names it, sorted.

    Nothing for a folder that is not there.
    """
    found = set()
    for directory, _, file_names in os.walk(get_abs_path(folder)):
        for file_name in file_names:
            suffix = next((s for s in (".py", *EXTENSION_SUFFIXES) if file_name.endswith(s)), "")
            path = os.path.join(directory, file_name[: -len(suffix)] + ".py")
            if suffix and fnmatch(os.path.basename(path), pattern) and find(path):
                found.add(path)
        if not recursive:
            break
    return sorted(found)


_COMPILED: dict[str, ModuleType] = {}  # the one module each compiled file has, by file
_COMPILED_LOCK = threading.RLock()


def _import_compiled(abs_path: str, file: str) -> ModuleType:
    """The one module object a compiled file has in this process.

    A source file loaded twice gives two modules that share nothing. A compiled
    file keeps its globals in the shared library, so a second load turns the
    functions of the first to the second's variables. It is therefore loaded
    once and kept, and the same object is what `import` gives under the file's
    dotted name: an `import` that came first is reused here, and a load here is
    put where a later `import` finds it. The module is named as its source would
    be, by the last part of the `.py` name, when it is loaded here first.
    """
    with _COMPILED_LOCK:
        if file in _COMPILED:
            return _COMPILED[file]
        short = os.path.basename(abs_path)[: -len(".py")]
        # The package `import` would look in for this file, when there is one.
        parts = os.path.relpath(abs_path[: -len(".py")], get_base_dir()).split(os.sep)
        package = None
        if len(parts) > 1 and all(part.isidentifier() for part in parts):
            try:
                package = importlib.import_module(".".join(parts[:-1]))
            except ImportError:
                package = None
            folders = {os.path.realpath(folder) for folder in getattr(package, "__path__", [])}
            if os.path.realpath(os.path.dirname(file)) not in folders:
                package = None
        dotted = ".".join(parts) if package is not None else ""
        module = sys.modules.get(dotted) if dotted else None
        if module is None:
            spec = importlib.util.spec_from_file_location(short, file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load module from {file}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if dotted:
                sys.modules[dotted] = module
                setattr(package, short, module)
        _COMPILED[file] = module
        return module


def import_module(file_path: str) -> ModuleType:
    # Handle file paths with periods in the name using importlib.util
    abs_path = get_abs_path(file_path)
    # usr/users/ holds what accounts upload and what agents write; nothing there is ever product code.
    if is_in_dir(os.path.realpath(abs_path), os.path.realpath(get_abs_path(USER_DIR, "users"))):
        raise ImportError(f"Refusing to import a module from a user's files: {abs_path}")
    file = find(abs_path)
    if file and file != abs_path:
        return _import_compiled(abs_path, file)
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
    # Compiled modules stay: their code does not change while the process
    # lives, and loading one again would be the second load _import_compiled
    # exists to prevent.
    to_delete = [
        name
        for name, module in list(sys.modules.items())
        if (name == namespace or name.startswith(namespace + "."))
        and not hasattr(module, "__compiled__")
        and not str(getattr(module, "__file__", "")).endswith(tuple(EXTENSION_SUFFIXES))
    ]

    # delete deepest first just to be tidy
    to_delete.sort(key=lambda n: n.count("."), reverse=True)

    for name in to_delete:
        del sys.modules[name]

    importlib.invalidate_caches()
    return to_delete