# 觀察清單(Family) LINE 推播系統

透過 GitHub Actions 定時執行，從 Google Sheets 讀取觀察清單，計算自 2026/2/26 起漲幅排行，以 LINE Flex Message 卡片推播給最多 4 位使用者。

---

## 系統流程

```
GitHub Actions（每週一～五）
    ↓
Google Sheets 讀取觀察清單
    ↓
永豐 Shioaji / TWSE / TPEX 取得股價
    ↓
① Flex Message 漲幅排行卡片  →  推播給所有人
② 個股詳細資訊（純文字）    →  推播給所有人
```

---

## 推播內容

| 訊息 | 格式 | 內容 |
|------|------|------|
| ① 漲幅排行榜 | LINE Flex Message 深綠卡片 | 自 2/26 起漲幅排名、百分比、股票名稱 |
| ② 個股詳細資訊 | 純文字 | 現價、日漲跌、月營收、EPS、最新新聞 |

---

## Google Sheets 格式

**SPREADSHEET_ID**：從 GitHub Secret `SPREADSHEET_ID` 讀取

**Sheet 名稱**：`觀察清單`

| 欄位名稱 | 範例值 | 說明 |
|----------|--------|------|
| `代號` | 2330 | 股票代號（空白列自動略過） |
| `名稱` | 台積電 | 股票名稱 |
| `交易所` | TSE | 上市填 TSE，上櫃填 OTC |
| `2026/2/26收盤價` | 1095.00 | 計算漲跌幅的基準價格 |

> Service Account 需共用：`stock-tracker-bot@stock-tracker-496215.iam.gserviceaccount.com`
> Google Sheets → 共用 → 貼上 Email → 設定「編輯者」

---

## GitHub Secrets 設定

| Secret 名稱 | 說明 | 必/選填 |
|-------------|------|---------|
| `LINE_ACCESS_TOKEN` | LINE Messaging API Channel Access Token | 必填 |
| `LINE_USER_ID_1` | 第 1 位接收者 LINE User ID（你自己） | 必填 |
| `LINE_USER_ID_2` | 第 2 位接收者 LINE User ID | 選填 |
| `LINE_USER_ID_3` | 第 3 位接收者 LINE User ID | 選填 |
| `LINE_USER_ID_4` | 第 4 位接收者 LINE User ID | 選填 |
| `SINOPAC_API_KEY` | 永豐金 Shioaji API Key | 必填 |
| `SINOPAC_SECRET_KEY` | 永豐金 Shioaji Secret Key | 必填 |
| `GOOGLE_CREDENTIALS` | Service Account JSON 憑證（整個 JSON 貼上） | 必填 |
| `SPREADSHEET_ID` | Google Sheets ID | 必填 |
| `FINMIND_TOKEN` | FinMind API Token（可提高 API 呼叫上限） | 選填 |

> `LINE_USER_ID_2` ～ `4` 留空不設定，系統自動跳過，不會報錯。
> 之後追加第 5 人：新增 `LINE_USER_ID_5` Secret，並在程式 `LINE_USER_IDS` 清單加一行。

---

## 推播排程

```yaml
cron: '0 6 * * 1-5'   # UTC 06:00 = 台灣時間 14:00，週一至週五
```

> 若要調整時間，修改 `news.yml` 的 cron 設定即可（GitHub Actions 使用 UTC 時區）。

---

## 本地執行

```bash
# 安裝套件
pip install -r requirements.txt

# 設定環境變數後執行
export LINE_ACCESS_TOKEN=xxx
export LINE_USER_ID_1=xxx
export SINOPAC_API_KEY=xxx
export SINOPAC_SECRET_KEY=xxx
export GOOGLE_CREDENTIALS='{ ... }'
export SPREADSHEET_ID=xxx

python news_tracker.py
```

---

## 版本記錄

| 版本 | 日期 | 說明 |
|------|------|------|
| v1.0 | 2026/05/25 | 初版，漲幅排行改為 Flex Message 深綠卡片，支援 4 人推播 |
