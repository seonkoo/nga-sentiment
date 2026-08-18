# NGA 股市情绪指数 · 手机看板

自动抓取 NGA **[股市]技术分析 板块**当天新帖标题做情绪分类统计，并分析 **狼楼（楼主·啊狼）** 当日发言风向，输出可在手机上直接看的 GitHub Pages 看板。

## 看板内容

- **板块情绪指数**：今日新发帖 / 总回复 / 提高因素(兴奋·晒收益·看多) / 压低因素(崩溃清仓·担忧看空·愤怒) / 情绪净分 / 指数分 / 情绪等级 + 分类占比环形图。
- **狼楼风向**：当日发言看多/看空/中性分布 + 全楼热词 + **楼主「啊狼」发言特别高亮**（今日发言条数、方向、原文摘录、楼主热词）。

## 数据来源与更新

- 数据源：`https://ngabbs.com/thread.php?stid=47206901`（板块）、`https://ngabbs.com/read.php?tid=47288722`（狼楼）。
- 更新：`analyze.py` 登录 NGA（Selenium + Edge + 验证码 OCR 自动破解）→ 抓取分析 → 同时写出 `data/nga_data.json` 与 `data/nga_data.js`。
- 自动更新：GitHub Actions 每日 **北京时间 08:00 / 12:00 / 18:00 / 22:00**（UTC 0/4/10/14）运行，结果提交回仓库，GitHub Pages 即时展示。
- 手机查看：打开 Pages 地址即可，想看时就是最新快照。

## 本地运行

```bash
pip install -r requirements.txt
cp config.example.json config.json   # 填入你的 NGA 账号密码
python analyze.py                    # 用 cookie；失效自动 OCR 登录
# 或：python analyze.py --login      # 强制重新登录
```

> 本机有 Microsoft Edge 即可；Linux/CI 会自动装 Edge 并由 Selenium Manager 拉驱动。

## 配置

可调项都在 `config.json` / `config.example.json`（关键词规则、指数公式、抓取页数、URL 等），**改配置不用动代码**。情绪分类是关键词启发式统计，**非投资建议**。

## 安全

- `config.json`（含账号密码）和 `data/nga_cookies.json`（登录态）已被 `.gitignore` 忽略，**不会提交**。
- CI 通过 GitHub Secrets `NGA_USER` / `NGA_PASS` 注入账号，仓库内只有脱敏的 `config.example.json`。
- 情绪指数/风向为启发式统计，仅供研究参考，不构成任何投资建议。

## 文件

| 文件 | 说明 |
|---|---|
| `index.html` | 手机看板（纯前端读 `data/nga_data.js`，零外部依赖；支持 `file://` 双击即用与 GH Pages） |
| `analyze.py` | 抓取+分析主脚本 |
| `config.example.json` | 脱敏配置模板 |
| `data/nga_data.json` | 当日数据（结构化备份，便于调试/二次消费） |
| `data/nga_data.js` | 当日数据 JS 形态（`window.NGA_DATA = {...}`，HTML 通过 `<script src>` 加载） |
| `.github/workflows/daily.yml` | 定时更新工作流 |
