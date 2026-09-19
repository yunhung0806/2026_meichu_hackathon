"use client";

import { useState } from "react";

type Tab = "home" | "items" | "history" | "ask";
type Flow = "idle" | "recognizing" | "menu" | "put" | "take";

const inventory = [
  { emoji: "🥬", name: "青江菜", owner: "小明", date: "今天 08:42", expiry: "明天", tone: "urgent", place: "蔬果室" },
  { emoji: "🥛", name: "鮮奶", owner: "小明", date: "9/17 19:20", expiry: "剩 2 天", tone: "soon", place: "上層" },
  { emoji: "🍎", name: "富士蘋果", owner: "小芸", date: "9/16 21:05", expiry: "約 5 天", tone: "fresh", place: "蔬果室" },
  { emoji: "🥚", name: "雞蛋", owner: "共用", date: "9/14 10:30", expiry: "剩 8 天", tone: "fresh", place: "門架" },
];

const history = [
  { time: "今天 08:42", person: "小明", action: "放入", item: "青江菜", type: "個人", color: "green" },
  { time: "昨天 20:14", person: "小芸", action: "取出", item: "優格", type: "共用", color: "orange" },
  { time: "9/17 19:20", person: "小明", action: "放入", item: "鮮奶", type: "個人", color: "green" },
  { time: "9/16 21:05", person: "小芸", action: "放入", item: "富士蘋果", type: "個人", color: "green" },
  { time: "9/14 10:30", person: "小明", action: "放入", item: "雞蛋", type: "共用", color: "green" },
];

export default function Home() {
  const [tab, setTab] = useState<Tab>("home");
  const [flow, setFlow] = useState<Flow>("idle");
  const [permission, setPermission] = useState("personal");
  const [expiry, setExpiry] = useState("");
  const [question, setQuestion] = useState("我週末要回家，哪些食物需要先處理？");
  const [answer, setAnswer] = useState(false);
  const [saved, setSaved] = useState(false);
  const today = "9月19日 星期六";

  function beginRecognition() { setFlow("recognizing"); window.setTimeout(() => setFlow("menu"), 1100); }
  function saveItem() { setSaved(true); window.setTimeout(() => { setSaved(false); setFlow("idle"); setTab("home"); }, 1500); }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">F</span><div><strong>Fridge Guardian</strong><small>共享冰箱管家</small></div></div>
        <nav aria-label="主要導覽">
          <NavButton active={tab === "home"} label="首頁" icon="⌂" onClick={() => setTab("home")} />
          <NavButton active={tab === "items"} label="冰箱物品" icon="▦" count="4" onClick={() => setTab("items")} />
          <NavButton active={tab === "history"} label="使用紀錄" icon="↻" onClick={() => setTab("history")} />
          <NavButton active={tab === "ask"} label="問冰箱" icon="✦" onClick={() => setTab("ask")} />
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy"><span>●</span><div><strong>本機模式</strong><small>影像不會上傳雲端</small></div></div>
          <div className="profile"><div className="avatar">明</div><div><strong>小明的家</strong><small>4 位成員</small></div><button aria-label="更多選項">•••</button></div>
        </div>
      </aside>
      <section className="content">
        <header className="topbar"><div><span className="eyebrow">{today}</span><h1>{tabTitle(tab)}</h1></div><div className="top-actions"><span className="status-dot">● 系統正常</span><button className="icon-button" aria-label="通知">♢<i>2</i></button></div></header>
        {tab === "home" && <HomeView onStart={beginRecognition} onTab={setTab} />}
        {tab === "items" && <ItemsView />}
        {tab === "history" && <HistoryView />}
        {tab === "ask" && <AskView question={question} setQuestion={setQuestion} answer={answer} onAsk={() => setAnswer(true)} />}
      </section>
      {flow !== "idle" && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="冰箱操作"><div className="flow-card"><button className="close" onClick={() => setFlow("idle")} aria-label="關閉">×</button>{flow === "recognizing" && <Recognizing />}{flow === "menu" && <ActionMenu onChoose={setFlow} />}{flow === "put" && <PutForm permission={permission} setPermission={setPermission} expiry={expiry} setExpiry={setExpiry} onSave={saveItem} saved={saved} />}{flow === "take" && <TakeFlow />}</div></div>}
    </main>
  );
}

