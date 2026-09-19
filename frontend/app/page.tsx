"use client";

import { useEffect, useState } from "react";
import { stationApi } from "@/lib/api";
import type { IdentifiedUser, InventoryItem, OperationResult, QuestionAnswer } from "@/lib/api";

type Tab = "home" | "items" | "history" | "ask";
type Flow = "idle" | "recognizing" | "menu" | "put" | "take" | "result" | "error";

export default function Home() {
  const [tab, setTab] = useState<Tab>("home");
  const [flow, setFlow] = useState<Flow>("idle");
  const [user, setUser] = useState<IdentifiedUser | null>(null);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [online, setOnline] = useState(false);
  const [label, setLabel] = useState("");
  const [shared, setShared] = useState(false);
  const [expiry, setExpiry] = useState("");
  const [result, setResult] = useState<OperationResult | null>(null);
  const [error, setError] = useState("");
  const [today, setToday] = useState("今天");

  useEffect(() => {
    stationApi.health().then(() => setOnline(true)).catch(() => setOnline(false));
    setToday(new Intl.DateTimeFormat("zh-TW", { month: "long", day: "numeric", weekday: "long" }).format(new Date()));
  }, []);

  function showError(cause: unknown) {
    setError(cause instanceof Error ? cause.message : "本機 API 發生未知錯誤");
    setFlow("error");
  }

  async function refreshInventory() {
    if (!user) return;
    try { setInventory(await stationApi.inventory()); } catch (cause) { showError(cause); }
  }

  async function beginRecognition() {
    setError("");
    setLabel("");
    setFlow("recognizing");
    try {
      const identified = await stationApi.identify();
      setUser(identified);
      setInventory(await stationApi.inventory());
      setFlow("menu");
    } catch (cause) { showError(cause); }
  }

  async function saveItem() {
    if (!label.trim()) {
      setError("請先確認並輸入物品名稱；HSV 模型不會產生食物名稱。");
      return;
    }
    setError("");
    setFlow("recognizing");
    try {
      const operation = await stationApi.operate({ action: "PUT_IN", label: label.trim(), shared, expires_on: expiry || null });
      setResult(operation);
      setInventory(await stationApi.inventory());
      setFlow("result");
    } catch (cause) { showError(cause); }
  }

  async function takeOut() {
    setError("");
    setFlow("take");
    try {
      const operation = await stationApi.operate({ action: "TAKE_OUT" });
      setResult(operation);
      setInventory(await stationApi.inventory());
      setFlow("result");
    } catch (cause) { showError(cause); }
  }

  async function selectTab(next: Tab) {
    setTab(next);
    if (next === "items" && user) await refreshInventory();
  }

  function closeFlow() {
    setFlow("idle");
    setError("");
    setResult(null);
  }

  return <main className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">F</span><div><strong>Fridge Guardian</strong><small>共享冰箱管家</small></div></div>
      <nav aria-label="主要導覽">
        <NavButton active={tab === "home"} label="首頁" icon="⌂" onClick={() => void selectTab("home")} />
        <NavButton active={tab === "items"} label="冰箱物品" icon="▦" count={String(inventory.length)} onClick={() => void selectTab("items")} />
        <NavButton active={tab === "history"} label="使用紀錄" icon="↻" onClick={() => void selectTab("history")} />
        <NavButton active={tab === "ask"} label="問冰箱" icon="✦" onClick={() => void selectTab("ask")} />
      </nav>
      <div className="sidebar-bottom"><div className="privacy"><span>●</span><div><strong>本機模式</strong><small>影像不會上傳雲端</small></div></div><div className="profile"><div className="avatar">{user?.display_name.slice(0, 1) ?? "?"}</div><div><strong>{user?.display_name ?? "尚未辨識"}</strong><small>{user ? "本機工作階段" : "請從首頁開始"}</small></div></div></div>
    </aside>
    <section className="content">
      <header className="topbar"><div><span className="eyebrow">{today}</span><h1>{tabTitle(tab, user)}</h1></div><div className="top-actions"><span className="status-dot">● {online ? "PN54 API 已連線" : "PN54 API 未連線"}</span></div></header>
      {tab === "home" && <HomeView inventory={inventory} online={online} onStart={beginRecognition} onTab={selectTab} />}
      {tab === "items" && <ItemsView items={inventory} identified={Boolean(user)} />}
      {tab === "history" && <UnavailableView title="使用紀錄尚未連線" detail="本次整合只連接辨識、PUT_IN／TAKE_OUT 與目前庫存；此頁不顯示模擬紀錄。" />}
      {tab === "ask" && <AskView identified={Boolean(user)} />}
    </section>
    {flow !== "idle" && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="冰箱操作"><div className="flow-card"><button className="close" onClick={closeFlow} aria-label="關閉">×</button>
      {flow === "recognizing" && <Recognizing text={label ? "正在辨識物品並登記" : "正在辨識使用者"} />}
      {flow === "menu" && user && <ActionMenu user={user} onPut={() => { setLabel(""); setShared(false); setExpiry(""); setFlow("put"); }} onTake={() => void takeOut()} />}
      {flow === "put" && <PutForm label={label} setLabel={setLabel} shared={shared} setShared={setShared} expiry={expiry} setExpiry={setExpiry} error={error} onSave={() => void saveItem()} />}
      {flow === "take" && <Recognizing text="正在辨識物品並記錄取出" />}
      {flow === "result" && result && <ResultView result={result} onDone={closeFlow} />}
      {flow === "error" && <ErrorView message={error} onRetry={beginRecognition} />}
    </div></div>}
  </main>;
}

