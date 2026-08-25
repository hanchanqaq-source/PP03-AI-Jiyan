from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


_FUND_CODE = re.compile(r"^[0-9]{6}$")
_TEMP_PREFIX = "fund-context-"


@runtime_checkable
class FundAnalysisAdapter(Protocol):
    """Request-scoped adapter for the existing public fund analysis method."""

    storage_mode: str

    def get_fund_analysis(self, code: str, force_refresh: bool = False) -> Mapping[str, Any]: ...


def _is_acceptance_root(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return any(
        parts[index : index + 2] == (".tmp", "acceptance")
        for index in range(max(0, len(parts) - 1))
    )


def _is_child(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


class TransientFundContext:
    """Own one fund adapter for one explicit request and leave no persistent state."""

    def __init__(self, *, adapter: FundAnalysisAdapter, acceptance_root: Path | None = None):
        if adapter is None:
            raise TypeError("adapter is required")
        self._adapter = adapter
        self._closed = False
        self._temp_root: Path | None = None
        self._acceptance_root: Path | None = None
        mode = getattr(adapter, "storage_mode", None)
        if mode not in {"memory", "request_temp"}:
            raise ValueError("adapter storage_mode must be memory or request_temp")
        if mode == "memory":
            return
        if acceptance_root is None:
            raise ValueError("request_temp adapter requires an acceptance_root")
        root = Path(acceptance_root)
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("acceptance_root must not be a symlink")
        resolved = root.resolve(strict=True)
        if not _is_acceptance_root(resolved):
            raise ValueError("request_temp adapter root must be under .tmp/acceptance")
        transient = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=resolved)).resolve(strict=True)
        if not _is_child(transient, resolved) or transient.parent != resolved:
            shutil.rmtree(transient, ignore_errors=True)
            raise ValueError("transient fund root escaped acceptance_root")
        self._acceptance_root = resolved
        self._temp_root = transient
        binder = getattr(adapter, "bind_transient_root", None)
        if not callable(binder):
            self._cleanup_temp_root()
            raise TypeError("request_temp adapter requires bind_transient_root")
        try:
            binder(transient)
        except BaseException:
            self._cleanup_temp_root()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def temp_root(self) -> Path | None:
        return self._temp_root

    def __enter__(self) -> TransientFundContext:
        if self._closed:
            raise RuntimeError("transient fund context is closed")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.close()
        return False

    def analyze(self, code: str) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("transient fund context is closed")
        if not isinstance(code, str) or _FUND_CODE.fullmatch(code) is None:
            raise ValueError("fund code must be exactly six digits")
        result = self._adapter.get_fund_analysis(code, force_refresh=False)
        if not isinstance(result, Mapping):
            raise TypeError("fund analysis adapter must return a mapping")
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_error: BaseException | None = None
        closer = getattr(self._adapter, "close", None)
        try:
            if callable(closer):
                closer()
        except BaseException as error:
            close_error = error
        finally:
            self._cleanup_temp_root()
        if close_error is not None:
            raise close_error

    def _cleanup_temp_root(self) -> None:
        transient = self._temp_root
        acceptance = self._acceptance_root
        if transient is None:
            return
        if (
            acceptance is None
            or transient.parent != acceptance
            or not transient.name.startswith(_TEMP_PREFIX)
            or not _is_child(transient, acceptance)
        ):
            raise RuntimeError("refusing unsafe transient fund cleanup")
        if transient.is_symlink():
            transient.unlink(missing_ok=True)
        elif transient.exists():
            shutil.rmtree(transient)
