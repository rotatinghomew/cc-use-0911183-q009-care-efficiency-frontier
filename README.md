# 养老院区投入产出边界业务服务

当前服务维护院区可比范围、月度资源投入、共享投入分摊、结果指标和完整改进项目报价。分摊比例须守恒；满意程度空值不作零处理。项目不可拆分，报价只是候选资源需求，不代表已经批准。`GET /api/peers?site=east` 返回相同服务范围与照护组的院区；`POST /api/project` 保存项目；`POST /api/project-quote` 接受 projects 数组。服务同时提供效率边界与可达组合分析，给出可借鉴的真实参照院区与可达改进空间，但不产出综合排名。

## 启动与核对

使用 Python 3.11 或更高版本。现有服务采用标准库运行；数值分析所需的固定版本工具列在 requirements.txt，部署准备时运行 `python3 -m pip install -r requirements-build.txt -r requirements.txt`，构建前端和打包后端的固定版本见 requirements-build.txt；依赖准备完毕后，测试、构建和运行均可断网进行。无需真实个人信息、凭据或远程数据源。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q frontier tests
python3 -m build --wheel --no-isolation
python3 -m frontier check
python3 -m frontier demo
python3 -m frontier load --database /tmp/frontier-business.sqlite3 --input examples/operations.json
python3 -m frontier serve --database /tmp/frontier-business.sqlite3
```

服务默认监听本机 8765 端口，`--port` 可指定其他端口。健康检查为 `GET /health`，`GET /api/summary` 返回当前已有业务汇总。未指定数据库时，demo 和 check 使用隔离的临时数据库；serve 若需保留修改须传入数据库路径。已有数据库不重复装载同一类样例。examples/operations.json 是较大的一组历史运营资料，可用 load 命令在事务内载入；按类型和编号更正同名记录，未出现的记录保持不变。所有资料采用固定来源生成且不包含对未来分析结论的预设值。

## 效率边界与可达改进

分析只回答两个问题：可以向哪些**真实院区**借鉴、本期**真实可达**的改进组合是什么；不把满意度、覆盖量和投诉压成一个综合分数，也不做院区排名。

### 口径与可比性

- 比较范围按服务范围与照护组划分（同 `/api/peers` 口径），范围外院区在 `excluded_outside_scope` 中写明不可比原因；范围内但统计期间不一致或满意度全缺测的院区进入 `excluded_within_scope`，不静默混入。
- `months` 取逗号分隔的 ISO 月份（如 `2026-05,2026-06`），窗口内缺月份的院区被排除；未知月份返回 400。
- 共享投入先按守恒比例分摊到院区，再在窗口内汇总，保证各院区投入口径一致。
- JSON 空值表示缺测而非零。满意度全缺测的院区不进入全口径（`full`）；`caliber=reduced` 使用去掉满意度的缩减口径并显式标注，零坏产出与零值不做倒数变换。
- 未完成服务与有效投诉是坏产出，按投入侧指标进入模型（越低越好），不会被当作可按比例放大的产出。

### 效率边界

`GET /api/frontier?site=east&months=2026-05,2026-10` 运行投入导向、可变规模报酬的数据包络分析（scipy HiGHS 求解）。

- 每个院区返回 `efficiency_score`（诊断值，不是排名）、投入侧与产出侧的具体差距（含相对差距），以及 `peers`：`lambda_weight` 为正的真实院区，附其实际指标和可借鉴的服务能力（更低资源、更少坏产出、更高好产出）。
- `statistical_reference` 是参照院区凸组合出的**统计参照点**，明确标注为 `virtual_convex_target`，不是真实存在的混合院区，不代表所有指标能同时无条件达成。
- 位于边界上的院区以自身为参照并给出超效率分；超效率明显偏高的院区在诊断中标记为极端参照。诊断同时报告小样本（样本量不足指标数两倍）、半数以上有效的区分度风险和零差异指标。

### 参照稳定性与更正敏感性

- `GET /api/stability?site=east` 做留一法分析：逐个移出参照院区重算，报告效率分波动、参照集合变化和无法替代的参照，说明小样本下结论是否稳定。
- `POST /api/correction-sensitivity` 接受临时更正差额（如 `{"site":"west","resource":"care_hours","delta":-100}`），比较更正前后的效率分与参照集合；更正只在内存中生效，不写库，负投入更正被拒绝。

### 本期可达组合

`GET /api/attainable?site=east&month=2026-11` 把统计参照与可执行组合严格分开：

- 项目是不可拆分的 0/1 选择，枚举资源池与资质约束下的全部可行组合（超过 2000 组截断并注明），给出固定费用、资源需求、覆盖增量和资源池余量。
- 本期资质不可获得的项目标注原因后排除；共享资源池不足以同时支持所有候选时，`cross_site_conflicts` 列出竞争项目，说明调配给一个院区会减少其他院区的服务。
- 组合只包含真实项目；效率边界的虚拟参照点不会出现在可执行组合中。

### 冻结计划与复盘

`POST /api/plan` 冻结改进计划，同时固化当时的窗口、口径面板（含哈希）、资源池与资质约束、项目组合和统计参照。冻结后即使新增院区或更正资料，计划保留当时的边界与目标，不会随样本增加自动改写。`GET /api/plan-review?id=...` 按冻结面板比较实际覆盖增量与原目标，并校验快照完整性。

对应命令行：`python3 -m frontier frontier --site east --months 2026-05,2026-10`、`python3 -m frontier stability --site east`、`python3 -m frontier attainable --site east --month 2026-11`（`--caliber reduced` 可选）。

## 数据与运行边界

fixtures 中均为本地合成业务资料，员工、家庭和院区编号不对应真实对象。JSON 里的空值表示未记录，不代表零；日期采用 ISO 格式。金额以分保存，工时以分钟保存。每类记录有独立编号，写入同编号用于业务更正，revision 用于并发修改核对。

业务状态由 SQLite 保存，记录批量更正在单一事务内提交。接口使用 UTF-8 JSON，未知资源返回 404，非法数据返回 400，版本冲突返回 409。服务不进行临床诊断，也不自动改变实际服务安排。测试使用临时数据库和系统分配的空闲端口，彼此隔离，不访问公网。运行日志、数据库和依赖缓存不应提交到仓库。
