from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


_FUND_CODE = re.compile(r"^[0-9]{6}$")
_TEMP_PREFIX = "fund-context-"


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = (("delete_file", wintypes.BOOL),)

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _KERNEL32.SetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


class _DirectoryGuard:
    """Retain an OS directory identity and deny rename/delete while it is guarded."""

    def __init__(self, path: Path, *, deletable: bool = False):
        self.path = path
        self._handle: int | None = None
        self._fd: int | None = None
        if os.name == "nt":
            desired_access = 0x0080 | (0x00010000 if deletable else 0)
            handle = _KERNEL32.CreateFileW(
                str(path), desired_access, 0x00000001 | 0x00000002, None, 3,
                0x02000000 | 0x00200000, None,
            )
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = int(handle)
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            self._fd = os.open(path, flags)
            try:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(self._fd)
                self._fd = None
                raise
        self.saved_identity = self.identity()

    def identity(self) -> tuple[int, int, int, int]:
        if os.name == "nt":
            if self._handle is None:
                raise RuntimeError("directory guard is closed")
            info = _ByHandleFileInformation()
            if not _KERNEL32.GetFileInformationByHandle(self._handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & 0x00000400:
                raise RuntimeError("reparse directory rejected")
            return (
                int(info.volume_serial_number),
                int(info.file_index_high),
                int(info.file_index_low),
                int(info.attributes),
            )
        if self._fd is None:
            raise RuntimeError("directory guard is closed")
        info = os.fstat(self._fd)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("directory guard is not a directory")
        return (info.st_dev, info.st_ino, info.st_mode, 0)

    def delete_empty(self) -> None:
        if os.name == "nt":
            if self._handle is None:
                raise RuntimeError("directory guard is closed")
            disposition = _FileDispositionInformation(True)
            if not _KERNEL32.SetFileInformationByHandle(
                self._handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.close()
            return
        os.rmdir(self.path)
        self.close()

    def close(self) -> None:
        if os.name == "nt":
            if self._handle is None:
                return
            handle = self._handle
            if not _KERNEL32.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None
            return
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


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
            key = raw_key.strip() if security_codes else raw_key.strip().casefold()
            if security_codes and _FUND_CODE.fullmatch(key) is None:
                raise ValueError("security industry configuration requires six-digit codes")
            if key in result:
                raise ValueError("official industry configuration key collision")
            if not isinstance(raw_ids, (list, tuple, set, frozenset)):
                raise TypeError("official industry IDs must be a sequence")
            normalized_ids: set[str] = set()
            for raw_id in raw_ids:
                if not isinstance(raw_id, str) or not raw_id.strip():
                    raise ValueError("official industry IDs must be non-blank strings")
                industry_id = raw_id.strip().casefold()
                if industry_id in normalized_ids:
                    raise ValueError("official industry ID collision")
                normalized_ids.add(industry_id)
            if not normalized_ids:
                raise ValueError("official industry IDs must not be empty")
            result[key] = frozenset(normalized_ids)
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
        self._ancestor_guards: list[
            tuple[Path, tuple[int, int, int, int], _DirectoryGuard, tuple[int, int, int, int]]
        ] = []
        self._temp_guard: _DirectoryGuard | None = None
        self._temp_guard_identity: tuple[int, int, int, int] | None = None
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
            self._acquire_ancestor_guards(root)
            self._assert_storage_identity(require_temp=False)
            transient = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=root)).absolute()
            self._temp_root = transient
            self._temp_identity = self._directory_identity(transient)
            self._temp_guard = _DirectoryGuard(transient, deletable=True)
            self._temp_guard_identity = self._temp_guard.saved_identity
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
                errors.extend(self._release_all_guards())
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
        if not isinstance(industry_id, str) or not industry_id.strip():
            return False
        if not isinstance(official_name, str) or not official_name.strip():
            return False
        return industry_id.strip().casefold() in self._allocation_industry_ids.get(
            official_name.strip().casefold(), frozenset()
        )

    def security_matches(self, *, industry_id: str, security_code: str) -> bool:
        if not isinstance(industry_id, str) or not industry_id.strip():
            return False
        if not isinstance(security_code, str):
            return False
        security_code = security_code.strip()
        if _FUND_CODE.fullmatch(security_code) is None:
            return False
        return industry_id.strip().casefold() in self._security_industry_ids.get(
            security_code, frozenset()
        )

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

    def _acquire_ancestor_guards(self, root: Path) -> None:
        parts = root.parts
        start = next(
            index for index in range(len(parts) - 1)
            if parts[index].casefold() == ".tmp" and parts[index + 1].casefold() == "acceptance"
        )
        acquired: list[
            tuple[Path, tuple[int, int, int, int], _DirectoryGuard, tuple[int, int, int, int]]
        ] = []
        try:
            for length in range(start + 1, len(parts) + 1):
                path = Path(*parts[:length]).absolute()
                path_identity = self._directory_identity(path)
                guard = _DirectoryGuard(path)
                guard_identity = guard.saved_identity
                if self._directory_identity(path) != path_identity or guard.identity() != guard_identity:
                    guard.close()
                    raise RuntimeError("guarded ancestor directory identity changed")
                acquired.append((path, path_identity, guard, guard_identity))
        except BaseException:
            for _path, _path_identity, guard, _guard_identity in reversed(acquired):
                guard.close()
            raise
        self._ancestor_guards = acquired

    def _assert_ancestor_guards(self) -> None:
        if self._acceptance_root is None:
            return
        if not self._ancestor_guards:
            raise RuntimeError("acceptance directory guards missing")
        for path, path_identity, guard, guard_identity in self._ancestor_guards:
            if self._directory_identity(path) != path_identity:
                raise RuntimeError("guarded ancestor directory identity changed")
            if guard.identity() != guard_identity:
                raise RuntimeError("guarded ancestor handle identity changed")

    def _release_ancestor_guards(self) -> None:
        errors: list[BaseException] = []
        retained: list[
            tuple[Path, tuple[int, int, int, int], _DirectoryGuard, tuple[int, int, int, int]]
        ] = []
        for entry in reversed(self._ancestor_guards):
            try:
                entry[2].close()
            except BaseException as error:
                errors.append(error)
                retained.append(entry)
        self._ancestor_guards = list(reversed(retained))
        _raise_errors("acceptance directory guard release failed", errors)

    def _release_all_guards(self) -> list[BaseException]:
        errors: list[BaseException] = []
        if self._temp_guard is not None:
            try:
                self._temp_guard.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._temp_guard = None
        try:
            self._release_ancestor_guards()
        except BaseException as error:
            errors.append(error)
        return errors

    def _assert_storage_identity(self, *, require_temp: bool) -> None:
        if self._acceptance_root is None:
            if require_temp:
                raise RuntimeError("transient directory identity missing")
            return
        if self._acceptance_identity is None:
            raise RuntimeError("acceptance directory identity missing")
        self._assert_ancestor_guards()
        if self._directory_identity(self._acceptance_root) != self._acceptance_identity:
            raise RuntimeError("acceptance directory identity changed")
        if not require_temp:
            return
        if (
            self._temp_root is None
            or self._temp_identity is None
            or self._temp_guard is None
            or self._temp_guard_identity is None
        ):
            raise RuntimeError("transient directory identity missing")
        if (
            self._temp_root.parent != self._acceptance_root
            or not self._temp_root.name.startswith(_TEMP_PREFIX)
            or not _is_child(self._temp_root, self._acceptance_root)
        ):
            raise RuntimeError("unsafe transient fund path")
        if self._directory_identity(self._temp_root) != self._temp_identity:
            raise RuntimeError("transient directory identity changed")
        if self._temp_guard.identity() != self._temp_guard_identity:
            raise RuntimeError("transient directory handle identity changed")

    def _cleanup_temp_root(self) -> None:
        if self._temp_root is None:
            self._release_ancestor_guards()
            return
        if self._temp_removed:
            self._release_ancestor_guards()
            return
        self._assert_storage_identity(require_temp=True)
        for child in tuple(self._temp_root.iterdir()):
            self._assert_storage_identity(require_temp=True)
            info = child.stat(follow_symlinks=False)
            if getattr(info, "st_reparse_tag", 0) or stat.S_ISLNK(info.st_mode):
                raise RuntimeError("reparse cleanup entry rejected")
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(child)
            elif stat.S_ISREG(info.st_mode):
                child.unlink()
            else:
                raise RuntimeError("unsupported transient cleanup entry")
            self._assert_storage_identity(require_temp=True)
        self._assert_storage_identity(require_temp=True)
        if self._temp_guard is None:
            raise RuntimeError("transient directory guard missing")
        self._temp_guard.delete_empty()
        self._temp_guard = None
        if self._temp_root.exists():
            raise RuntimeError("transient directory cleanup incomplete")
        self._temp_removed = True
        self._assert_ancestor_guards()
        self._release_ancestor_guards()
