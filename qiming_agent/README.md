# 企明｜照明产业链经营与财报 Agent

**面向中小企业经营分析、采购支持人员的个人改造原型。**

把已经存在的财报清洗脚本升级成“能选任务、读报表、查依据、出简报、留记录”的业务工作台。不是通用聊天机器人，不替代财务、授信、采购审批，也没有声称某家企业或高校已经上线。

## 一句话理解

旧脚本负责“把表整理出来”；企明继续用程序处理数字，再加上任务流程、知识检索、版本与权限检查，让使用者知道**算了什么、依据在哪里、哪些结论还需人工确认**。

## 立即运行

建议 Python 3.11+。本次实际测试环境见 `docs/validation.json`。

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m qiming serve --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。默认只监听本机。界面提供三个公开的本地演示身份：`local-analyst`、`local-editor`、`local-reviewer`。它们只为演示权限与第二人复核，不是企业身份认证。不要直接部署到公网。

命令行也能运行：

```bash
python -m qiming ask "公牛2024年现金流分析"
python -m qiming check-data
python -m pytest -q
python evaluate.py
```

## 现有数据与适用边界

| 公司 | 报告年度 | 数值年度 | 产业链位置 |
|---|---|---|---|
| 公牛集团 603195 | 2024 | 2024、2023 | 终端电工与照明 |
| 民爆光电 301362 | 2024 | 2024、2023 | 商用、工业照明灯具 |
| 兆驰股份 002429 | 2024 | 2024、2023 | 显示与 LED 产业链 |
| 晶丰明源 688368 | 2023 | 2023、2022 | 电源管理芯片 |

数据来自用户提供的四份公开年报副本，每份选取三大**合并**报表的 21 项指标、两年数据，共 **168 条**。原始报告路径、SHA256、PDF 页数保存在 `qiming/data/sources.json`，每条数字有报告编号和物理页码。PDF 原件不随公共代码重新分发。

这些是历史演示数据，不代表当前最新披露。四家公司不是同质可比公司，不输出综合排名、行业分位或采购结论。跨公司对照必须指定同一年度；晶丰明源没有 2024 年已核对数据，不能用 2023 年冒充。

## 已实现什么

- **财务工具**：单位/文本数字规范化、缺失和重复检查、三表勾稽、比率与同比、带来源的简报和 CSV 导出。计算由 Decimal 程序完成。
- **6 个受限 Skill**：三表核对、利润与现金流、应收与存货、产业链样本对照、经营简报、知识依据问答。Skill 实际改变工具集合、检索词和 top-k；不是任意代码执行。
- **知识与版本**：SQLite 事实库；部门/租户过滤；待审核、有效和归档状态；独立第二人发布；版本切换在同库事务内执行。
- **证据检索**：默认 BM25 中文双字切分；显式配置嵌入服务后可走语义向量 + BM25 + RRF。失败会标明回退，不伪装成语义检索成功。
- **可选生成**：兼容式模型接口生成带引用的定性草稿；数字留在程序面板；无证据不调用生成。结构检查不等于语义正确，草稿仍需人工复核。
- **反馈记录**：保存用户自己的 Trace；错误反馈进入待办，复核后只能接受进入开发或驳回，不自动修改线上知识/Skill。

## 工作流

```text
明确公司、年度、任务
  → Intent / 任务识别
  → Rewrite / 加载 Skill 检索词
  → 白名单财务工具（查数、核对、计算）
  → 权限与有效版本过滤 → 检索证据
  → 程序简报 + 可选模型定性草稿
  → 来源/内容校验 → 人工确认
  → Trace 与反馈待办
```

这是一个**受限工具工作流型 Agent 原型**。当前路由是规则式，未实现自主 ReAct 规划、真实 MCP Server、重排模型、长期记忆、自动灰度发布或 Kubernetes 部署。

## 不是从零编造的项目背景

沿用用户旧项目的方法，再做配置化、校验和界面改造：

1. `simple-data-cleaning-about-three-financial-statement`：目录遍历、按项目名定位、跨年汇总。新版不再固定 2018–2023，不盲取首个重复项目。
2. `information-extraction-from-an-enterprise-annual-report`：先定位原生 PDF 财务章节，再提取。新版限制合并报表、保留页码，并把新提取结果放入待核验候选。
3. `Intern-Python4WealthProduct`：数字清洗、可比范围、指标计算、模板报表和图表展示思路。没有移植理财产品数据来伪装企业财报。
4. 用户提供的文枢参考源码：固定流程、权威来源、版本与权限、Skill 和 Trace 设计。当前是单机轻量化改造，未复制其完整微服务或未经复现的性能数字。

详见 [来源与改造审查](docs/SOURCE_REVIEW.md)。

## 新资料接入

已授权并确认来源的原生 PDF：

```bash
python -m qiming ingest report.pdf --company 603195 --name 公牛集团 --year 2024 --out candidates.json
```

输出仍是 `pending_review`。原生 PDF 自动提取只对当前测试版式验证，不是任意财报通用解析器；扫描件不会暗中 OCR。人工需检查合并/母公司、当期/上期、调整前/后、单位、页码。**候选尚不能在界面一键发布到财务账本**；正式更新需人工复核 CSV 与来源清单，通过代码评审后更新并重建本地工作区。

旧 Excel 可另存为 UTF-8 CSV 后接入：

```bash
python -m qiming normalize-csv report.csv --company 603195 --years 2024 2023 --statement is --unit 元 --out normalized.json
```

公开下载辅助仅接受明确的 HTTPS PDF 地址及白名单域名，不自动搜索，不绕过登录、不跟随重定向、不覆盖旧文件：

```bash
python -m qiming download "https://static.cninfo.com.cn/...pdf" --out downloads/report.pdf
```

上述占位地址须替换为真实且有权访问的官方文件地址。本次未实测外网抓取成功；下载保护和内容检查以模拟响应测试。

## 接模型（可选，默认不外发）

`.env.example` 仅为配置说明，程序**不会自动读取 .env**。需在运行进程设置环境变量：

```powershell
$env:QIMING_ALLOW_EXTERNAL="1"
$env:QIMING_MODEL_BASE_URL="https://你的服务地址/v1"
$env:QIMING_MODEL_API_KEY="你的密钥"
$env:QIMING_CHAT_MODEL="你的模型名称"
$env:QIMING_EMBEDDING_MODEL="你的向量模型名称"
python -m qiming serve
```

macOS/Linux 用 `export 变量名="值"`。仅填写你已获授权的模型服务，先确认文档可外发；密钥不能放入简历或 GitHub。需要完全离线时保持 `QIMING_ALLOW_EXTERNAL=0`。本次没有真实 LLM/Embedding 调用，相关接口测试使用 MockTransport。

## 验证与限制

- 73 个自动测试通过；12 个固定业务任务回归通过。
- 168 条财务数值核对；8 个公司年度组合的 24 项勾稽检查通过。
- UI 已核对概览、财务任务、反馈、知识页；浏览器环境禁止直连本机，测试以桥接 fetch 到本机 API 完成，不宣称公网浏览器完整部署验收。
- 未测真实用户收益、云模型质量、并发容量；没有沿用参考文章的 1,086 文档、1,200 题及准确率等数字。

运行与结构见 [产品/架构手册](docs/PRODUCT_MANUAL.md)，面试及术语见 [面试手册](docs/INTERVIEW_MANUAL.md)。
