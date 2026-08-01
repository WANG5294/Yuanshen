"""SkillLoader（可重载：任务结束提取的新经验立即可用）。"""
import re
from pathlib import Path

from yuanshen.config import SKILLS_DIR


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self.reload()

    def reload(self):
        self.skills = {}
        if not self.skills_dir.exists():
            return
        for skill_dir in sorted(self.skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.exists():
                parsed = self.parse(skill_md)
                if parsed:
                    self.skills[parsed["name"]] = parsed

    def parse(self, path: Path):
        content = path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            return None
        frontmatter, body = match.groups()
        meta = {}
        key = None
        for line in frontmatter.strip().split("\n"):
            if ":" in line and not line.startswith((" ", "\t")):
                key, value = line.split(":", 1)
                key = key.strip()
                meta[key] = value.strip().strip("\"'")
            elif key:
                meta[key] += " " + line.strip()
        if "name" not in meta or "description" not in meta:
            return None
        return {"name": meta["name"], "description": meta["description"],
                "body": body.strip()}

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(无可用技能)"
        return "\n".join(f"- {n}: {s['description']}"
                         for n, s in self.skills.items())

    def get_content(self, name: str):
        skill = self.skills.get(name)
        if not skill:
            return None
        return f"# Skill: {skill['name']}\n\n{skill['body']}"


SKILLS = SkillLoader(SKILLS_DIR)