function NavButton({ active, label, icon, count, onClick }: { active: boolean; label: string; icon: string; count?: string; onClick: () => void }) { return <button className={active ? "nav-item active" : "nav-item"} onClick={onClick}><span>{icon}</span>{label}{count && <b>{count}</b>}</button>; }
function tabTitle(tab: Tab, user: IdentifiedUser | null) { return { home: user ? `你好，${user.display_name}` : "Fridge Guardian", items: "冰箱裡有什麼？", history: "使用紀錄", ask: "問問你的冰箱" }[tab]; }

function HomeView({ inventory, online, onStart, onTab }: { inventory: InventoryItem[]; online: boolean; onStart: () => void; onTab: (tab: Tab) => Promise<void> }) {
  const expiring = inventory.filter(item => item.expires_on).length;
  const shared = inventory.filter(item => Boolean(item.shared)).length;
  return <div className="page-grid"><section className="hero-card"><div className="hero-copy"><span className="pill">PN54 本機工作站</span><h2>要放東西，<br />還是拿東西？</h2><p>站到鏡頭前，後端會先確認是誰。<br />攝影機只由本機 Python API 控制。</p><button className="primary-button" onClick={onStart} disabled={!online}><span className="scan-icon">◎</span>{online ? "開始人臉辨識" : "等待本機 API"}<b>→</b></button><small className="safe-note">▣ 臉部資料與 token 只保存在這台裝置</small></div><div className="camera-visual"><div className="camera-ring"><div className="face-art"></div><div className="corner tl"></div><div className="corner tr"></div><div className="corner bl"></div><div className="corner br"></div></div><p><span></span> {online ? "API 已就緒" : "API 離線"}</p></div></section><section className="summary-row"><Stat icon="▦" label="目前庫存" value={inventory.length} onClick={() => void onTab("items")} /><Stat icon="!" label="有期限紀錄" value={expiring} onClick={() => void onTab("items")} /><Stat icon="♙" label="共用物品" value={shared} onClick={() => void onTab("items")} /></section><section className="lower-grid"><div className="panel"><div className="panel-head"><div><h3>本機庫存</h3><p>顯示目前辨識使用者可見的真實資料</p></div><button onClick={() => void onTab("items")}>查看全部</button></div>{inventory.length ? inventory.slice(0, 2).map(item => <FoodRow key={item.item_id} item={item} />) : <p className="rag-hint">辨識使用者後，這裡會載入 SQLite 庫存。</p>}</div><div className="panel ask-teaser"><span className="spark">✦</span><h3>問問你的冰箱</h3><p>使用真實庫存與 FoodKeeper 一般保存指引檢索相關資料。</p><button onClick={() => void onTab("ask")}>開始提問 <b>→</b></button><small>RAG 已連線 · LLM 待接</small></div></section></div>;
}

function Stat({ icon, label, value, onClick }: { icon: string; label: string; value: number; onClick: () => void }) { return <div className="stat-card"><span className="stat-icon mint">{icon}</span><div><small>{label}</small><strong>{value} <em>件</em></strong></div><button onClick={onClick}>查看 →</button></div>; }
function ItemsView({ items, identified }: { items: InventoryItem[]; identified: boolean }) { return <div className="page-stack"><div className="filter-row"><button className="chip selected">目前庫存 {items.length}</button></div>{!identified ? <UnavailableView title="請先辨識使用者" detail="庫存 API 需要本機記憶體 token；請回首頁開始辨識。" /> : items.length === 0 ? <UnavailableView title="目前沒有物品" detail="PUT_IN 成功後，SQLite 中的目前庫存會顯示在這裡。" /> : <div className="inventory-grid">{items.map(item => <article className="food-card" key={item.item_id}><div className="food-emoji">▣</div><div className="expiry-badge fresh">{item.expires_on ? `期限 ${item.expires_on}` : "未填期限"}</div><h3>{item.label}</h3><p>{item.shared ? "共用" : "個人"} · owner {item.owner_id.slice(0, 8)}</p><div className="card-meta"><span>放入時間</span><strong>{formatTime(item.put_at)}</strong></div></article>)}</div>}</div>; }
function UnavailableView({ title, detail }: { title: string; detail: string }) { return <section className="panel"><div className="panel-head"><div><h3>{title}</h3><p>{detail}</p></div></div></section>; }
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
function FoodRow({ item }: { item: InventoryItem }) { return <div className="food-row"><div className="mini-food">▣</div><div><strong>{item.label}</strong><small>{item.shared ? "共用" : "個人"} · {formatTime(item.put_at)}</small></div><span className="expiry fresh">{item.expires_on ?? "無期限"}</span></div>; }
function formatTime(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("zh-TW"); }

