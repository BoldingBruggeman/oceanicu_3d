"""Shared path-resolution helpers for cfg_* modules."""

from pathlib import Path


def resolve_folder(src_cfg, **extra) -> Path:
    """Return the resolved folder for a source config namespace.

    Applies folder_template if present, otherwise returns Path(folder).
    """
    context = {**vars(src_cfg), **extra}
    folder = Path(src_cfg.folder)
    if hasattr(src_cfg, 'folder_template'):
        folder = folder / src_cfg.folder_template.format_map(context)
    return folder


def resolve_path(src_cfg, **extra) -> Path:
    """Return the resolved full path (folder + filename) for a source config namespace.

    Uses filename_template if present, otherwise falls back to filename.
    Wildcards (?, *) in the filename pass through untouched.
    """
    context = {**vars(src_cfg), **extra}
    folder = resolve_folder(src_cfg, **extra)
    if hasattr(src_cfg, 'filename_template'):
        filename = src_cfg.filename_template.format_map(context)
    else:
        filename = src_cfg.filename
    return folder / filename
