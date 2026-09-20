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
  owner_display_name: string;
  owner_name?: string;
  shared: boolean;
  access_type: "OWNER" | "SHARED_ALL" | "SHARED_DIRECT" | "PRIVATE_VISIBLE";
  can_edit: boolean;
  can_take: boolean;
  shared_user_ids: string[];
  shared_user_names: string[];
  put_at: string;
  expires_on: string | null;
};

export type Member = {
  user_id: string;
  display_name: string;
};

export type HistoryEvent = {
  event_id: string;
  session_id: string;
  action: "PUT_IN" | "TAKE_OUT" | "INVENTORY_EDIT";
  decision: string;
  occurred_at: string;
  item_id: string | null;
  item_label: string | null;
  owner_name: string | null;
  viewer_role: "OWNER" | "ACTOR" | null;
  related_user_name: string | null;
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

export type ItemCandidate = {
  item_id: string;
  label: string;
  similarity?: number;
  shared: boolean | number;
  owner_id?: string;
  owner_display_name?: string;
  owner_name?: string;
  put_at?: string;
  expires_on?: string | null;
};

export type ItemInspection = {
  inspection_id: string;
  action: "PUT_IN" | "TAKE_OUT";
  expires_at: string;
  identity: { status: "MATCHED"; user_id: string; display_name: string };
  localization: { status: string; score: number; box: number[] | null };
  category: {
    status: "OK" | "UNKNOWN_CATEGORY" | "NOT_RUN";
    top3: { label: string; score: number }[];
  };
  instance: {
    status: "MATCHED" | "AMBIGUOUS" | "NO_MATCH" | "NOT_RUN";
    candidates: ItemCandidate[];
  };
  suggested_label: string;
  authorized_inventory: ItemCandidate[];
  committable: boolean;
  review_state: "READY" | "NO_AUTHORIZED_ITEMS" | "WARN_NOT_OWNER";
  review_decision: "WARN_NOT_OWNER" | null;
  review_message: string | null;
  latency_ms: Record<string, number>;
};

export type QuestionAnswer = {
  status: "OK" | "NO_SOURCES" | "LLM_NOT_CONFIGURED" | "LLM_UNAVAILABLE";
  answer: string;
  sources: { source: string; text: string }[];
};

export type RecipeIngredient = InventoryItem & {
  days_left: number | null;
  priority: boolean;
  date_basis: "PACKAGE" | "FOODKEEPER" | "UNKNOWN";
  reference_date: string | null;
  status: string;
};

export type RecipeRecommendations = {
  today: string;
  soon_days: number;
  ingredients: RecipeIngredient[];
  excluded: RecipeIngredient[];
  status: "OK" | "RETRIEVAL_ONLY" | "LLM_UNAVAILABLE" | "NO_MATCH" | "EMPTY_INVENTORY";
  answer: string;
  recipes: {
    id: string;
    title: string;
    matched: string[];
    missing: string[];
    use_first: string[];
    pantry: string[];
    steps: string[];
    source: string;
    source_title: string;
    provenance: string;
  }[];
};

type Envelope<T> = { success: true; data: T };
type ErrorEnvelope = { success: false; error: { code: string; message: string } };

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_FRIDGE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

let accessToken: string | null = null;
let warningAudioContext: AudioContext | null = null;
let warningAudioBufferPromise: Promise<AudioBuffer> | null = null;

function getWarningAudioContext(): AudioContext {
  if (typeof window === "undefined" || typeof window.AudioContext === "undefined") {
    throw new Error("這個瀏覽器不支援警示音效");
  }
  warningAudioContext ??= new window.AudioContext();
  return warningAudioContext;
}

async function loadWarningAudio(context: AudioContext): Promise<AudioBuffer> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(`${API_BASE_URL}/api/v1/station/warning-audio`, {
    headers,
    cache: "no-store",
  });
  if (!response.ok) throw new Error("警示音效尚未設定");
  return context.decodeAudioData(await response.arrayBuffer());
}

function warningAudioBuffer(context: AudioContext): Promise<AudioBuffer> {
  if (warningAudioBufferPromise) return warningAudioBufferPromise;
  const loading = loadWarningAudio(context);
  warningAudioBufferPromise = loading;
  void loading.catch(() => {
    if (warningAudioBufferPromise === loading) warningAudioBufferPromise = null;
  });
  return loading;
}

export class StationApiError extends Error {
  public readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export type StationPageFailure = {
  requiresLogin: boolean;
  message: string;
};

export function describeStationPageFailure(cause: unknown): StationPageFailure {
  if (cause instanceof StationApiError && cause.code === "UNAUTHORIZED") {
    return {
      requiresLogin: true,
      message: "登入已逾時或後端已重新啟動，請重新進行人臉辨識。",
    };
  }
  return {
    requiresLogin: false,
    message: cause instanceof Error ? cause.message : "本機 API 發生未知錯誤",
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  const payload = (await response.json()) as Envelope<T> | ErrorEnvelope;
  if (!response.ok || !payload.success) {
    const error = "error" in payload ? payload.error : { code: "HTTP_ERROR", message: response.statusText };
    if (error.code === "UNAUTHORIZED") accessToken = null;
    throw new StationApiError(error.code, error.message);
  }
  return payload.data;
}

export const stationApi = {
  baseUrl: API_BASE_URL,

  previewUrl(revision: number) {
    return `${API_BASE_URL}/api/v1/station/preview?revision=${revision}`;
  },

  armWarningAudio() {
    const context = getWarningAudioContext();
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    void warningAudioBuffer(context);
  },

  async playWarningAudio() {
    const context = getWarningAudioContext();
    if (context.state === "suspended") await context.resume();
    const source = context.createBufferSource();
    source.buffer = await warningAudioBuffer(context);
    source.connect(context.destination);
    source.start();
  },

  health() {
    return request<{ status: string; mode: string; camera_owner: string }>("/api/v1/health");
  },

  async identify() {
    const user = await request<IdentifiedUser>("/api/v1/station/identify", { method: "POST" });
    accessToken = user.access_token;
    return user;
  },

  async enroll(displayName: string) {
    const user = await request<IdentifiedUser>("/api/v1/station/enroll", {
      method: "POST",
      body: JSON.stringify({ display_name: displayName }),
    });
    accessToken = user.access_token;
    return user;
  },

  inspect(action: "PUT_IN" | "TAKE_OUT") {
    return request<ItemInspection>("/api/v1/station/inspect", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
  },

  operate(payload: {
    inspection_id: string;
    action: "PUT_IN" | "TAKE_OUT";
    confirmed: boolean;
    label?: string;
    selected_item_id?: string | null;
    add_as_new?: boolean;
    shared?: boolean;
    shared_user_ids?: string[];
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

  updateInventory(itemId: string, payload: {
    label?: string;
    expires_on?: string | null;
    shared?: boolean;
    shared_user_ids?: string[];
  }) {
    return request<InventoryItem>(`/api/v1/inventory/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  members() {
    return request<{ users: Member[] }>("/api/v1/members").then(({ users }) => users);
  },

  history() {
    return request<{ events: HistoryEvent[] }>("/api/v1/history").then(({ events }) => events);
  },

  askQuestion(question: string, category: "recipes" | "storage" = "storage") {
    return request<QuestionAnswer>("/api/v1/questions", {
      method: "POST",
      body: JSON.stringify({ question, category }),
    });
  },

  recipes(question: string) {
    return request<RecipeRecommendations>("/api/v1/recipes/recommend", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
  },

  clearToken() {
    accessToken = null;
  },
};
