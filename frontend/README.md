# 冰友 ChillMate Frontend

互動式前端 prototype，對應專案的共享冰箱使用流程。

## 目前介面

- 透過 PN54 本機 station API 辨識使用者
- 顯示由同一個 Python 攝影機 session 提供的即時預覽與物品綠框
- 選擇放入或取出物品，並顯示真實 `ALLOW`、`WARNING` 或 `UNKNOWN`
- 放入時由使用者確認名稱、個人／共用與選填期限
- 每位已辨識使用者都可查看冰箱內所有現存物品；可見不代表可編輯或可取出
- 相同名稱只在畫面上合併成數量卡片，展開後仍顯示每筆獨立的擁有者、期限、放入時間與權限，底層 `item_id` 不合併
- 只有擁有者會看到名稱、期限及全體共用設定的編輯按鈕
- 庫存卡顯示使用者登錄名稱，不暴露 opaque user ID
- 放入時可從其他已登錄使用者中複選共用對象；被指定者會在自己的庫存頁看到並可取用
- 取出確認會把名稱與期限放在一起顯示，並以擁有者與放入時間協助區分外觀相同的多筆物品；未授權物品不會成為可選項
- 阻擋錯拿後，拿取者與物品擁有者都會看到 `WARN_NOT_OWNER` 紀錄
- 從 SQLite interaction events 顯示目前辨識使用者的操作紀錄
- 「問冰箱」使用目前登入者的 SQLite 庫存與 FoodKeeper RAG 來源

核心流程不再使用 mock data。攝影機只由 Python 後端控制；瀏覽器只輪詢
不快取的本機 JPEG，不會另外開啟攝影機。預覽影格不保存也不送往 Manta；
RAG 已透過 FastAPI 回傳真實來源，後端設定 Lemonade
模型後會顯示生成答案；未設定時則明確顯示 `LLM_NOT_CONFIGURED`。

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

警示音由後端讀取本機 `FRIDGE_WARNING_AUDIO_PATH`，再透過需要登入權杖的
端點交給瀏覽器播放；因此 Windows 與 PN54 Linux 不需要相同的系統音訊
播放器。音檔是部署資料，不加入 Git。瀏覽器分頁必須有音訊輸出且不能靜音。

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
