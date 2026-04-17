"""
Skill Loader — Discovers skills from the workspace.
Scans for directories containing SKILL.md, extracts name/description/path
so the agent knows what skills exist and where to find their docs.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional


# Project root (where the project lives)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Skills live under <project_root>/skills/
_SKILLS_DIR = _PROJECT_ROOT / "skills"


def discover_skills(root: Path = None) -> List[Dict]:
    """
    Discover skills by scanning for directories containing SKILL.md.
    Searches both direct children and one level deeper (skills/<name>/SKILL.md).
    """
    if root is None:
        root = _SKILLS_DIR

    skills = []

    if not root.exists():
        return skills

    # Scan: skills/*/SKILL.md
    for skill_md_path in root.glob("*/SKILL.md"):
        skill_dir = skill_md_path.parent
        skill_info = _load_skill_meta(skill_dir)
        if skill_info:
            skills.append(skill_info)

    return skills


def _load_skill_meta(skill_dir: Path) -> Optional[Dict]:
    """Load skill metadata (name, description, paths) from a skill directory."""
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return None

    # Extract name and description from package.json or _meta.json
    name = None
    description = None
    env_key = None

    for meta_file in ["package.json", "_meta.json"]:
        meta_path = skill_dir / meta_file
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("name", "").split("/")[-1] or None
                description = meta.get("description", "")

                # Read clawdhub config if present
                clawdhub = meta.get("clawdhub", {})
                env_key = clawdhub.get("envKey")
                break
            except Exception:
                pass

    # Fallback: extract name from directory
    if not name:
        name = skill_dir.name

    # Find CLI binary
    bin_path = None
    bin_dir = skill_dir / "bin"
    if bin_dir.exists():
        for f in bin_dir.iterdir():
            if f.suffix == ".py" and not f.name.startswith("_"):
                bin_path = str(f.resolve())
                break

    # Clean name: "finance-tracker" → "finance"
    clean_name = name
    for suffix in ["-tracker", "-skill", "-tool"]:
        if suffix in clean_name:
            clean_name = clean_name.split(suffix)[0]
            break

    if not env_key:
        env_key = f"{clean_name.upper()}_DATA_DIR"

    return {
        "name": clean_name,
        "raw_name": name,
        "description": description or f"Skill: {clean_name}",
        "dir": str(skill_dir.resolve()),
        "bin": bin_path,
        "skill_md_path": str(skill_md_path.resolve()),
        "env_key": env_key,
    }


def get_skill_agent_prompt() -> str:
    """
    Discover skills and return a prompt segment for the system prompt.
    Tells the agent what skills exist and how to use them step by step.
    """
    skills = discover_skills()

    if not skills:
        return "(目前沒有已註冊的技能)"

    lines = [
        "--- 已註冊技能 (REGISTERED SKILLS) ---",
        "",
        "以下技能已安裝，你可以透過工具來使用它們。",
        "",
        "使用技能的標準流程：",
        "  步驟 1: 用 read_file 工具讀取該技能的 SKILL.md 檔案，了解完整指令格式",
        "  步驟 2: 根據 SKILL.md 說明，用 execute_command 工具執行正確的 CLI 指令",
        "  步驟 3: 將執行結果用口語化、親切的方式回覆用戶",
        "  步驟 4: 如果指令失敗，根據錯誤訊息修正指令後重試",
        "",
        "⚠️ 重要：不要猜測指令格式！一定要先讀 SKILL.md 再執行。",
        "",
    ]

    for skill in skills:
        lines.append(f"技能名稱: {skill['name']}")
        lines.append(f"  說明: {skill['description']}")
        lines.append(f"  SKILL.md 路徑: {skill['skill_md_path']}")
        lines.append(f"  CLI 執行檔: {skill['bin']}")
        lines.append(f"  執行方式: python {skill['bin']} <子指令> [參數...]")
        lines.append(f"  資料環境變數: {skill['env_key']}（已自動設定，無需手動處理）")
        lines.append("")

    lines.append("--- END SKILLS ---")

    return "\n".join(lines)
