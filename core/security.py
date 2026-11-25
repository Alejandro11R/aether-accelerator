"""
Aether Download Accelerator - Security Validation Module
Validación estricta de nombres de archivo, sanitización de nombres remotos
y confinamiento de directorios de salida multiplataforma.
"""

import os
import re
from typing import FrozenSet, Optional
from urllib.parse import unquote

WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/*?:"<>|]')
_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _replace_invalid_and_control_chars(value: str) -> str:
    """Reemplaza caracteres inválidos multiplataforma y de control ASCII por '_'."""
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", value)
    return "".join("_" if ord(ch) < 32 else ch for ch in cleaned)


def _is_within_directory(base_real: str, target_real: str) -> bool:
    """Comprueba si target_real está contenido dentro de base_real sin escape."""
    try:
        return os.path.commonpath([base_real, target_real]) == base_real
    except ValueError:
        # En Windows, rutas en unidades distintas lanzan ValueError en commonpath
        return False


def _has_hidden_component(base_real: str, target_real: str) -> bool:
    """Devuelve True si algún segmento relativo entre base_real y target_real empieza por '.'."""
    rel = os.path.relpath(target_real, base_real)
    if rel == ".":
        return False
    parts = [part for part in rel.replace("\\", "/").split("/") if part]
    return any(part.startswith(".") for part in parts)


def validate_custom_filename(filename: str) -> str:
    """
    Valida estrictamente un nombre de archivo proporcionado por el usuario.

    Rechaza rutas, traversal, archivos ocultos, caracteres de control o inválidos,
    prefijos de unidad de Windows y nombres reservados de Windows.
    """
    if not isinstance(filename, str):
        raise ValueError("El nombre de archivo debe ser una cadena de texto")

    cleaned = filename.strip()
    if not cleaned:
        raise ValueError("El nombre de archivo no puede estar vacío")

    if cleaned.startswith(".") or cleaned.endswith("."):
        raise ValueError("No se permiten archivos ocultos ni nombres terminados en punto")

    if "/" in cleaned or "\\" in cleaned or os.path.isabs(cleaned):
        raise ValueError("El nombre de archivo no puede contener rutas ni separadores")

    if ".." in cleaned:
        raise ValueError("El nombre de archivo no puede contener secuencias '..'")

    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError("El nombre de archivo contiene caracteres de control no permitidos")

    if _WINDOWS_DRIVE_PREFIX_RE.match(cleaned) or _INVALID_FILENAME_CHARS_RE.search(cleaned):
        raise ValueError("El nombre de archivo contiene caracteres inválidos")

    stem = cleaned.split(".", 1)[0].strip().upper()
    if stem in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Nombre reservado del sistema no permitido: {stem}")

    return cleaned


def sanitize_remote_filename(filename: str, fallback: str = "download.bin") -> str:
    """
    Sanitiza un nombre de archivo proveniente de cabeceras HTTP o URLs remotas.

    Decodifica URL iterativamente para neutralizar traversal codificado, extrae
    el último segmento no vacío y reemplaza caracteres peligrosos o reservados.
    """
    if not isinstance(filename, str) or not filename:
        return fallback

    decoded = filename
    for _ in range(5):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded

    normalized = decoded.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return fallback

    candidate = _replace_invalid_and_control_chars(parts[-1])
    candidate = candidate.lstrip(". ").rstrip(". ")

    if not candidate or candidate in {".", ".."}:
        return fallback

    stem = candidate.split(".", 1)[0].strip().upper()
    if stem in WINDOWS_RESERVED_NAMES:
        candidate = f"_{candidate}"

    return candidate


def validate_output_dir(requested_dir: Optional[str], default_dir: str) -> str:
    """
    Valida y resuelve el directorio de salida respetando confinamiento y symlinks.

    Permite el directorio por defecto (y sus subdirectorios no ocultos) o cualquier
    subdirectorio no oculto bajo el HOME del usuario (excluyendo el propio HOME).
    """
    default_real = os.path.realpath(os.path.abspath(os.path.expanduser(default_dir)))

    if requested_dir is None:
        return default_real

    if not isinstance(requested_dir, str):
        raise ValueError("Directorio de destino no permitido")

    stripped = requested_dir.strip()
    if not stripped:
        return default_real

    if "\x00" in stripped:
        raise ValueError("Directorio de destino no permitido")

    target_real = os.path.realpath(os.path.abspath(os.path.expanduser(stripped)))
    home_real = os.path.realpath(os.path.abspath(os.path.expanduser("~")))

    in_default = target_real == default_real or (
        _is_within_directory(default_real, target_real)
        and not _has_hidden_component(default_real, target_real)
    )
    in_home_subdir = (
        target_real != home_real
        and _is_within_directory(home_real, target_real)
        and not _has_hidden_component(home_real, target_real)
    )

    if in_default or in_home_subdir:
        return target_real

    raise ValueError("Directorio de destino no permitido")


def ensure_within_directory(base_dir: str, target_path: str) -> str:
    """
    Verifica que target_path (resuelto con realpath) permanezca dentro de base_dir.
    """
    if not isinstance(target_path, str) or "\x00" in target_path:
        raise ValueError("Ruta de destino inválida")

    base_real = os.path.realpath(os.path.abspath(base_dir))
    target_real = os.path.realpath(os.path.abspath(target_path))

    if not _is_within_directory(base_real, target_real):
        raise ValueError(f"La ruta '{target_path}' escapa del directorio base permitido")

    return target_real
