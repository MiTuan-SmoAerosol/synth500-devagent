# synth500：500 道开发者智能体题（轻量版）

本仓库收录了 500 道「读题面 → 改代码 → 跑测试验收」的题目，格式与 Kaggle「Gemma 4 Developer Agent」比赛官方提供的参考题相同。

- **来源**：fastapi、rich、httpx、requests 四个开源仓库在 GitHub 上已合并的 PR。
- **验证**：每道题都在断网环境里验证过：只打测试补丁跑 2 次，必须都失败；再打上参考答案补丁跑 3 次，必须都通过。
- **为什么是轻量版**：完整数据带 500 份仓库快照，共 36 GB，放不进 GitHub。这里改为附带 4 个 git bundle（共约 129 MB）和一个重建脚本，在你本机就能重建出全部快照，不需要联网。重建结果和原始快照的代码内容、提交历史完全一致（见「核对方式」）。

## 快速开始

需要 Linux 或 macOS，git ≥ 2.32，Python ≥ 3.8（只用标准库）；全部重建约需 40 GB 磁盘空间。

```bash
# 1. clone 本仓库（约 140 MB，4 个 bundle 已在 bundles/ 里），并校验 bundle
git clone https://github.com/MiTuan-SmoAerosol/synth500-devagent.git synth500 && cd synth500
shasum -a 256 -c bundles.sha256          # Linux 上可以用 sha256sum -c
# 2. 重建 500 份快照到 snapshots/
python3 rebuild_snapshots.py --jobs 4
```

- **耗时**：4 并发时全部重建约 30–40 分钟，大头是 fastapi：166 道，每道 20 秒到 2 分钟；rich 153 道约 3–6 分钟；httpx 和 requests 加起来约 1 分钟。机器核多时可以加大 `--jobs`，每个任务临时占用不到 1 GB 磁盘。
- **只重建一部分**：用 `--repo rich` 只建某个仓库的题，或用 `--only fastapi_100 rich_1849` 只建指定的几道题。
- **中断与重跑**：中断后重跑即可，已经建好的快照会跳过；加 `--force` 则全部重建。
- **报告**：每道题的结果写在 `rebuild_report.jsonl` 里；有任何一道没通过核对时，脚本以非 0 状态退出。

## 文件说明

| 文件 | 说明 |
|---|---|
| `tasks.jsonl` | 500 道题，每行一道。8 个字段，顺序与官方相同（见下表） |
| `task-index.jsonl` | 题目与快照的对应关系：instance_id、repo、base_commit、snapshot（`snapshots/<instance_id>.tgz`） |
| `validation.jsonl` | 每道题的验证记录，官方没有这个文件，字段见下文 |
| `snapshot_manifest.jsonl` | 每份快照的 HEAD 提交号、文件树哈希和提交数，重建脚本用它逐题核对 |
| `rebuild_snapshots.py` | 从 bundle 重建快照的脚本 |
| `bundles.sha256` | 4 个 bundle 的 SHA-256 校验值 |
| `bundles/{fastapi,rich,httpx,requests}.bundle` | 4 个仓库的 git 历史，只包含这 500 道题需要的部分，每道题的基底提交对应一个引用 `refs/tasks/<instance_id>` |

`tasks.jsonl` 的字段：

| 字段 | 含义 |
|---|---|
| `instance_id` | `<仓库短名>_<PR 号>`，例如 `fastapi_100` 对应 fastapi 仓库的 PR #100 |
| `repo` | GitHub 仓库名，例如 `fastapi/fastapi` |
| `base_commit` | PR 的基底提交，即修复前的代码版本 |
| `patch` | 参考答案：PR 对非测试 `.py` 文件的改动 |
| `test_patch` | PR 对测试文件的改动（`tests/` 下或文件名为 `test_*.py`），用于验收 |
| `problem_statement` | PR 标题 + 两个换行 + PR 正文原文 |
| `hints_text` | 为空 |
| `created_at` | PR 的合并时间（与官方题相同，用的是合并时间而不是创建时间） |

补丁的 diff 风格与官方相同：没有 `diff --git` 头；新建文件写成 `--- a/x` 加 `@@ -0,0 +1,N`；整份补丁末尾没有换行。只保留了 `.py` 文件。

## 一道题怎么用

1. 解压 `snapshots/<instance_id>.tgz`，得到修复前的完整 git 仓库：
   - 历史只到 `base_commit` 为止，分支为 `main` 和 `export_ref`，不带虚拟环境；
   - 和官方一样，导出时提交号被重写过，所以快照里的 HEAD 不等于 `base_commit`，但代码与 `base_commit` 完全一致。
2. 把 `problem_statement` 交给智能体，让它在快照里改代码。
3. 验收：
   - 先把测试文件恢复原样，再打上 `test_patch`；
   - 对 `test_patch` 涉及的测试文件运行 pytest；
   - 判定通过的条件：退出码为 0，没有 failure 或 error，并且 `test_patch` 中新增或修改的测试函数全部通过、没有被跳过。

我们验证时用的命令（Python 3.12，断网）：

```
python3 -s -m pytest <test_patch 中的测试文件> -p no:anyio -o timeout=0 -o python_classes='Test* *Test' -q
```

依赖要自己装：按仓库自带的 requirements 文件、poetry.lock 或 uv.lock 安装即可。`validation.jsonl` 的 `env_strategy` 字段记录了每题用的是哪一种。

## 验证方法与结果

