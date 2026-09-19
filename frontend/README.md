# Fridge Guardian Frontend

互動式前端 prototype，對應專案的共享冰箱使用流程。

## 目前介面

- 透過 PN54 本機 station API 辨識使用者
- 選擇放入或取出物品，並顯示真實 `ALLOW`、`WARNING` 或 `UNKNOWN`
- 放入時由使用者確認名稱、個人／共用與選填期限
- 從 SQLite inventory API 顯示目前辨識使用者的庫存
- 「問冰箱」使用目前登入者的 SQLite 庫存與 FoodKeeper RAG 來源

核心流程不再使用 mock data 或計時器。攝影機只由 Python 後端控制；
歷史與 Local LLM 尚未串接。RAG 已透過 FastAPI 回傳真實來源；在
Lemonade 接上前，回答狀態會明確顯示 `LLM_NOT_CONFIGURED`，不會冒充
生成式答案。

## 開發

需要 Node.js 22.13 或更新版本。

```bash
cd frontend
npm install
npm run dev
```

API base URL 預設為 `http://127.0.0.1:8000`。如需調整，在本機未提交的
`.env.local` 設定：

```text
NEXT_PUBLIC_FRIDGE_API_BASE_URL=http://127.0.0.1:8000
```

啟動前端前，先從 repository root 啟動本機 API：

```bash
uv run fridge-guardian-api
cd frontend
npm run dev
```

正式編譯檢查：

```bash
npm run build
```

主要介面位於 `app/page.tsx`，樣式位於 `app/globals.css`。
