from __future__ import annotations

import re
import shutil
import stat
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


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


def _normalized_industry_config(
    value: Mapping[str, object] | None,
) -> tuple[Mapping[str, frozenset[str]], Mapping[str, frozenset[str]]]:
    if value is None:
        return MappingProxyType({}), MappingProxyType({})
    if not isinstance(value, Mapping) or set(value) != {
        "official_allocation_name_to_industry_ids",
        "security_code_to_industry_ids",
    }:
        raise ValueError("official_industry_config requires exact allocation and security maps")

    def normalize(source: object, *, security_codes: bool) -> Mapping[str, frozenset[str]]:
        if not isinstance(source, Mapping):
            raise TypeError("official industry configuration entries must be mappings")
        result: dict[str, frozenset[str]] = {}
        for raw_key, raw_ids in source.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ValueError("official industry configuration keys must not be blank")
            key = raw_key.strip()
            if security_codes and _FUND_CODE.fullmatch(key) is None:
                raise ValueError("security industry configuration requires six-digit codes")
            if not isinstance(raw_ids, (list, tuple, set, frozenset)):
                raise TypeError("official industry IDs must be a sequence")
            ids = frozenset(
                item.strip() for item in raw_ids
                if isinstance(item, str) and item.strip()
            )
            if len(ids) != len(raw_ids) or not ids:
                raise ValueError("official industry IDs must be unique non-blank strings")
            result[key] = ids
        return MappingProxyType(result)

    return (
        normalize(value["official_allocation_name_to_industry_ids"], security_codes=False),
        normalize(value["security_code_to_industry_ids"], security_codes=True),
    )


def _raise_errors(message: str, errors: Sequence[BaseException]) -> None:
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    if all(isinstance(error, Exception) for error in errors):
        raise ExceptionGroup(message, list(errors))  # type: ignore[arg-type]
    raise BaseExceptionGroup(message, list(errors))


