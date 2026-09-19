export type IdentifiedUser = {
  access_token: string;
  token_type: "bearer";
  user_id: string;
  display_name: string;
  expires_at: string;
};

export type InventoryItem = {
  item_id: string;
  label: string;
  owner_id: string;
  shared: number;
  put_at: string;
  expires_on: string | null;
};

export type OperationResult = {
  outcome: "ALLOW" | "WARNING" | "UNKNOWN";
  decision: string;
  message: string;
  session_id: string;
  user_id: string | null;
  item_id: string | null;
  identity_confidence: number;
  item_confidence: number;
  warnings: string[];
  decided_at: string;
};

export type QuestionAnswer = {
  status: "OK" | "NO_SOURCES" | "LLM_NOT_CONFIGURED" | "LLM_UNAVAILABLE";
  answer: string;
  sources: { source: string; text: string }[];
};

type Envelope<T> = { success: true; data: T };
type ErrorEnvelope = { success: false; error: { code: string; message: string } };

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_FRIDGE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

let accessToken: string | null = null;

export class StationApiError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  const payload = (await response.json()) as Envelope<T> | ErrorEnvelope;
  if (!response.ok || !payload.success) {
    const error = "error" in payload ? payload.error : { code: "HTTP_ERROR", message: response.statusText };
    throw new StationApiError(error.code, error.message);
  }
  return payload.data;
}

export const stationApi = {
  baseUrl: API_BASE_URL,

  health() {
    return request<{ status: string; mode: string; camera_owner: string }>("/api/v1/health");
  },

  async identify() {
    const user = await request<IdentifiedUser>("/api/v1/station/identify", { method: "POST" });
    accessToken = user.access_token;
    return user;
  },

  operate(payload: {
    action: "PUT_IN" | "TAKE_OUT";
    label?: string;
    shared?: boolean;
    expires_on?: string | null;
  }) {
    return request<OperationResult>("/api/v1/station/operate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  inventory() {
    return request<{ items: InventoryItem[] }>("/api/v1/inventory").then(({ items }) => items);
  },

  askQuestion(question: string, category: "recipes" | "storage" = "storage") {
    return request<QuestionAnswer>("/api/v1/questions", {
      method: "POST",
      body: JSON.stringify({ question, category }),
    });
  },

  clearToken() {
    accessToken = null;
  },
};
