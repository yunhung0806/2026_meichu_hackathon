"use client";

import { useEffect, useState } from "react";
import { stationApi } from "@/lib/api";
import type { RecipeIngredient, RecipeRecommendations } from "@/lib/api";

function dateDescription(item: RecipeIngredient) {
  if (item.date_basis === "UNKNOWN") return "未填期限，使用前請確認狀態";
  if (item.date_basis === "FOODKEEPER") return `保存參考起日 ${item.reference_date}（非包裝效期）`;
  return `包裝期限 ${item.reference_date} · ${item.days_left === 0 ? "今天到期" : (item.days_left ?? 0) < 0 ? "已過期" : `剩 ${item.days_left} 天`}`;
}

export default function RecipeView() {
  const [question, setQuestion] = useState("現在可以煮什麼？");
  const [request, setRequest] = useState({ question: "現在可以煮什麼？", version: 0 });
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

  return <div className="page-stack recipe-view">
    <section className="panel">
      <h2>現在可以煮什麼？</h2>
      <p>先用快到期的食材，看看今天能做哪些料理。</p>
      <form className="recipe-question" onSubmit={event => {
        event.preventDefault();
        if (!question.trim() || loading) return;
        setLoading(true); setError(""); setData(null);
        setRequest({ question: question.trim(), version: request.version + 1 });
      }}>
        <label htmlFor="recipe-question">想吃什麼料理？</label>
        <input id="recipe-question" value={question} maxLength={2000} onChange={event => setQuestion(event.target.value)} placeholder="例如：想吃炒蛋，有什麼建議？" />
        <button className="primary-button compact" disabled={loading || !question.trim()}>{loading ? "正在找食譜…" : "推薦料理"}</button>
      </form>
      {loading && <p role="status">正在確認庫存、保存期限與食譜…</p>}
      {error && <p role="alert">{error}。可再次推薦；若登入逾時，請回首頁重新辨識。</p>}
    </section>
    {data && <>
      <section className="panel">
        <h3>先看看要優先使用哪些食材</h3>
        <p>你的庫存 · {data.today} · 包裝期限 {data.soon_days} 天內優先</p>
        {data.ingredients.length === 0 ? <p>目前沒有可供推薦的食材。</p> : data.ingredients.map(item => <div className="food-row" key={item.item_id}>
          <div className="mini-food">{item.priority ? "!" : "▣"}</div>
          <div><strong>{item.label}{item.priority ? " · 優先使用" : ""}</strong><small>{dateDescription(item)}</small></div>
        </div>)}
        {data.excluded.length > 0 && <div className="recipe-excluded"><h4>未納入推薦</h4>{data.excluded.map(item => <p key={item.item_id}>{item.label}：{item.status === "EXPIRED" ? "已超過包裝期限" : "已超過保存參考區間"}</p>)}</div>}
        <p className="rag-hint">保存參考依放入日期推算，不能代表實際效期。庫存未記錄份量；烹調前請確認食材狀態及用量。</p>
      </section>
      {data.answer && <section className="panel"><h3>{data.status === "OK" ? "料理建議" : "推薦狀態"}</h3><p className="recipe-answer">{data.answer}</p></section>}
      <section className="panel"><h3>用這些食材做料理</h3><p>優先使用快到期食材，再依缺少材料的數量排序。</p>{data.status === "RETRIEVAL_ONLY" && <p>目前顯示食譜庫配對結果。</p>}
        {data.recipes.length === 0 && <p>尚未找到符合的食譜。</p>}
        <div className="recipe-grid">{data.recipes.map(recipe => <article className="recipe-card" key={recipe.source}>
          <span className="pill">{recipe.missing.length ? `需補 ${recipe.missing.length} 種主食材` : "主食材已齊"}</span>
          <h3>{recipe.title}</h3>
          {recipe.use_first.length > 0 && <p className="recipe-priority">優先用掉：{recipe.use_first.join("、")}</p>}
          <p>現有食材：{recipe.matched.join("、")}</p>
          <p>缺少主食材：{recipe.missing.join("、") || "無"}</p>
          <p>另確認調味料／用水：{recipe.pantry.join("、")}</p>
          <ol>{recipe.steps.map((step, index) => <li key={index}>{step}</li>)}</ol>
          <details><summary>食譜來源：{recipe.source_title}</summary><p>{recipe.provenance}</p><code>{recipe.source}</code></details>
        </article>)}</div>
      </section>
    </>}
  </div>;
}
