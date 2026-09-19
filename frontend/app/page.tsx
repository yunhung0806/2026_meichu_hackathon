"use client";

import { useEffect, useState } from "react";
import { stationApi } from "@/lib/api";
import type { HistoryEvent, IdentifiedUser, InventoryItem, ItemInspection, Member, OperationResult, QuestionAnswer } from "@/lib/api";
import { canEditInventoryItem, groupInventoryItems, inventorySharingLabel, saveInventoryEdit, takeChoiceOwner, takeChoiceTitle } from "@/lib/inventory-view";
import RecipeView from "./recipe-view";

type Tab = "home" | "items" | "recipes" | "history" | "ask";
type Flow = "idle" | "register" | "enrolling" | "recognizing" | "menu" | "scanning" | "review" | "committing" | "result" | "error";

export default function Home() {
  const [showEntrance, setShowEntrance] = useState(true);
  const [tab, setTab] = useState<Tab>("home");
  const [flow, setFlow] = useState<Flow>("idle");
  const [user, setUser] = useState<IdentifiedUser | null>(null);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [online, setOnline] = useState(false);
  const [label, setLabel] = useState("");
  const [shareUserIds, setShareUserIds] = useState<string[]>([]);
  const [expiry, setExpiry] = useState("");
  const [result, setResult] = useState<OperationResult | null>(null);
  const [inspection, setInspection] = useState<ItemInspection | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string>("");
  const [addAsNew, setAddAsNew] = useState(false);
  const [error, setError] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [today, setToday] = useState("今天");

  useEffect(() => {
    stationApi.health().then(() => setOnline(true)).catch(() => setOnline(false));
    const dateTimer = window.setTimeout(() => setToday(new Intl.DateTimeFormat("zh-TW", { month: "long", day: "numeric", weekday: "long" }).format(new Date())), 0);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const entranceTimer = window.setTimeout(() => setShowEntrance(false), reduceMotion ? 100 : 2800);
    return () => { window.clearTimeout(dateTimer); window.clearTimeout(entranceTimer); };
  }, []);

  function showError(cause: unknown) {
    setError(cause instanceof Error ? cause.message : "本機 API 發生未知錯誤");
    setFlow("error");
  }

  async function refreshInventory() {
    if (!user) return;
    try { setInventory(await stationApi.inventory()); } catch (cause) { showError(cause); }
  }

  async function refreshHistory() {
    if (!user) return;
    try { setHistory(await stationApi.history()); } catch (cause) { showError(cause); }
  }

  async function beginRecognition() {
    setError("");
    setLabel("");
    setFlow("recognizing");
    try {
      const identified = await stationApi.identify();
      setUser(identified);
      const [nextInventory, nextMembers] = await Promise.all([
        stationApi.inventory(), stationApi.members(),
      ]);
      setInventory(nextInventory);
      setMembers(nextMembers);
      setFlow("menu");
    } catch (cause) { showError(cause); }
  }

  function beginEnrollment() {
    setError("");
    setNewUserName("");
    setFlow("register");
  }

  async function enrollUser() {
    const name = newUserName.trim();
    if (!name) { setError("請先輸入你的名稱。"); return; }
    setError("");
    setFlow("enrolling");
    try {
      const enrolled = await stationApi.enroll(name);
      setUser(enrolled);
      setInventory([]);
      setMembers(await stationApi.members());
      setFlow("menu");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法建立人臉資料");
      setFlow("register");
    }
  }

  async function scanItem(action: "PUT_IN" | "TAKE_OUT") {
    setError("");
    setLabel("");
    setSelectedItemId("");
    setAddAsNew(false);
    setShareUserIds([]);
    setExpiry("");
    setFlow("scanning");
    try {
      const next = await stationApi.inspect(action);
      setInspection(next);
      if (next.review_decision === "WARN_NOT_OWNER") {
        void stationApi.playWarningAudio().catch(() => undefined);
      }
      setLabel(next.suggested_label);
      if (action === "PUT_IN") {
        setAddAsNew(true);
      } else if (next.instance.status === "MATCHED" && next.instance.candidates[0]) {
        setSelectedItemId(next.instance.candidates[0].item_id);
      }
      setFlow("review");
    } catch (cause) { showError(cause); }
  }

  async function commitInspection() {
    if (!inspection) return;
    if (!inspection.committable) { setError("無法定位物品，請重新掃描。"); return; }
    if (inspection.action === "PUT_IN" && !label.trim()) { setError("請確認或輸入物品名稱。"); return; }
    if (needsItemChoice(inspection, selectedItemId, addAsNew)) { setError("請先選擇要使用的庫存物品，或選擇新增。"); return; }
    setError("");
    setFlow("committing");
    try {
      const operation = await stationApi.operate({
        inspection_id: inspection.inspection_id,
        action: inspection.action,
        confirmed: true,
        label: inspection.action === "PUT_IN" ? label.trim() : undefined,
        selected_item_id: selectedItemId || null,
        add_as_new: addAsNew,
        shared: false,
        shared_user_ids: shareUserIds,
        expires_on: expiry || null,
      });
      setResult(operation);
      setInventory(await stationApi.inventory());
      setFlow("result");
    } catch (cause) { showError(cause); }
  }

  async function selectTab(next: Tab) {
    setTab(next);
    if (next === "items" && user) await refreshInventory();
    if (next === "history" && user) await refreshHistory();
  }

  function closeFlow() {
    setFlow("idle");
    setError("");
    setResult(null);
    setInspection(null);
  }

  return <main className="app-shell">
    {showEntrance && <EntranceIntro onSkip={() => setShowEntrance(false)} />}
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark" aria-hidden="true"></span><div><strong>冰友 ChillMate</strong><small>共享冰箱管家</small></div></div>
      <nav aria-label="主要導覽">
        <NavButton active={tab === "home"} label="首頁" icon="⌂" onClick={() => void selectTab("home")} />
        <NavButton active={tab === "items"} label="冰箱物品" icon="▦" count={String(inventory.length)} onClick={() => void selectTab("items")} />
        <NavButton active={tab === "recipes"} label="食譜推薦" icon="♨" onClick={() => void selectTab("recipes")} />
        <NavButton active={tab === "history"} label="使用紀錄" icon="↻" onClick={() => void selectTab("history")} />
        <NavButton active={tab === "ask"} label="問冰箱" icon="✦" onClick={() => void selectTab("ask")} />
      </nav>
      <div className="sidebar-bottom"><div className="profile"><div className="avatar">{user?.display_name.slice(0, 1) ?? "?"}</div><div><strong>{user?.display_name ?? "尚未辨識"}</strong></div></div></div>
    </aside>
    <section className="content">
      <header className="topbar"><div><span className="eyebrow">{today}</span><h1>{tabTitle(tab)}</h1></div></header>
      {tab === "home" && flow === "idle" && <HomeView inventory={inventory} online={online} user={user} onStart={beginRecognition} onEnroll={beginEnrollment} onTab={selectTab} />}
      {tab === "items" && <ItemsView items={inventory} identified={Boolean(user)} onRefresh={async () => { setInventory(await stationApi.inventory()); }} />}
      {tab === "recipes" && (user ? <RecipeView key={`${user.user_id}:${user.access_token}`} /> : <UnavailableView title="請先辨識使用者" detail="回首頁辨識後，就能依你的庫存與保存期限推薦料理。" />)}
      {tab === "history" && <HistoryView events={history} identified={Boolean(user)} />}
      {tab === "ask" && <AskView identified={Boolean(user)} />}
    </section>
    {flow !== "idle" && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="冰箱操作"><div className="flow-card"><button className="close" onClick={closeFlow} aria-label="關閉">×</button>
      {flow === "recognizing" && <Recognizing text="正在辨識使用者" />}
      {flow === "register" && <EnrollmentForm name={newUserName} setName={setNewUserName} error={error} onSubmit={() => void enrollUser()} />}
      {flow === "enrolling" && <Recognizing text="正在建立你的人臉資料" enrollment />}
      {flow === "menu" && user && <ActionMenu user={user} onPut={() => void scanItem("PUT_IN")} onTake={() => void scanItem("TAKE_OUT")} />}
      {flow === "scanning" && <Recognizing text="正在掃描物品，此階段不會修改庫存" />}
      {flow === "review" && inspection && <ReviewItem inspection={inspection} label={label} setLabel={setLabel} selectedItemId={selectedItemId} setSelectedItemId={setSelectedItemId} addAsNew={addAsNew} setAddAsNew={setAddAsNew} members={members} shareUserIds={shareUserIds} setShareUserIds={setShareUserIds} expiry={expiry} setExpiry={setExpiry} error={error} onConfirm={() => void commitInspection()} onRescan={() => void scanItem(inspection.action)} />}
      {flow === "committing" && <Recognizing text="正在確認並更新庫存" />}
      {flow === "result" && result && <ResultView result={result} onDone={closeFlow} />}
      {flow === "error" && <ErrorView message={error} onRetry={beginRecognition} />}
    </div></div>}
  </main>;
}

