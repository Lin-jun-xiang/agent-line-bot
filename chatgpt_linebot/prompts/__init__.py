from pathlib import Path

from .template import (
    cws_channel_template,
    system_prompt as _system_prompt_template,
    horoscope_template,
    youtube_recommend_template,
)
from chatgpt_linebot.modules.skill_loader import get_skill_agent_prompt

# Resolve workspace directory (project root)
_workspace_dir = str(Path(__file__).resolve().parent.parent.parent)

# Build skill prompt
_skill_prompt = get_skill_agent_prompt()

# Inject workspace path and skill info into system prompt
system_prompt = _system_prompt_template.replace("{workspace_dir}", _workspace_dir).replace("{skill_prompt}", _skill_prompt)