function NavButton({ active, label, icon, count, onClick }: { active: boolean; label: string; icon: string; count?: string; onClick: () => void }) { return <button className={active ? "nav-item active" : "nav-item"} onClick={onClick}><span>{icon}</span>{label}{count && <b>{count}</b>}</button>; }
function tabTitle(tab: Tab) { return { home: "早安，小明", items: "冰箱裡有什麼？", history: "使用紀錄", ask: "問問你的冰箱" }[tab]; }

function HomeView({ onStart, onTab }: { onStart: () => void; onTab: (tab: Tab) => void }) {
  return <div className="page-grid"><section className="hero-card"><div className="hero-copy"><span className="pill">智慧共享冰箱</span><h2>要放東西，<br />還是拿東西？</h2><p>站到鏡頭前，讓我先確認是誰。<br />辨識完成後就能開始操作。</p><button className="primary-button" onClick={onStart}><span className="scan-icon">◎</span>開始人臉辨識 <b>→</b></button><small className="safe-note">▣ 臉部資料只儲存在這台裝置</small></div><div className="camera-visual"><div className="camera-ring"><div className="face-art"></div><div className="corner tl"></div><div className="corner tr"></div><div className="corner bl"></div><div className="corner br"></div></div><p><span></span> 攝影機已就緒</p></div></section><section className="summary-row"><div className="stat-card"><span className="stat-icon mint">▦</span><div><small>冰箱內物品</small><strong>4 <em>件</em></strong></div><button onClick={() => onTab("items")}>查看 →</button></div><div className="stat-card"><span className="stat-icon amber">!</span><div><small>即將到期</small><strong>2 <em>件</em></strong></div><button onClick={() => onTab("items")}>處理 →</button></div><div className="stat-card"><span className="stat-icon lilac">♙</span><div><small>共用物品</small><strong>1 <em>件</em></strong></div><button onClick={() => onTab("items")}>查看 →</button></div></section><section className="lower-grid"><div className="panel"><div className="panel-head"><div><h3>需要注意</h3><p>優先處理快到期的食物</p></div><button onClick={() => onTab("items")}>查看全部</button></div>{inventory.slice(0,2).map(item => <FoodRow key={item.name} item={item} />)}</div><div className="panel ask-teaser"><span className="spark">✦</span><h3>不知道先吃什麼？</h3><p>問問冰箱，讓本機 AI 根據期限和庫存幫你安排。</p><button onClick={() => onTab("ask")}>「週末前要先吃什麼？」 <b>→</b></button><small>RAG + Local LLM · 資料不離開裝置</small></div></section></div>;
}

