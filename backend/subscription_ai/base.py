"""Small provider interface for PP03 subscription-backed AI."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ConnectionTestResult, SubscriptionProviderStatus


class SubscriptionProvider(ABC):
    @abstractmethod
    def get_status(self, force: bool = False) -> SubscriptionProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def start_login(self, confirm_switch: bool = False) -> dict[str, str]:
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_test(self) -> ConnectionTestResult:
        raise NotImplementedError
