# Venue 兜底渠道 — 会议和期刊

**触发条件**：arXiv 渠道交付不足 `max_papers`。按 `venues.yaml` 的顺序扫描，会议和期刊混排；不依据类型或 CCF 等级额外加权。用户提供的 venue 和别名是放行依据。

## 流程

1. 从配置和期刊首次在线发表日期窗口、会议年份构造待扫描项。会议缺少年份时先询问用户，期刊按 `window_days` 筛选首次在线发表日期；日期缺失、冲突或不能核验时标记待核验，不当作窗口内已证实。
2. 读取 `data/venue_state.json`；按 venue、年份、卷或来源、配置版本、名单版本检查状态。未扫、部分、失败、中断继续处理；完成状态仅适用于相同配置和名单快照，来源可能更新时必须重新核验。不得仅凭 90 天内扫过就跳过更新检查。
3. 优先获取官方 proceedings、会议录用名单或期刊官网/可信索引。对期刊只接受已正式发表或官方在线发表；仅投稿、模糊接收声明不能冒充发表。记录 DOI、来源、发表状态和首次在线发表时间并核验。没有 arXiv ID 的期刊论文也可用 DOI、标题和官方元数据评估，不要因无法回查 arXiv 自动丢弃。
4. 多卷 venue 要发现并扫描相关卷（如 main、long、short、findings）；不能找到第一个 200 就停。发现来源不足以证明完整时记录 partial；获取失败记录 failed，不能称已扫完。ACL Anthology 辅助工具见下文。
5. 标题及可得摘要按关键词和排除词预筛，逐篇按共同的综述/benchmark、开源和 venue 门处理，用 arXiv ID / DOI / 标题去重。部分列表或博客不能单独作为官方录用或发表证据；逐篇回到官方来源核验。
6. 按相关度降序、venue 分数降序、时间由新到旧排序并补足。交付已满、预算用尽、扫描失败或确实穷尽时停止；明确说明原因和剩余范围。

## 断点和完整性

使用 `python3 scripts/venue_state.py --state data/venue_state.json --help` 和 `get/set/list --help`。每个状态键包含 `venue`、`year`、`volume`（期刊可用稳定的来源/期号标识）、`config-version`、`roster-version`。调用方需对相关配置及实际名单内容计算稳定内容摘要作为版本；未知的来源或不完整名单不得伪装成完整快照。`partial`、`interrupted`、`failed` 和 `complete` 区分记录；只有能证明该快照全部消化后才设 `complete`。

先持久化已处理的候选/台账，再更新可恢复游标；每处理一个批次即落盘，不要只在运行结束时保存。中断后读取相同版本的游标继续；配置或名单变化时创建新键并复查。写入必须串行，当前工具不提供并发写锁。此工具是显式检查点 API/CLI，不会自动扫描，agent 必须调用它并检查返回值。

## 期刊候选工具

```bash
python3 scripts/journal_search.py --journal 'Nature' --start 2026-10-01 --end 2026-10-29 --max-pages 10
```

此工具从 Crossref 检索候选并以期刊全称规范化全等匹配；别名须分别查询或核验。`search_report` 的 complete 仅指 API 返回结果已耗尽，不证明 Crossref 全覆盖；达到预算或请求失败须报告。缺失或不精确的在线日期须人工核验，所有候选均须核验期刊官方发表状态后才进入共同门槛。该工具不自动写扫描状态；agent 须先保存结果，再把 `search_report.next_cursor` 持久化到检查点，下次用相同参数和 `--cursor` 续扫；游标过期时从头重扫并去重。若发现范围不完整则不得标记 `complete`。

## ACL Anthology

将事件索引或卷页 HTML 保存到本地后执行：

```bash
python3 scripts/acl_anthology.py discover --html /tmp/event.html --year 2025 --family naacl
python3 scripts/acl_anthology.py papers --html /tmp/volume.html --volume 2025.naacl-long
```

索引里发现的卷仅代表索引中实际存在的链接，不保证全量；须核实来源覆盖，必要时补充其他官方索引。提取标题支持无引号属性，链接文字可能不是规范标题；核验论文页面后才能交付。事件页可能很大，网络请求应限制超时并报告失败。NAACL 2025 的 `naacl-long` 不是 `naacl-main`，不要假定固定卷后缀。

## 台账和报告

每次报告：已扫/总 venue、逐 venue/年份/卷的 complete、partial、failed、unscanned 状态及原因；各来源名单质量；名单数、预筛数、去重数、门槛幸存数、交付数；未扫范围和截断/预算耗尽情况。拿不到完整名单或无法核验时如实说明，**禁止假装扫完**。