function Recognizing({ text }: { text: string }) { return <div className="recognize-step"><span className="step-label">本機辨識</span><h2>{text}</h2><p>請看向鏡頭，並把單一物品放入指定區域</p><div className="scan-window"><div className="face-art large"></div><div className="scan-line"></div></div><div className="loading-line"><i></i></div><small>請求會等 Python 後端完成真實辨識，不使用計時器模擬。</small></div>; }
function ActionMenu({ user, onPut, onTake }: { user: IdentifiedUser; onPut: () => void; onTake: () => void }) { return <div className="action-step"><div className="recognized-user"><div className="avatar success">{user.display_name.slice(0, 1)}</div><div><span>辨識完成</span><h2>嗨，{user.display_name}！</h2></div><b>✓</b></div><p>你現在想做什麼？</p><div className="action-options"><button onClick={onPut}><span className="big-action put">↓</span><div><strong>放入物品</strong><small>先由你輸入名稱，再拍攝登記</small></div><b>→</b></button><button onClick={onTake}><span className="big-action take">↑</span><div><strong>取出物品</strong><small>一次完成辨識、權限判斷與紀錄</small></div><b>→</b></button></div></div>; }
function PutForm({ label, setLabel, shared, setShared, expiry, setExpiry, error, onSave }: { label: string; setLabel: (value: string) => void; shared: boolean; setShared: (value: boolean) => void; expiry: string; setExpiry: (value: string) => void; error: string; onSave: () => void }) { return <div className="form-step"><span className="step-label">放入物品</span><h2>確認這件物品</h2><label htmlFor="item-label">物品名稱</label><div className="date-input"><span>✎</span><input id="item-label" value={label} onChange={event => setLabel(event.target.value)} placeholder="例如：鮮奶（使用者確認）" /></div><p className="rag-hint">HSV instance matcher 只辨識同一件物品，不會產生食物名稱。</p><label>誰可以取用？</label><div className="permission-grid"><button className={!shared ? "selected" : ""} onClick={() => setShared(false)}><span>♙</span><strong>個人</strong><small>只有擁有者可取出</small></button><button className={shared ? "selected" : ""} onClick={() => setShared(true)}><span>♧</span><strong>共用</strong><small>已辨識成員可取出</small></button></div><label htmlFor="expiry">包裝期限 <em>選填</em></label><div className="date-input"><span>▣</span><input id="expiry" type="date" value={expiry} onChange={event => setExpiry(event.target.value)} /><button onClick={() => setExpiry("")}>不填</button></div>{error && <p className="rag-hint">{error}</p>}<div className="form-footer"><span>確認後才會呼叫相機與寫入 SQLite</span><button className="primary-button compact" onClick={onSave}>確認放入 →</button></div></div>; }
function ResultView({ result, onDone }: { result: OperationResult; onDone: () => void }) { const symbol = result.outcome === "ALLOW" ? "✓" : result.outcome === "WARNING" ? "!" : "?"; return <div className="take-step"><span className="step-label">操作結果</span><h2>{result.outcome}</h2><div className="take-visual"><span>{symbol}</span><div className="warning-card"><b>{symbol}</b><div><strong>{result.decision}</strong><p>{result.message}</p></div></div></div><div className="item-detail"><span>使用者 <b>{result.user_id?.slice(0, 8) ?? "未知"}</b></span><span>物品 <b>{result.item_id?.slice(0, 8) ?? "未知"}</b></span><span>信心分數 <b>{result.item_confidence.toFixed(3)}</b></span></div><button className="primary-button full" onClick={onDone}>完成</button></div>; }
function ErrorView({ message, onRetry }: { message: string; onRetry: () => void }) { return <div className="take-step"><span className="step-label">本機 API 錯誤</span><h2>無法完成操作</h2><div className="warning-card"><b>!</b><div><strong>請檢查鏡頭、登入或後端</strong><p>{message}</p></div></div><button className="primary-button full" onClick={() => void onRetry()}>重新辨識</button></div>; }