- **环境**：Python 3.12，依赖来自 pypi.org。
  - 有锁文件的按锁定版本安装；
  - 没有锁文件的用 requirements 文件安装，并按 PR 合并日期给版本设上限；
  - 测试工具在 3.12 上设了最低版本：pytest≥7.4、pytest-timeout≥2.2 等。
- **断网**：测试在只有回环网卡的网络命名空间里运行，每次运行都确认过外网不通。
- **流程**：在快照上先只打 `test_patch` 跑 2 次，必须都失败；再打上 `patch` 和 `test_patch` 跑 3 次。
- **判定**：
  - **严格**（照搬官方判定）：补丁后 3 次都满足上面的通过条件，并且至少 1 个测试从失败变为通过。**454 道满足。**
  - **放宽**（参照 SWE-bench）：补丁前后都失败的测试不计，这类测试多半是环境原因跑不了，比如需要真实网络、测试证书已过期；其余条件不变。**500 道全部满足**，其中 46 道只在放宽标准下通过。

只想要严格标准通过的 454 道时：

```bash
python3 -c "
import json
ok = {json.loads(l)['instance_id'] for l in open('validation.jsonl') if json.loads(l)['verdict_strict'] == 'valid'}
print(len(ok))
open('tasks.strict.jsonl', 'w').writelines(l for l in open('tasks.jsonl') if json.loads(l)['instance_id'] in ok)
"
```

`validation.jsonl` 的主要字段：

| 字段 | 含义 |
|---|---|
| `verdict_strict` / `verdict_relaxed` | 两种判定的结果，取值为 `valid` 或 `invalid` |
| `FAIL_TO_PASS` / `PASS_TO_PASS` | 从失败变通过的测试 / 前后都通过的测试 |
| `required_tests` | 从 `test_patch` 提取的必须通过的测试 |
| `env_broken_tests` | 放宽标准下不计的测试，即补丁前后都失败的那些 |
| `runs` | 5 次运行（before1–2、after1–3）各自的退出码和测试统计 |
| `source_pr`、`merged_at`、`pr_created_at`、`head_sha` | 源 PR 的链接和相关时间、提交 |
| `env_strategy`、`src_lines`、`test_lines` | 依赖来源、源码和测试的改动行数 |

## 分布

| | 题数 |
|---|---|
| fastapi / rich / httpx / requests | 166 / 153 / 176 / 5 |
| 源码改动 ≤10 行 / 11–50 / 51–200 / 201–500 行 | 193 / 170 / 105 / 32 |
| 合并年份：2012 / 2019 / 2020 / 2021 / 2022 | 3 / 73 / 137 / 63 / 94 |
| 合并年份：2023 / 2024 / 2025 / 2026 | 40 / 28 / 15 / 47 |
| 题型（按关键词粗分）：修 bug / 新功能 / 重构或弃用 / 其他 | 300 / 116 / 18 / 66 |

## 已知偏差与注意事项

1. **年代偏早**：2019–2022 年的题占 73%，官方参考题集中在 2023–2026 年。需要的话可以按 `created_at` 筛选。
2. **仓库配比与官方不同**：
   - requests 很少：它的测试文件里有需要真实网络的超时测试，2018 年以前的代码也大多跑不了 Python 3.12；
   - httpx 不算少，但依赖 anyio 插件的异步测试题都被剔除了，因为验证时保留了官方的 `-p no:anyio`。
3. **与比赛隐藏评测集可能重叠**：没有限制时间，同仓库的 PR 有可能出现在比赛的隐藏评测集里；与官方公开的 129 道参考题不重复。
4. **验收环境与官方不完全相同**：官方沙箱会另写一份 pytest.ini 和 conftest.py，内容不公开；我们保留了仓库自带的 pytest 配置。
5. 题面直接用的是 PR 原文，有些很短，有些直接给出了修复思路；其中有 1 道「Revert …」类题（rich_1849）。

## 核对方式

重建脚本对每份快照检查三项：

- **硬性要求**：文件树哈希、提交数与 `snapshot_manifest.jsonl` 一致，工作区干净；
- **预期一致**：HEAD 提交号与原始快照相同。提交号相同，意味着整段提交历史，包括作者、时间和提交信息，都和原始快照一致。

发布前做过的验证：

- **Linux**（git 2.34、Python 3.10）：只用这 4 个 bundle 重建了全部 500 份，HEAD 提交号全部一致；
- **macOS**（git 2.50、Python 3.9）：
  - 重建了 rich、httpx、requests 的全部 334 份，以及 fastapi 中历史最长的 3 份，HEAD 提交号全部一致；
  - 确认 500 道题的代码里都没有只差大小写的文件名，在不区分大小写的文件系统上也能正常解压。

`.tgz` 文件本身的字节和原始快照不同：打包时间、压缩率都会变，所以不提供整包校验值，以上面三项检查为准。

## 来源与许可

题目中的代码、补丁和快照来自以下开源仓库，版权归原作者所有，并遵循各自的许可证；每份快照里都带有该仓库的 LICENSE 文件。

| 仓库 | 许可证 |
|---|---|
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | MIT |
| [Textualize/rich](https://github.com/Textualize/rich) | MIT |
| [encode/httpx](https://github.com/encode/httpx) | BSD-3-Clause |
| [psf/requests](https://github.com/psf/requests) | Apache-2.0 |

`problem_statement` 是各 PR 的标题和正文原文，作者为对应 PR 的提交者，原文可以通过 `validation.jsonl` 里的 `source_pr` 链接查看。
