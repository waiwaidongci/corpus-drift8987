# gb-0012 · 百万级分批上传与流式比较测试

本目录仅含测试脚本与说明，不含源码改动。所有脚本使用独立临时目录数据，
以 `python3 -P` 运行（不把当前目录加入 `sys.path`，防止同目录 `json.py` 等被误导入），
后端源码经 `PYTHONPATH=/workspace/backend` 引用，依赖装在用户 site。

## 运行方式

```sh
export JWT_SECRET=t
export PYTHONPATH=/workspace/backend:/home/node/.local/lib/python3.11/site-packages
cd /workspace/test-artifacts/gb-0012
python3 -P test_equivalence.py        # 流式 vs 内存管线全字段口径一致性
python3 -P test_uploads.py            # 分批上传服务规则
python3 -P test_http.py               # FastAPI HTTP 端到端（内存 SQLite）
python3 -P test_scale.py 1000000      # 百万行规模、计数守恒、产物完整性
# 独立进程度量比较阶段 RSS（需先由 test_scale 生成的 SQLite，参数：db 路径、输出目录、SQLite cache KB）
python3 -P run_standalone_compare.py <scale.db> <out_dir> 2000
```

## 覆盖点与结论

### 1. 统计口径零漂移（test_equivalence.py）
同一数据分别走原内存管线 `compare_snapshots` 与新流式管线
`compare_snapshots_streaming`，断言：
- SHA-256 指纹逐字符相同（流式哈希器与 `services/fingerprint.py` 字节一致，
  含 `sort_keys`、`ensure_ascii=True`、紧凑分隔符，另用随机含 Unicode 数据单独验证）；
- 每个特征的冻结箱（分位内边界/类别字典）、两边桶计数、平滑比例、PSI/JS、
  KS 统计量与原 p、BH q 与 FDR 拒绝、状态标记完全一致（浮点按 1e-12 相对容差比较）；
- 切片（language=zh）+ 部分特征 + 自定义 bins/平滑/α 场景一致；
- 参考常量等失败态两条管线抛同样的错误；
- artifact JSON 的 `reference_ids/candidate_ids/findings/test_family` 与内存版相同。

### 2. 分批上传规则（test_uploads.py / test_http.py）
- 多批上传→收齐→发布→分批搬运到 `utterances`，发布后才进入可用快照列表；
- 跨批重复 `reference`：第二批触发 `(upload_id,reference)` 唯一约束，**整份拒绝**，
  快照置 `rejected`、不进入列表、不可再追加、不可发布；
- 批内重复（422）、重复批次号（409）、未传完发布（409）均被拒绝；
- 两个并发会话使用相同 `reference` 字符串互不污染（唯一性按 upload 维度），
  非创建者追加/发布返回 403；
- `open/rejected` 快照不参与比较（`runs.create_run` 409/422 拦截）；
- 原 20–5000 条小快照直传路径、字段校验（类型错误/保留桶名）保持可用；
- viewer 写操作 403，所有登录用户可读上传进度。

### 3. 百万级规模与内存（test_scale.py）
1,000,000 行/快照（两边共 200 万行）：
- 流式比较约 27–37 秒完成，产出 5 个特征的完整发现；
- 每特征桶计数之和严格等于切片总量（守恒）；
- artifact 含全部 100 万 + 100 万切片 id，结果 JSON 流式写出；
- `tracemalloc` 实测**整条管线 Python 对象峰值约 126MB、结束驻留 0.2MB**；
  单次扫描峰值约 26MB。

## 内存口径的诚实说明

本机无 Docker/MySQL，规模测试用 SQLite。SQLite 是**进程内**数据库，其页缓存
计入被测进程 RSS，因此 `run_standalone_compare.py` 报告的 ~260MB RSS 含 SQLite C
层缓存，这部分在真实架构（MySQL 独立服务 + PyMySQL `SSDictCursor` 服务器端游标，
由 SQLAlchemy `stream_results=True` 启用，客户端只缓冲 `yield_per` 批量）中不存在。

可归因到本算法的内存（tracemalloc，不含 C 层缓存）：
- 连续值分块 np.float64：每特征每边 8MB/百万行，逐特征取用后即释放；
- 类别仅留去重计数；缺失/总量为标量；
- 切片 id 分块 np.int64：每边 8MB；
- 指纹行落盘外部归并（`RowSpool`），归并时逐行读入，不驻留全量 dict；
- 最大瞬时项是 SciPy `ks_2samp` 内部排序/拷贝（约 70–80MB 短暂峰值），
  为保持 KS 精确口径未替换实现。

生产侧护栏：worker/backend 设 `MALLOC_ARENA_MAX=2`，阶段边界 `malloc_trim(0)`
归还 arena，compose 设 `mem_limit: 384m` 作为容器护栏（数据面按 256MB 预算设计，
余量留给解释器与 SciPy 瞬时峰值）。

## 已知差异 / 未覆盖

- 未在真实 MySQL+Celery 容器内做负载验证（环境无 Docker）；SSCursor 的定界行为
  依据 PyMySQL/SQLAlchemy 契约，已在代码中通过 `stream_results=True` 启用。
- 浏览器端百万行 JSON 切批受限于页面内存；UI 默认每批 5000 行，超大文件建议
  由脚本按批调用 `/uploads/{id}/batches`。
- 被拒绝快照的名称不可直接复用（同名快照行已存在），需换新名称重建上传会话。
