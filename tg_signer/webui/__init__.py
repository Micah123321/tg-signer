"""WebUI package.

Importing this package should not require NiceGUI until UI entrypoints are used,
so that data/runtime helpers remain available in non-gui environments.
"""


def __getattr__(name: str):
    if name in {"AUTH_CODE_ENV", "build_ui", "main"}:
        from tg_signer.webui import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["build_ui", "main", "AUTH_CODE_ENV"]
