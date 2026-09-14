"""HPC 执行后端（Phase 8）。

扩展点是既有 ToolRunner 接口（实验不经 LLM 的铁律不变）：把
ExperimentManager 的 runner 换成 SlurmRunner 即切换到集群执行。
"""
from .slurm import SlurmConfig, SlurmRunner, render_sbatch

__all__ = ["SlurmConfig", "SlurmRunner", "render_sbatch"]
