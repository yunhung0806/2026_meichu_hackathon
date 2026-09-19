"use client";

import { useEffect, useState, type FormEvent } from "react";
import { stationApi } from "@/lib/api";
import type { RecipeIngredient, RecipeRecommendations } from "@/lib/api";

function dateDescription(item: RecipeIngredient) {
  if (item.date_basis === "UNKNOWN") return "未填期限，使用前請自行確認狀態";
  if (item.date_basis === "FOODKEEPER") return `一般保存參考起日 ${item.reference_date}（非包裝效期）`;
  if ((item.days_left ?? 0) < 0) return `包裝期限 ${item.reference_date} · 已過期`;
  return `包裝期限 ${item.reference_date} · ${item.days_left === 0 ? "今天到期" : `剩 ${item.days_left} 天`}`;
}

export default function RecipeView() {
  const [question, setQuestion] = useState("請用快到期的食材推薦料理");
  const [request, setRequest] = useState({ question, version: 0 });
  const [data, setData] = useState<RecipeRecommendations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    stationApi.recipes(request.question).then(result => {
      if (active) setData(result);
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "無法取得料理建議");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [request]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || loading) return;
    setLoading(true);
    setError("");
    setData(null);
    setRequest({ question: question.trim(), version: request.version + 1 });
  }

  return <div className="page-stack recipe-view">
    <section className="panel recipe-hero">
      <span className="recipe-icon">♨</span><div><h2>今天可以煮什麼？</h2><p>優先使用快到期食材，依目前庫存推薦料理。</p></div>
      <form className="recipe-question" onSubmit={submit}>
        <label htmlFor="recipe-question">想吃什麼？</label>
        <input id="recipe-question" value={question} maxLength={2000} onChange={event => setQuestion(event.target.value)} placeholder="例如：想吃炒蛋，有什麼建議？" />
        <button className="primary-button compact" disabled={loading || !question.trim()}>{loading ? "正在找食譜…" : "推薦料理"}</button>
      </form>
      {loading && <p role="status">正在確認庫存、保存期限與食譜…</p>}
      {error && <p className="rag-hint" role="alert">{error}。若登入逾時，請回首頁重新辨識。</p>}
    </section>
    {data && <>
      <section className="panel">
        <div className="panel-head"><div><h3>優先使用食材</h3><p>{data.today} · 包裝期限或保存參考 {data.soon_days} 天內優先</p></div></div>
        {data.ingredients.length === 0 ? <p>目前沒有可供推薦的食材。</p> : data.ingredients.map(item => <div className="food-row" key={item.item_id}><div className={`mini-food ${item.priority ? "urgent-food" : ""}`}>{item.priority ? "!" : "▣"}</div><div><strong>{item.label}{item.priority ? " · 優先使用" : ""}</strong><small>{dateDescription(item)}</small></div></div>)}
        {data.excluded.length > 0 && <div className="recipe-excluded"><h4>未納入推薦</h4>{data.excluded.map(item => <p key={item.item_id}>{item.label}：{item.status === "EXPIRED" ? "已超過包裝期限" : "已超過一般保存參考"}</p>)}</div>}
        <p className="rag-hint">FoodKeeper 日期是一般保存參考，不是包裝效期或安全保證；烹調前請確認食材實際狀態。</p>
      </section>
      {data.answer && <section className="panel ai-answer"><div className="answer-label"><span>✦</span> Lemonade 食譜建議 · {data.status}</div><p>{data.answer}</p></section>}
      <section className="panel"><div className="panel-head"><div><h3>推薦食譜</h3><p>優先使用快到期食材，再依缺少主食材數量排序。</p></div></div>{data.recipes.length === 0 && <p>尚未找到符合目前庫存的食譜。</p>}<div className="recipe-grid">{data.recipes.map(recipe => <article className="recipe-card" key={recipe.source}><span className="pill">{recipe.missing.length ? `需補 ${recipe.missing.length} 種主食材` : "主食材已齊"}</span><h3>{recipe.title}</h3>{recipe.use_first.length > 0 && <p className="recipe-priority">優先用掉：{recipe.use_first.join("、")}</p>}<p><b>現有：</b>{recipe.matched.join("、")}</p><p><b>缺少：</b>{recipe.missing.join("、") || "無"}</p><p><b>另確認：</b>{recipe.pantry.join("、")}</p><ol>{recipe.steps.map((step, index) => <li key={index}>{step}</li>)}</ol><details><summary>食譜來源</summary><p>{recipe.source_title} · {recipe.provenance}</p><code>{recipe.source}</code></details></article>)}</div></section>
    </>}
  </div>;
}