class TransientFundContext:
    """Own one fund adapter for one explicit request and leave no persistent state."""

    def __init__(
        self,
        *,
        adapter: FundAnalysisAdapter,
        acceptance_root: Path | None = None,
        official_industry_config: Mapping[str, object] | None = None,
    ):
        if adapter is None:
            raise TypeError("adapter is required")
        self._adapter = adapter
        self._adapter_closed = False
        self._closed = False
        self._temp_root: Path | None = None
        self._acceptance_root: Path | None = None
        self._acceptance_identity: tuple[int, int, int, int] | None = None
        self._temp_identity: tuple[int, int, int, int] | None = None
        self._temp_removed = False
        self._allocation_industry_ids: Mapping[str, frozenset[str]] = MappingProxyType({})
        self._security_industry_ids: Mapping[str, frozenset[str]] = MappingProxyType({})
        try:
            self._allocation_industry_ids, self._security_industry_ids = _normalized_industry_config(
                official_industry_config
            )
            mode = getattr(adapter, "storage_mode", None)
            if mode not in {"memory", "request_temp"}:
                raise ValueError("adapter storage_mode must be memory or request_temp")
            if mode == "memory":
                return
            if acceptance_root is None:
                raise ValueError("request_temp adapter requires an acceptance_root")
            root = Path(acceptance_root).absolute()
            if not _is_acceptance_root(root):
                raise ValueError("request_temp adapter root must be under .tmp/acceptance")
            if not root.exists():
                raise ValueError("request_temp acceptance_root must already exist")
            self._acceptance_root = root
            self._acceptance_identity = self._directory_identity(root)
            self._assert_storage_identity(require_temp=False)
            transient = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=root)).absolute()
            self._temp_root = transient
            self._temp_identity = self._directory_identity(transient)
            if not _is_child(transient, root) or transient.parent != root:
                raise RuntimeError("transient fund root escaped acceptance_root")
            self._assert_storage_identity(require_temp=True)
            binder = getattr(adapter, "bind_transient_root", None)
            if not callable(binder):
                raise TypeError("request_temp adapter requires bind_transient_root")
            self._assert_storage_identity(require_temp=True)
            binder(transient)
            self._assert_storage_identity(require_temp=True)
        except BaseException as original:
            errors: list[BaseException] = [original]
            close_error = self._close_adapter_once()
            if close_error is not None:
                errors.append(close_error)
            try:
                self._cleanup_temp_root()
            except BaseException as cleanup_error:
                errors.append(cleanup_error)
            if len(errors) == 1:
                raise
            _raise_errors("transient fund context initialization and cleanup failed", errors)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def temp_root(self) -> Path | None:
        return self._temp_root

    def official_allocation_matches(self, *, industry_id: str, official_name: str) -> bool:
        return industry_id in self._allocation_industry_ids.get(official_name, frozenset())

    def security_matches(self, *, industry_id: str, security_code: str) -> bool:
        return industry_id in self._security_industry_ids.get(security_code, frozenset())

    def __enter__(self) -> TransientFundContext:
        if self._closed:
            raise RuntimeError("transient fund context is closed")
        return self

    def __exit__(self, _exc_type, exc, _traceback) -> bool:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is not None:
                _raise_errors("fund request and cleanup failed", (exc, cleanup_error))
            raise
        return False

    def analyze(self, code: str) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("transient fund context is closed")
        if not isinstance(code, str) or _FUND_CODE.fullmatch(code) is None:
            raise ValueError("fund code must be exactly six digits")
        self._assert_storage_identity(require_temp=self._temp_root is not None)
        result = self._adapter.get_fund_analysis(code, force_refresh=False)
        self._assert_storage_identity(require_temp=self._temp_root is not None)
        if not isinstance(result, Mapping):
            raise TypeError("fund analysis adapter must return a mapping")
        return result

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        close_error = self._close_adapter_once()
        if close_error is not None:
            errors.append(close_error)
        cleanup_failed = False
        try:
            self._cleanup_temp_root()
        except BaseException as cleanup_error:
            cleanup_failed = True
            errors.append(cleanup_error)
        if not cleanup_failed:
            self._closed = True
        _raise_errors("transient fund context close failed", errors)

    def _close_adapter_once(self) -> BaseException | None:
        if self._adapter_closed:
            return None
        self._adapter_closed = True
        closer = getattr(self._adapter, "close", None)
        if not callable(closer):
            return None
        try:
            closer()
        except BaseException as error:
            return error
        return None

    def _directory_identity(self, path: Path) -> tuple[int, int, int, int]:
        try:
            info = path.stat(follow_symlinks=False)
        except (FileNotFoundError, OSError) as error:
            raise RuntimeError("transient directory identity unavailable") from error
        identity = (info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_reparse_tag", 0))
        if not stat.S_ISDIR(info.st_mode) or identity[-1]:
            raise RuntimeError("reparse directory rejected")
        return identity

    def _assert_storage_identity(self, *, require_temp: bool) -> None:
        if self._acceptance_root is None:
            if require_temp:
                raise RuntimeError("transient directory identity missing")
            return
        if self._acceptance_identity is None:
            raise RuntimeError("acceptance directory identity missing")
        if self._directory_identity(self._acceptance_root) != self._acceptance_identity:
            raise RuntimeError("acceptance directory identity changed")
        if not require_temp:
            return
        if self._temp_root is None or self._temp_identity is None:
            raise RuntimeError("transient directory identity missing")
        if (
            self._temp_root.parent != self._acceptance_root
            or not self._temp_root.name.startswith(_TEMP_PREFIX)
            or not _is_child(self._temp_root, self._acceptance_root)
        ):
            raise RuntimeError("unsafe transient fund path")
        if self._directory_identity(self._temp_root) != self._temp_identity:
            raise RuntimeError("transient directory identity changed")

    def _cleanup_temp_root(self) -> None:
        if self._temp_root is None or self._temp_removed:
            return
        self._assert_storage_identity(require_temp=True)
        try:
            shutil.rmtree(self._temp_root)
        except BaseException:
            if not self._temp_root.exists():
                self._temp_removed = True
            raise
        if self._temp_root.exists():
            raise RuntimeError("transient directory cleanup incomplete")
        self._temp_removed = True
