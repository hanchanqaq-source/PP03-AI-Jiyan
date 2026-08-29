import { ApiError, authHeaders } from "@/lib/api";

export type SubscriptionAuthStatus =
  | "not_installed"
  | "installed_not_logged_in"
  | "logged_in_chatgpt"
  | "logged_in_api_key"
  | "unsupported_version"
  | "status_failed";

export type ConnectionTestStatus =
  | "not_tested"
  | "running"
  | "success"
  | "not_installed"
  | "not_logged_in"
  | "wrong_auth_mode"
  | "quota_or_rate_limited"
  | "timeout"
  | "cancelled"
  | "process_failed"
  | "unexpected_output";

export interface SubscriptionProviderStatus {
  provider_id: "codex";
  installed: boolean;
  version: string | null;
  auth_status: SubscriptionAuthStatus;
  available: boolean;
  test_status: ConnectionTestStatus;
  message: string;
  last_test_at: string | null;
}

async function subscriptionRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/subscription-ai${path}`, {
      ...init,
      headers: {
        ...authHeaders(),
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers || {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("连接不到本机后端，请先启动 PP03 backend。", 0);
  }

  let payload: any = null;
  try {
    payload = await response.json();
  } catch {
    // The endpoint contract is JSON; a non-JSON error is still reported safely below.
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new ApiError("后端需要访问密钥，请在本页底部填写后再重试。", 401);
    }
    throw new ApiError(payload?.detail || `请求失败（HTTP ${response.status}）`, response.status);
  }
  return payload as T;
}

export function loadSubscriptionProviders(refresh = false): Promise<SubscriptionProviderStatus[]> {
  return subscriptionRequest<SubscriptionProviderStatus[]>(`/providers${refresh ? "?refresh=true" : ""}`);
}

export function startCodexLogin(confirmSwitch = false): Promise<SubscriptionProviderStatus> {
  return subscriptionRequest<SubscriptionProviderStatus>("/codex/login", {
    method: "POST",
    body: JSON.stringify({ confirm_switch: confirmSwitch }),
  });
}

export function testCodexConnection(signal?: AbortSignal): Promise<SubscriptionProviderStatus> {
  return subscriptionRequest<SubscriptionProviderStatus>("/codex/test", {
    method: "POST",
    signal,
  });
}

export function cancelCodexTest(): Promise<SubscriptionProviderStatus> {
  return subscriptionRequest<SubscriptionProviderStatus>("/codex/cancel", {
    method: "POST",
  });
}
