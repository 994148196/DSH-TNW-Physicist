"""Slurm 执行后端（计划 v2 Phase 8：HPC）。

实现 ToolRunner 接口：ExperimentManager(runner=SlurmRunner(cfg)) 即把实验
切到集群。铁律不变——实验不经 LLM，提交/轮询是确定性代码。

诚实边界：本机无 Slurm 集群，dry_run=True（默认）只生成并校验 sbatch 脚本、
不提交；真实提交路径（sbatch/squeue）按标准 Slurm 命令实现，需集群环境验收。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from qresearch.experiments.manager import RunOutcome, ToolRunner


@dataclass
class SlurmConfig:
    partition: str = "cpu"
    time_min: int = 60
    mem_gb: int = 4
    cpus: int = 1
    account: str | None = None
    python_exe: str = "python"  # 远端解释器（含 qresearch 依赖的环境）
    submit_cmd: str = "sbatch"  # 例：跨机可换 ["ssh", "user@cluster", "sbatch"]
    poll_interval_s: int = 10


def _time_str(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}:00"


def render_sbatch(
    tool_name: str,
    inputs_file: Path,
    out_file: Path,
    *,
    cfg: SlurmConfig,
    job_name: str | None = None,
) -> str:
    """生成 sbatch 脚本文本（纯函数，可离线校验）。"""
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={job_name or f'qresearch_{tool_name}'}",
        f"#SBATCH --partition={cfg.partition}",
        f"#SBATCH --time={_time_str(cfg.time_min)}",
        f"#SBATCH --mem={cfg.mem_gb}G",
        f"#SBATCH --cpus-per-task={cfg.cpus}",
    ]
    if cfg.account:
        lines.append(f"#SBATCH --account={cfg.account}")
    lines += [
        "",
        "# qresearch 实验任务：实验不经 LLM（计划 v2 §15.6），失败退出码非零",
        "set -euo pipefail",
        f"{cfg.python_exe} -X utf8 -m qresearch.tools.cli run "
        f"--tool {tool_name} --inputs-file {inputs_file.as_posix()} "
        f"--out {out_file.as_posix()}",
        "",
    ]
    return "\n".join(lines)


class SlurmRunner(ToolRunner):
    """slurm 提交型 runner。dry_run=True（默认）只产出脚本，不做真实计算。

    dry-run 的 RunOutcome.result 带 dry_run 标记且不含工具输出字段——
    三层验证自然判不合格，证据资格门自动拒绝引用（诚实失败优于冒充成功）。
    """

    def __init__(self, cfg: SlurmConfig, *, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run

    def run(self, spec, inputs: dict, workspace: Path, timeout_s: float) -> RunOutcome:
        workspace.mkdir(parents=True, exist_ok=True)
        in_path = workspace / "inputs.json"
        out_path = workspace / "result.json"
        in_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        script = render_sbatch(spec.name, in_path, out_path, cfg=self.cfg)
        script_path = workspace / "job.sbatch"
        script_path.write_text(script, encoding="utf-8")

        if self.dry_run:
            return RunOutcome(
                result={"dry_run": True,
                        "note": "dry-run：仅生成 sbatch 脚本，未提交（无 Slurm 集群）",
                        "script": str(script_path)},
                log_text=script,
            )

        # ---- 真实提交路径（需 Slurm 集群）
        proc = subprocess.run(
            [*self.cfg.submit_cmd.split(), str(script_path)],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"sbatch 退出码 {proc.returncode}：{proc.stderr[-500:]}")
        match = re.search(r"Submitted batch job (\d+)", proc.stdout)
        if match is None:
            raise RuntimeError(f"无法从 sbatch 输出解析 job id：{proc.stdout[:200]}")
        job_id = match.group(1)

        # 轮询等待作业离开队列（简化：squeue 消失即认为结束，结果以 result.json 为准）
        while True:
            check = subprocess.run(
                ["squeue", "-j", job_id, "-h"], capture_output=True, text=True,
            )
            if check.returncode != 0 or not check.stdout.strip():
                break
            time.sleep(self.cfg.poll_interval_s)

        if not out_path.exists():
            raise RuntimeError(
                f"Slurm 作业 {job_id} 结束但未产出 result.json（查看集群日志）")
        result = json.loads(out_path.read_text(encoding="utf-8"))
        return RunOutcome(result=result, log_text=script + f"\njob {job_id}\n")
