"""DSH research profile 落盘（计划 v3 M2 / D7：权限默认最小）。

在 <DSH_HOME>/profiles/research/ 创建一个自定义 profile（与 web 同底座）：
- package.json   —— profile 清单（bundles = dsh-base + dsh-web-app；幂等，存在不覆盖）
- cordis.yml     —— 空根 `[]`（树的构成全靠 patch；幂等）
- cordis.patch.yml —— 我们的 patch：
    · mcp-qresearch：stdio 挂本仓库的 qresearch MCP server（文件路径直启，与 cwd 无关）
    · sandbox-policy：workspace-write（不是 sdk-minimal 的 danger-full-access！）
    · approval：ask（fail-closed，A1 的壳侧审批呈现）
  已存在时不覆盖（保护手工修改）——--force 才重写。

可选 --install-skill-to <projectRoot>：把 research-protocol SKILL.md 复制到
<projectRoot>/.dsh/skills/research-protocol/SKILL.md。

用法：
    .venv/Scripts/python.exe -X utf8 examples/setup_dsh_profile.py \
        --projects-root D:/AI/Agent/Try/research-projects \
        [--dsh-home ~/.dsh] [--install-skill-to D:/somewhere/research] [--force]

之后按"改 → 验证 → 记录"三步走：`dsh --profile research --dump-config` 核对
合并结果，再 `dsh --profile research` 启动（M2 live 走查 L3）。
注意：profile 的 node_modules 在 <DSH_HOME>/profiles/ 根共享（pnpm workspace）；
本脚本不装 npm 依赖——所需插件（dsh-mcp-client / dsh-sandbox-policy /
dsh-user-approval）随 dsh 自带。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ENTRY = REPO_ROOT / "qresearch" / "mcp_server.py"
SKILL_SRC = REPO_ROOT / "qresearch" / "skills" / "research-protocol" / "SKILL.md"

# 与 web profile 同底座（dsh web 是壳 A；research 只是它的 patch 差异）
PROFILE_MANIFEST = {
    "name": "dsh-profile-research",
    "private": True,
    "dependencies": {},
    "dsh": {"profile": {"bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
    ]}},
}
PROFILE_ROOT_YAML = """\
# dsh profile root — an empty entry list. The tree is composed as patches:
# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml, then any
# --patch overlays. Edit cordis.patch.yml, not this file.
[]
"""


def default_dsh_home() -> Path:
    env = os.environ.get("DSH_HOME")
    if env:
        return Path(env)
    return Path.home() / ".dsh"


def patch_yaml(python_exe: str, projects_root: str) -> str:
    """与计划 §4.2 M2 的 profile 片段一致；路径全部绝对化（stdio spawn 与 cwd 无关）。

    loader patch 语义（dsh-app-boot applyEntryPatches）：`id:` 定位**既有**条目做
    覆盖/禁用；新增条目必须经 `insert:` 追加——直接写 `- id: mcp-qresearch` 会
    "entry not found" 被跳过（dump-config 实测）。
    """
    entry = str(SERVER_ENTRY).replace("\\", "/")
    return f"""\
# qresearch research profile patch（examples/setup_dsh_profile.py 生成）
# 权限默认最小（D7）：workspace-write + ask；禁用 sdk-minimal。
# 新增插件条目必须走 insert:（id 定位只用于覆盖既有条目）。
- insert:
    - id: mcp-qresearch
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: qresearch
        transport: stdio
        command: {python_exe}
        args: ['-X', 'utf8', '{entry}', '--project-root', '{projects_root}']
        toolCallTimeoutMs: 60000
- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config: {{ mode: workspace-write }}
- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  config: {{ policy: ask }}
"""


def install_skill(project_root: Path) -> Path:
    dst = project_root / ".dsh" / "skills" / "research-protocol" / "SKILL.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL_SRC, dst)
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description="qresearch DSH profile 落盘")
    parser.add_argument("--projects-root", required=True,
                        help="研究项目根目录（MCP server 的 --project-root）")
    parser.add_argument("--dsh-home", default=None,
                        help="DSH_HOME（默认 %%DSH_HOME%% 或 ~/.dsh）")
    parser.add_argument("--install-skill-to", default=None,
                        help="顺便把 research-protocol skill 装到该研究项目根")
    parser.add_argument("--python", default=sys.executable,
                        help="MCP server 用的解释器（默认当前 venv）")
    parser.add_argument("--force", action="store_true",
                        help="已存在 cordis.patch.yml 时也重写（默认保护手工修改）")
    args = parser.parse_args()
    python_exe = str(Path(args.python).resolve()).replace("\\", "/")

    dsh_home = Path(args.dsh_home) if args.dsh_home else default_dsh_home()
    profile_dir = dsh_home / "profiles" / "research"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # profile 清单与空根：幂等（首次创建；已有不动）
    manifest_path = profile_dir / "package.json"
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps(PROFILE_MANIFEST, indent=2) + "\n", encoding="utf-8")
        print(f"profile 清单已创建：{manifest_path}")
    root_yaml = profile_dir / "cordis.yml"
    if not root_yaml.exists():
        root_yaml.write_text(PROFILE_ROOT_YAML, encoding="utf-8")
        print(f"profile 根已创建：{root_yaml}")

    # patch：默认不覆盖已有（保护手工修改，三步法里的"改"）
    target = profile_dir / "cordis.patch.yml"
    if target.exists() and not args.force:
        print(f"patch 已存在，未改动：{target}（--force 重写）")
    else:
        target.write_text(patch_yaml(python_exe, args.projects_root), encoding="utf-8")
        print(f"profile patch 已写入：{target}")

    print(f"  MCP server 入口：{SERVER_ENTRY}（存在={SERVER_ENTRY.exists()}）")
    print(f"  projects root：{Path(args.projects_root).resolve()}")
    print("验证（改→验证→记录）：")
    print("  dsh --profile research --dump-config   # 核对合并结果")
    print("  dsh --profile research                 # 启动（L3 live 走查）")
    if args.install_skill_to:
        dst = install_skill(Path(args.install_skill_to))
        print(f"skill 已安装：{dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
