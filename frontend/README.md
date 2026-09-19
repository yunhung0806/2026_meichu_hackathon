# Fridge Guardian Frontend

互動式前端 prototype，對應專案的共享冰箱使用流程。

## 目前介面

- 人臉辨識後顯示使用者名稱
- 選擇放入或取出物品
- 放入時選擇個人／共用、記錄時間與選填期限
- 無明定期限時顯示 RAG 保存期限提示
- 取出時提示即將或已經超過期限的物品
- 冰箱物品、個人物品與歷史紀錄查詢
- RAG + Local LLM 問答介面

目前使用 mock data，尚未串接攝影機、FastAPI、RAG 或 Local LLM。

## 開發

需要 Node.js 22.13 或更新版本。

```bash
cd frontend
npm install
npm run dev
```

正式編譯檢查：

```bash
npm run build
```

主要介面位於 `app/page.tsx`，樣式位於 `app/globals.css`。