function NavButton({ active, label, icon, count, onClick }: { active: boolean; label: string; icon: string; count?: string; onClick: () => void }) { return <button className={active ? "nav-item active" : "nav-item"} onClick={onClick}><span>{icon}</span>{label}{count && <b>{count}</b>}</button>; }
function tabTitle(tab: Tab) { return { home: "冰友 ChillMate", items: "冰箱裡有什麼？", recipes: "食譜推薦", history: "使用紀錄", ask: "問問你的冰箱" }[tab]; }

function EntranceIntro({ onSkip }: { onSkip: () => void }) {
  return <button className="entrance-intro" onClick={onSkip} aria-label="跳過進場動畫">
    <span className="intro-light"></span>
    <span className="intro-fridge" aria-hidden="true"><i></i><b></b></span>
    <span className="intro-wordmark">ChillMate</span>
  </button>;
}

function CameraPreview({ guidance }: { guidance: string }) {
  const [revision, setRevision] = useState(() => Date.now());
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    const timer = window.setInterval(() => setRevision(Date.now()), 350);
    return () => window.clearInterval(timer);
  }, []);

  return <figure className="camera-preview">
    <div className="camera-preview-frame">
      {/* The Python station owns the camera; this is an uncached loopback JPEG. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={stationApi.previewUrl(revision)}
        alt="本機攝影機即時預覽，綠框是物品辨識範圍"
        onLoad={() => setAvailable(true)}
        onError={() => setAvailable(false)}
      />
      {!available && <div className="camera-preview-error">等待本機攝影機預覽…</div>}
    </div>
    <figcaption><b>即時畫面</b><span>{guidance}</span></figcaption>
  </figure>;
}

function HomeView({ inventory, online, user, onStart, onEnroll, onTab }: { inventory: InventoryItem[]; online: boolean; user: IdentifiedUser | null; onStart: () => void; onEnroll: () => void; onTab: (tab: Tab) => Promise<void> }) {
  const expiring = inventory.filter(item => item.expires_on).length;
  const shared = inventory.filter(item => Boolean(item.shared)).length;
  return <div className="page-grid"><section className="hero-card"><div className="hero-copy"><h2>{user ? `你好，${user.display_name}` : "請登入以繼續使用"}</h2><p>{user ? "歡迎回來，選擇放入或取出物品開始使用。" : "站到鏡頭前，讓冰友辨識你並載入專屬庫存。"}</p><div className="identity-actions"><button className="primary-button" onClick={onStart} disabled={!online}><span className="scan-icon">◎</span>{online ? (user ? "重新辨識使用者" : "開始人臉辨識") : "等待本機 API"}<b>→</b></button><button className="secondary-button" onClick={onEnroll} disabled={!online}><span>＋</span>我是新人</button></div></div><div className="camera-visual" aria-hidden="true"><div className="camera-ring smart-core"><span className="orbit orbit-one"></span><span className="orbit orbit-two"></span><span className="orbit orbit-three"></span><div className="smart-fridge"><span className="ai-lens"><i></i></span><b></b><em></em></div><div className="signal-ring signal-one"></div><div className="signal-ring signal-two"></div><span className="scan-sweep"></span></div></div></section><section className="home-hub" aria-label="冰箱快速入口"><DashboardCard className="inventory-tile" icon="▦" eyebrow="INVENTORY" label="目前庫存" value={inventory.length} detail="查看所有已登記物品" onClick={() => void onTab("items")} /><DashboardCard className="expiry-tile" icon="◷" eyebrow="EXPIRY" label="有期限紀錄" value={expiring} detail="掌握需要優先處理的食物" onClick={() => void onTab("items")} /><DashboardCard className="shared-tile" icon="♙" eyebrow="SHARED" label="共用物品" value={shared} detail="每位成員都能安心取用" onClick={() => void onTab("items")} /><button className="dashboard-card ask-tile" onClick={() => void onTab("ask")}><span className="dashboard-icon">✦</span><span className="dashboard-copy"><small>FOODKEEPER RAG</small><strong>問問你的冰箱</strong><em>依真實庫存與保存指引回答你的問題</em></span><span className="card-arrow">開始提問 →</span></button></section></div>;
}

function DashboardCard({ className, icon, eyebrow, label, value, detail, onClick }: { className: string; icon: string; eyebrow: string; label: string; value: number; detail: string; onClick: () => void }) { return <button className={`dashboard-card ${className}`} onClick={onClick}><span className="dashboard-icon">{icon}</span><span className="dashboard-copy"><small>{eyebrow}</small><strong>{label}</strong><em>{detail}</em></span><span className="dashboard-value">{value}<small>件</small></span><span className="card-arrow">查看 →</span></button>; }
function ItemsView({ items, identified, onRefresh }: { items: InventoryItem[]; identified: boolean; onRefresh: () => Promise<void> }) {
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ itemId: string; label: string; expiry: string; shared: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const groups = groupInventoryItems(items);

  function beginEdit(item: InventoryItem) {
    setEditError("");
    setEditing({
      itemId: item.item_id,
      label: item.label,
      expiry: item.expires_on ?? "",
      shared: Boolean(item.shared),
    });
  }

  async function saveEdit() {
    if (!editing || !editing.label.trim()) {
      setEditError("名稱不可為空白。");
      return;
    }
    setSaving(true);
    setEditError("");
    try {
      await saveInventoryEdit(
        () => stationApi.updateInventory(editing.itemId, {
          label: editing.label.trim(),
          expires_on: editing.expiry || null,
          shared: editing.shared,
        }),
        onRefresh,
      );
      setEditing(null);
    } catch (cause) {
      setEditError(cause instanceof Error ? cause.message : "無法儲存物品資料");
    } finally {
      setSaving(false);
    }
  }

  return <div className="page-stack">
    <div className="filter-row"><button className="chip selected">目前庫存 {items.length} 件 · {groups.length} 組</button></div>
    {!identified ? <UnavailableView title="請先辨識使用者" /> : items.length === 0 ? <UnavailableView title="目前沒有物品" /> : <div className="inventory-grid">
      {groups.map(group => {
        const expanded = expandedKeys.includes(group.key);
        const owners = new Set(group.items.map(item => item.owner_id));
        const expiries = new Set(group.items.map(item => item.expires_on ?? ""));
        return <article className="food-card inventory-group-card" key={group.key}>
          <button className="inventory-group-toggle" aria-expanded={expanded} onClick={() => setExpandedKeys(expanded ? expandedKeys.filter(key => key !== group.key) : [...expandedKeys, group.key])}>
            <span className="food-emoji">▣</span>
            <span className="inventory-group-copy">
              <span className="expiry-badge fresh">{group.earliestExpiry ? `最近期限 ${group.earliestExpiry}` : "未填期限"}</span>
              <strong>{group.label} × {group.items.length}</strong>
              <small>{owners.size > 1 ? "多位擁有者" : group.items[0].owner_display_name} · {expiries.size > 1 ? "多個期限" : "相同期限"}</small>
            </span>
            <b>{expanded ? "收合" : "查看明細"}</b>
          </button>
          {expanded && <div className="inventory-records">
            {group.items.map(item => <section className="inventory-record" key={item.item_id}>
              <div className="inventory-record-main">
                <strong>{item.label}</strong>
                <span className={item.expires_on ? "record-expiry" : "record-expiry missing"}>{item.expires_on ?? "未填期限"}</span>
              </div>
              <div className="inventory-record-meta">
                <span>擁有者：{item.owner_display_name}</span>
                <span>放入：{formatTime(item.put_at)}</span>
                <span>{inventorySharingLabel(item)}</span>
                <span>{item.can_take ? "可取出" : "僅可查看"}</span>
              </div>
              {canEditInventoryItem(item) ? <button className="secondary-button inventory-edit-button" onClick={() => beginEdit(item)}>編輯</button> : null}
              {editing?.itemId === item.item_id && <div className="inventory-edit-form">
                <label>名稱<input maxLength={200} value={editing.label} disabled={saving} onChange={event => setEditing({ ...editing, label: event.target.value })} /></label>
                <label>期限<input type="date" value={editing.expiry} disabled={saving} onChange={event => setEditing({ ...editing, expiry: event.target.value })} /></label>
                <label className="inventory-share-toggle"><input type="checkbox" checked={editing.shared} disabled={saving} onChange={event => setEditing({ ...editing, shared: event.target.checked })} /> 所有已登入使用者可取出</label>
                {editError && <p className="rag-hint">{editError}</p>}
                <div className="inventory-edit-actions"><button className="secondary-button" disabled={saving} onClick={() => { setEditing(null); setEditError(""); }}>取消</button><button className="primary-button compact" disabled={saving || !editing.label.trim()} onClick={() => void saveEdit()}>{saving ? "儲存中…" : "儲存"}</button></div>
              </div>}
            </section>)}
          </div>}
        </article>;
      })}
    </div>}
  </div>;
}
function HistoryView({ events, identified }: { events: HistoryEvent[]; identified: boolean }) {
  if (!identified) return <UnavailableView title="請先辨識使用者" detail="辨識後會顯示你的操作，以及涉及你物品的阻擋紀錄。" />;
  if (events.length === 0) return <UnavailableView title="目前沒有使用紀錄" />;
  return <section className="panel history-panel" aria-label="使用紀錄">
    {events.map(event => {
      const allowed = event.decision.startsWith("ALLOW") || event.decision === "ITEM_REGISTERED";
      const actionLabel = event.action === "PUT_IN" ? "放入" : "取出";
      const itemLabel = event.item_label ?? event.item_id?.slice(0, 8) ?? "未辨識物品";
      const warningDetail = event.decision === "WARN_NOT_OWNER"
        ? event.viewer_role === "OWNER"
          ? `${event.related_user_name ?? "其他使用者"} 嘗試取出你的物品，已阻止。`
          : `這是 ${event.related_user_name ?? "其他使用者"} 的物品，取出已阻止。`
        : null;
      return <article className="history-row" key={event.event_id}>
        <span className={`event-icon ${allowed ? "green" : "orange"}`}>{event.action === "PUT_IN" ? "↓" : "↑"}</span>
        <div className="history-main"><strong>{actionLabel} · <em>{itemLabel}</em></strong><small>{warningDetail ?? event.decision}</small></div>
        <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
      </article>;
    })}
  </section>;
}
function UnavailableView({ title, detail }: { title: string; detail?: string }) { return <section className="panel"><div className="panel-head"><div><h3>{title}</h3>{detail && <p>{detail}</p>}</div></div></section>; }
function AskView({ identified }: { identified: boolean }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QuestionAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [askError, setAskError] = useState("");
  const suggestions = ["我週末要回家，哪些食物需要先處理？", "蘋果可以冷藏多久？", "哪些食物接近一般保存建議？"];

  async function submit() {
    if (!identified) { setAskError("請先回首頁進行人臉辨識。"); return; }
    if (!question.trim()) { setAskError("請先輸入問題。"); return; }
    setLoading(true);
    setAskError("");
    setAnswer(null);
    try { setAnswer(await stationApi.askQuestion(question.trim())); }
    catch (cause) { setAskError(cause instanceof Error ? cause.message : "RAG API 無法回答"); }
    finally { setLoading(false); }
  }

  return <div className="ask-page"><div className="ask-intro"><span>✦</span><h2>問問你的冰箱</h2><p>根據你的目前庫存與 USDA FoodKeeper 一般保存指引檢索資料。</p></div><div className="prompt-box"><textarea aria-label="冰箱問題" value={question} onChange={event => setQuestion(event.target.value)} placeholder="例如：我週末要回家，哪些食物需要先處理？" disabled={loading} /><button aria-label="送出問題" onClick={() => void submit()} disabled={loading}>{loading ? "…" : "↑"}</button></div><div className="suggestions">{suggestions.map(text => <button key={text} onClick={() => setQuestion(text)}>{text}</button>)}</div>{!identified && <p className="rag-hint">請先回首頁辨識使用者，RAG 只會讀取該使用者的庫存。</p>}{askError && <p className="rag-hint">{askError}</p>}{answer && <section className="ai-answer"><div className="answer-label"><span>✦</span> FoodKeeper RAG · {answer.status}</div><p>{answer.answer}</p>{answer.sources.map(source => <details key={`${source.source}-${source.text}`}><summary>{source.source}</summary><p>{source.text}</p></details>)}</section>}</div>;
}
function formatTime(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("zh-TW"); }

function EnrollmentForm({ name, setName, error, onSubmit }: { name: string; setName: (value: string) => void; error: string; onSubmit: () => void }) { return <form className="form-step enrollment-form" onSubmit={event => { event.preventDefault(); onSubmit(); }}><span className="step-label">新使用者註冊</span><h2>歡迎加入冰箱管家</h2><p>輸入名稱後站到鏡頭中央。拍攝時請先直視，再緩慢向左、向右轉動一點。</p><CameraPreview guidance="臉在畫面中清楚可見即可，不需要進入綠框。" /><label htmlFor="new-user-name">顯示名稱</label><div className="date-input"><span>♙</span><input id="new-user-name" maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder="例如：小明" /></div><div className="enrollment-guide"><b>拍攝提醒</b><span>光線充足，畫面中只能有一張臉</span><span>不要戴口罩或遮住五官</span><span>過程約需數秒，影像不會儲存</span></div>{error && <p className="rag-hint">{error}</p>}<button className="primary-button full" disabled={!name.trim()}>開始建立人臉資料</button></form>; }
function Recognizing({ text, enrollment = false }: { text: string; enrollment?: boolean }) { return <div className="recognize-step"><span className="step-label">{enrollment ? "本機註冊" : "本機辨識"}</span><h2>{text}</h2><p>{enrollment ? "請直視鏡頭，再緩慢向左、向右轉動一點" : "請看向鏡頭，並把單一物品放入指定區域"}</p><div className="scan-window"><div className="face-art large"></div><div className="scan-line"></div></div><div className="loading-line"><i></i></div><small>請求會等 Python 後端完成真實拍攝，不使用計時器模擬。</small></div>; }
function ActionMenu({ user, onPut, onTake }: { user: IdentifiedUser; onPut: () => void; onTake: () => void }) { return <div className="action-step"><div className="recognized-user"><div className="avatar success">{user.display_name.slice(0, 1)}</div><div><span>辨識完成</span><h2>嗨，{user.display_name}！</h2></div><b>✓</b></div><p>先把單一物品完整放入綠框，再選擇動作。</p><CameraPreview guidance="物品需要完整放入綠框；臉不需要在綠框內。" /><div className="action-options"><button onClick={onPut}><span className="big-action put">↓</span><div><strong>放入物品</strong><small>先掃描，由你檢查名稱後才登記</small></div><b>→</b></button><button onClick={onTake}><span className="big-action take">↑</span><div><strong>取出物品</strong><small>先掃描，由你確認對象後才記錄</small></div><b>→</b></button></div></div>; }

function needsItemChoice(inspection: ItemInspection, selectedItemId: string, addAsNew: boolean) {
  if (inspection.action === "PUT_IN") {
    return !selectedItemId && !addAsNew;
  }
  return !selectedItemId;
}

function ReviewItem({ inspection, label, setLabel, selectedItemId, setSelectedItemId, addAsNew, setAddAsNew, members, shareUserIds, setShareUserIds, expiry, setExpiry, error, onConfirm, onRescan }: {
  inspection: ItemInspection; label: string; setLabel: (value: string) => void;
  selectedItemId: string; setSelectedItemId: (value: string) => void;
  addAsNew: boolean; setAddAsNew: (value: boolean) => void;
  members: Member[]; shareUserIds: string[]; setShareUserIds: (value: string[]) => void;
  expiry: string; setExpiry: (value: string) => void;
  error: string; onConfirm: () => void; onRescan: () => void;
}) {
  const isPut = inspection.action === "PUT_IN";
  const aiChoices = inspection.instance.candidates;
  const primaryChoices = !isPut && inspection.instance.status === "NO_MATCH"
    ? inspection.authorized_inventory : aiChoices;
  const aiChoiceIds = new Set(aiChoices.map(item => item.item_id));
  const manualAlternatives = inspection.authorized_inventory.filter(
    item => !aiChoiceIds.has(item.item_id),
  );
  const canCorrectTake = !isPut
    && ["MATCHED", "AMBIGUOUS"].includes(inspection.instance.status);
  const noAuthorizedTakeChoices = !isPut && inspection.authorized_inventory.length === 0;
  const selectedTake = !isPut
    ? inspection.authorized_inventory.find(item => item.item_id === selectedItemId)
    : undefined;
  const statusText = inspection.localization.status !== "OK"
    ? "沒有找到清楚的物品，請重新掃描。"
    : noAuthorizedTakeChoices
      ? "目前沒有你有權限且仍在冰箱內的物品；這次不會修改庫存。"
    : inspection.instance.status === "MATCHED"
      ? aiChoices.length > 0
        ? "找到一個可能對應的庫存物品，請確認；AI 判斷錯誤時仍可更正。"
        : isPut
          ? "AI 找到的物品目前不可重用，已改為新增物品；你仍可確認名稱。"
          : "AI 找到的物品目前不可取用，請改選你有權限的庫存物品。"
      : inspection.instance.status === "AMBIGUOUS"
        ? aiChoices.length > 0
          ? "多個已存物品看起來很相似，請選擇或更正。"
          : isPut
            ? "沒有可重用的候選，已改為新增物品。"
            : "AI 候選目前不可取用，請改選你有權限的庫存物品。"
        : isPut ? "這看起來是新物品，可以登記。" : "無法自動對應，請從你可取用的庫存中選擇。";
  const disabled = !inspection.committable || (isPut && !label.trim()) || needsItemChoice(inspection, selectedItemId, addAsNew);
  const choiceButtons = (choices: typeof primaryChoices) => <div className="action-options">{choices.map(item => <button className={selectedItemId === item.item_id ? "selected" : ""} key={item.item_id} onClick={() => { setSelectedItemId(item.item_id); setAddAsNew(false); }}><div><strong>{takeChoiceTitle(item)}</strong><small>擁有者：{takeChoiceOwner(item)} · 放入：{item.put_at ? formatTime(item.put_at) : "尚無紀錄"} · {item.shared ? "共用" : "私人"}{typeof item.similarity === "number" ? ` · 相似度 ${item.similarity.toFixed(3)}` : ""}</small></div><b>{selectedItemId === item.item_id ? "✓" : "→"}</b></button>)}</div>;
  const toggleShareUser = (userId: string) => setShareUserIds(
    shareUserIds.includes(userId)
      ? shareUserIds.filter(value => value !== userId)
      : [...shareUserIds, userId],
  );
  return <div className="form-step">
    <span className="step-label">掃描完成 · {isPut ? "放入" : "取出"}</span>
    <h2>確認辨識結果</h2>
    <div className="recognized-user"><div className="avatar success">{inspection.identity.display_name.slice(0, 1)}</div><div><span>已再次確認身分</span><strong>{inspection.identity.display_name}</strong></div><b>✓</b></div>
    {inspection.review_decision === "WARN_NOT_OWNER"
      ? <div className="warning-card"><b>!</b><div><strong>WARN_NOT_OWNER · 這是別人的個人物品</strong><p>無法取出這件物品；若 AI 判斷錯誤，只能改選你有權限的庫存。</p></div></div>
      : <p className="rag-hint">{inspection.review_message ?? statusText}</p>}
    {inspection.category.top3.length > 0 && <><p className="review-label">AI 名稱建議</p><div className="suggestions">{inspection.category.top3.map(entry => <button key={entry.label} onClick={() => isPut && setLabel(entry.label)}>{entry.label} {Math.round(entry.score * 100)}%</button>)}</div></>}
    {inspection.category.status === "UNKNOWN_CATEGORY" && <p className="rag-hint">AI 不確定名稱；這不會阻止放入，請自行修正。</p>}
    {isPut && <><label htmlFor="item-label">物品名稱</label><div className="date-input"><span>✎</span><input id="item-label" value={label} onChange={event => setLabel(event.target.value)} placeholder="請輸入你要保存的名稱" /></div></>}
    {primaryChoices.length > 0 && <><p className="review-label">{!isPut && inspection.instance.status === "NO_MATCH" ? "選擇要取出的授權庫存" : "AI 建議的對應物品"}</p>{choiceButtons(primaryChoices)}</>}
    {canCorrectTake && <details><summary>AI 結果不對—改選其他有權限的物品</summary>{manualAlternatives.length > 0 ? choiceButtons(manualAlternatives) : <p className="rag-hint">目前沒有其他有權限且仍在冰箱內的物品。</p>}</details>}
    {selectedTake && <div className="selected-inventory-confirm"><span>即將取出</span><strong>{takeChoiceTitle(selectedTake)}</strong><small>擁有者：{takeChoiceOwner(selectedTake)}</small></div>}
    {noAuthorizedTakeChoices && <p className="rag-hint">沒有可選物品，確認按鈕已停用；庫存不會被修改。</p>}
    {isPut && <button className={addAsNew ? "chip selected" : "chip"} onClick={() => { setAddAsNew(true); setSelectedItemId(""); }}>＋ 這是不同／新的物品</button>}
    {isPut && <>
      <p className="review-label">要和誰共用？</p>
      <div className="permission-grid">
        <button className={shareUserIds.length === 0 ? "selected" : ""} onClick={() => setShareUserIds([])}><span>♙</span><strong>只有我</strong><small>只有擁有者可取出</small></button>
        {members.map(member => <button className={shareUserIds.includes(member.user_id) ? "selected" : ""} key={member.user_id} onClick={() => toggleShareUser(member.user_id)}><span>{member.display_name.slice(0, 1)}</span><strong>{member.display_name}</strong><small>{shareUserIds.includes(member.user_id) ? "已選擇共用" : "點選即可共用"}</small></button>)}
      </div>
      {members.length === 0 && <p className="rag-hint">目前沒有其他已登錄使用者，物品會保持個人。</p>}
      <label htmlFor="expiry">包裝期限 <em>選填</em></label><div className="date-input"><span>▣</span><input id="expiry" type="date" value={expiry} onChange={event => setExpiry(event.target.value)} /><button onClick={() => setExpiry("")}>不填</button></div>
    </>}
    {error && <p className="rag-hint">{error}</p>}
    <div className="form-footer"><button className="secondary-button" onClick={onRescan}>重新掃描</button><button className="primary-button compact" disabled={disabled} onClick={onConfirm}>確認後更新庫存 →</button></div>
  </div>;
}
function ResultView({ result, onDone }: { result: OperationResult; onDone: () => void }) { const symbol = result.outcome === "ALLOW" ? "✓" : result.outcome === "WARNING" ? "!" : "?"; return <div className="take-step"><span className="step-label">操作結果</span><h2>{result.outcome}</h2><div className="take-visual"><span>{symbol}</span><div className="warning-card"><b>{symbol}</b><div><strong>{result.decision}</strong><p>{result.message}</p></div></div></div><div className="item-detail"><span>使用者 <b>{result.user_id?.slice(0, 8) ?? "未知"}</b></span><span>物品 <b>{result.item_id?.slice(0, 8) ?? "未知"}</b></span><span>信心分數 <b>{result.item_confidence.toFixed(3)}</b></span></div><button className="primary-button full" onClick={onDone}>完成</button></div>; }
function ErrorView({ message, onRetry }: { message: string; onRetry: () => void }) { return <div className="take-step"><span className="step-label">本機 API 錯誤</span><h2>無法完成操作</h2><div className="warning-card"><b>!</b><div><strong>請檢查鏡頭、登入或後端</strong><p>{message}</p></div></div><button className="primary-button full" onClick={() => void onRetry()}>重新辨識</button></div>; }