function ItemsView() { return <div className="page-stack"><div className="filter-row"><div className="search">⌕ <input aria-label="搜尋物品" placeholder="搜尋食物或使用者…" /></div><button className="chip selected">全部 4</button><button className="chip">我的 2</button><button className="chip">共用 1</button><button className="chip">快到期 2</button></div><div className="inventory-grid">{inventory.map(item => <article className="food-card" key={item.name}><div className="food-emoji">{item.emoji}</div><div className={`expiry-badge ${item.tone}`}>{item.expiry}</div><h3>{item.name}</h3><p>{item.place} · {item.owner}</p><div className="card-meta"><span>放入時間</span><strong>{item.date}</strong></div></article>)}</div></div>; }
function HistoryView() { return <div className="page-stack"><div className="history-tools"><div className="chip selected">全部紀錄</div><div className="chip">放入</div><div className="chip">取出</div><button className="date-button">本月⌄</button></div><section className="panel history-panel">{history.map((h, i) => <div className="history-row" key={i}><span className={`event-icon ${h.color}`}>{h.action === "放入" ? "↓" : "↑"}</span><div className="history-main"><strong>{h.person} <em>{h.action}</em>了「{h.item}」</strong><small>{h.type}物品</small></div><time>{h.time}</time></div>)}</section></div>; }
function AskView({ question, setQuestion, answer, onAsk }: { question: string; setQuestion: (v:string)=>void; answer:boolean; onAsk:()=>void }) { return <div className="ask-page"><div className="ask-intro"><span>✦</span><h2>今天想問冰箱什麼？</h2><p>我會參考你的庫存、保存期限與食材知識，在這台裝置上回答。</p></div><div className="prompt-box"><textarea value={question} onChange={e=>setQuestion(e.target.value)} aria-label="輸入問題" /><button onClick={onAsk} aria-label="送出問題">↑</button></div><div className="suggestions"><button onClick={()=>setQuestion("哪些食物快過期了？")}>哪些食物快過期了？</button><button onClick={()=>setQuestion("今晚可以煮什麼？")}>今晚可以煮什麼？</button><button onClick={()=>setQuestion("哪些是大家都能吃的？")}>哪些是共用的？</button></div>{answer && <div className="ai-answer"><div className="answer-label"><span>✦</span> 冰箱管家</div><p>週末前建議先處理 <strong>青江菜</strong>，預估明天到期；接著是 <strong>鮮奶</strong>，還有約 2 天。今晚可以把青江菜和雞蛋做成清炒青菜與蛋料理，鮮奶則可作為早餐搭配。</p><div className="source-row"><span>依據 4 件庫存</span><span>期限紀錄</span><span>食材保存知識庫</span></div></div>}</div>; }
function FoodRow({ item }: { item: typeof inventory[number] }) { return <div className="food-row"><div className="mini-food">{item.emoji}</div><div><strong>{item.name}</strong><small>{item.owner} · {item.place}</small></div><span className={`expiry ${item.tone}`}>{item.expiry}</span></div>; }
function Recognizing() { return <div className="recognize-step"><span className="step-label">步驟 1 / 2</span><h2>正在辨識使用者</h2><p>請看向鏡頭，保持臉部清楚可見</p><div className="scan-window"><div className="face-art large"></div><div className="scan-line"></div></div><div className="loading-line"><i></i></div><small>所有辨識都在本機完成</small></div>; }
function ActionMenu({ onChoose }: { onChoose:(flow:Flow)=>void }) { return <div className="action-step"><div className="recognized-user"><div className="avatar success">明</div><div><span>辨識完成</span><h2>嗨，小明！</h2></div><b>✓</b></div><p>你現在想做什麼？</p><div className="action-options"><button onClick={()=>onChoose("put")}><span className="big-action put">↓</span><div><strong>放入物品</strong><small>登記食物、期限與共享方式</small></div><b>→</b></button><button onClick={()=>onChoose("take")}><span className="big-action take">↑</span><div><strong>取出物品</strong><small>辨識物品並檢查效期</small></div><b>→</b></button></div></div>; }
function PutForm({permission,setPermission,expiry,setExpiry,onSave,saved}:{permission:string;setPermission:(v:string)=>void;expiry:string;setExpiry:(v:string)=>void;onSave:()=>void;saved:boolean}) { return <div className="form-step"><span className="step-label">放入物品</span><h2>登記這件食物</h2><div className="detected-item"><span>🥬</span><div><small>AI 辨識結果</small><strong>青江菜</strong></div><button>修改</button></div><label>誰可以取用？</label><div className="permission-grid"><button className={permission==="personal"?"selected":""} onClick={()=>setPermission("personal")}><span>♙</span><strong>個人</strong><small>只有你可以取用</small></button><button className={permission==="shared"?"selected":""} onClick={()=>setPermission("shared")}><span>♧</span><strong>共用</strong><small>家中成員皆可取用</small></button></div><label htmlFor="expiry">包裝期限 <em>選填</em></label><div className="date-input"><span>▣</span><input id="expiry" type="date" value={expiry} onChange={e=>setExpiry(e.target.value)} /><button onClick={()=>setExpiry("")}>無明定期限</button></div>{!expiry && <p className="rag-hint">✦ 未填期限時，系統會參考食材知識庫估算並標示為「建議期限」。</p>}<div className="form-footer"><span>放入時間會自動記錄為現在</span><button className="primary-button compact" onClick={onSave}>{saved?"✓ 已登記":"確認放入 →"}</button></div></div>; }
function TakeFlow() { return <div className="take-step"><span className="step-label">取出物品</span><h2>偵測到青江菜</h2><div className="take-visual"><span>🥬</span><div className="warning-card"><b>!</b><div><strong>建議盡快食用</strong><p>這件物品預估明天到期，已冷藏約 1 天。</p></div></div></div><div className="item-detail"><span>擁有者 <b>小明</b></span><span>權限 <b>個人</b></span><span>放入 <b>今天 08:42</b></span></div><button className="primary-button full" onClick={()=>window.alert("已記錄取出時間")}>確認取出 →</button></div>; }
