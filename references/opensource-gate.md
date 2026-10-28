# Open-Source Gate — 开源门槛

**硬门槛**：没有公开可用代码的论文，一律不放行。这是用户明确规则，只有用户能对单篇豁免。

---

## §1 查什么（按成本从低到高）

1. **arXiv abs 页 `Comments` 字段** — 代码链接绝大多数在这里（`Code: https://…`）
2. **abs 页无结论 → HTML 全文** — 抓 `arxiv.org/abs/<id>` 的 HTML 版或 `/html/<id>`，从**原始 HTML** 提链接
3. **仍无结论 → 项目页** — 论文里给的项目主页，页上必须能找到代码出口

### 链接提取要领

- **从原始 HTML 提链接，不要先剥标签**。URL 在 `href="…"` 属性里；先 `re.sub(r'<[^>]+>')` 去标签会把链接变成文字 `this https URL`，导致误判成无开源。
- 正确顺序：raw HTML → 提 href → 再去标签做展示。
- 过滤 arXiv 页脚 chrome 要**大小写不敏感**（真实链接是 `github.com/arXiv/html_feedback`，小写模式会漏）。

---

## §2 什么算开源

**从宽认定**，不要求一定是 GitHub：

- GitHub / GitLab 仓库
- HuggingFace model / dataset / Space
- 官方项目页，页上有可下载的代码或权重
- arXiv 页面自带的 code 链接
- 任何等价的公开代码/权重发布

**不算**：
- 承诺「录用后开源」「code upon acceptance」
- 只引用别人的仓库
- 只使用第三方工具（vLLM / TRL / Docling / crawlee…）—— 上下文里必须是「我们的代码」才作数
- fork 上游、无本论文自有实现的仓库

### 所有权判定

链接周边文本需含自有信号：`our code` / `available at` / `Code —` / `project page` / `reproduce`。
拿不准 → 读上下文再定，**宁可严勿滥**。

---

## §3 判空（关键）

链接真实 ≠ 仓库有内容。以下任一 → 视同**无开源**：

1. GitHub API `size == 0`
2. 文件树里除 `README*` / `LICENSE*` / `.gitignore` / `.gitattributes` 外，无任何代码、数据、配置或文档正文
3. README 实质为空：≲100 字符，或只有一行标题（典型如 `# PQA`）

### curl 方法

```bash
# 仓库元数据：size 单位 KB
curl -sSL https://api.github.com/repos/<user>/<repo>

# 完整文件树
curl -sSL "https://api.github.com/repos/<user>/<repo>/git/trees/HEAD?recursive=1"

# 读 README（分支不确定先试 main，404 再试 master）
curl -sSL https://raw.githubusercontent.com/<user>/<repo>/main/README.md

# 兜底：抓 HTML 首页，含 "This repository is empty" 即空仓
curl -sSL https://github.com/<user>/<repo>
```

- 未认证限额 **60 次/小时/IP**。只对过门候选使用；先查 size，必要时再查 tree。
- `HEAD` 不可用 → 换默认分支名（`main` / `master`）。

---

## §4 常见坑

| 现象 | 真相 |
|---|---|
| raw 返回 404 | 不一定是空仓。先换分支（main→master）、换路径，再判 |
| API 403 / 429 | 触发限额。改走 HTML 首页兜底，或报告注明「判空受限」 |
| API 404 | 仓库真不存在（`{"message":"Not Found"}`），不是限流 |
| 项目页只有介绍 | 页上必须找到代码出口（GitHub / HF / zip 下载）才算通过 |
| 链接在 bib 里 | 引用不等于自有，不算 |

---

## §5 报告

淘汰时记录原因到 `seen.csv` 的 `reason` 列，取值 `no_code`。
若判定受限（如 GitHub API 限额），在日报里如实说明，不要静默当作无开源。
