"""Parse and validate arcoa.yaml agent manifests."""

import re
from dataclasses import dataclass, field

_SKILL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$")
_SECRET_REF_PATTERN = re.compile(r"^\$\{secrets\.([A-Z_][A-Z0-9_]*)\}$")
_VALID_RUNTIMES = {"python:3.13", "python:3.12", "node:20", "node:22"}


@dataclass
class SkillDef:
    id: str
    description: str
    base_price: str = "0.01"


@dataclass
class AgentManifest:
    name: str
    runtime: str
    skills: list[SkillDef] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cpu: str = "0.25"
    memory_mb: int = 512
    entrypoint: str = "handler.py"


def parse_manifest(data: dict) -> AgentManifest:
    """Parse a raw manifest dict (from YAML) into a validated AgentManifest.

    Raises ValueError on invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a YAML mapping")

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("'name' is required and must be a string")
    if len(name) > 128:
        raise ValueError("'name' must be <= 128 characters")

    runtime = data.get("runtime", "python:3.13")
    if runtime not in _VALID_RUNTIMES:
        raise ValueError(f"'runtime' must be one of: {', '.join(sorted(_VALID_RUNTIMES))}")

    # Parse skills
    skills_raw = data.get("skills", [])
    if not isinstance(skills_raw, list):
        raise ValueError("'skills' must be a list")
    if len(skills_raw) > 20:
        raise ValueError("Maximum 20 skills per agent")

    skills = []
    seen_ids = set()
    for i, s in enumerate(skills_raw):
        if not isinstance(s, dict):
            raise ValueError(f"skills[{i}] must be a mapping")
        skill_id = s.get("id")
        if not skill_id or not _SKILL_ID_PATTERN.match(skill_id):
            raise ValueError(f"skills[{i}].id must be alphanumeric+hyphens, 1-64 chars")
        if skill_id in seen_ids:
            raise ValueError(f"Duplicate skill id: {skill_id}")
        seen_ids.add(skill_id)

        desc = s.get("description", "")
        if not desc:
            raise ValueError(f"skills[{i}].description is required")

        base_price = str(s.get("base_price", "0.01"))

        skills.append(SkillDef(
            id=skill_id,
            description=desc,
            base_price=base_price,
        ))

    # Parse requirements (system packages)
    requirements = data.get("requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("'requirements' must be a list")
    if len(requirements) > 50:
        raise ValueError("Maximum 50 system requirements")
    for req in requirements:
        if not isinstance(req, str) or len(req) > 128:
            raise ValueError(f"Invalid requirement: {req}")

    # Parse env vars (may reference secrets)
    env = data.get("env", {})
    if not isinstance(env, dict):
        raise ValueError("'env' must be a mapping")
    for k, v in env.items():
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", k):
            raise ValueError(f"env key must be UPPER_SNAKE_CASE: {k}")
        if not isinstance(v, str):
            raise ValueError(f"env value must be a string: {k}")

    # Parse resource limits
    cpu = str(data.get("cpu", "0.25"))
    if cpu not in {"0.25", "0.5", "1", "2"}:
        raise ValueError("'cpu' must be one of: 0.25, 0.5, 1, 2")

    memory_mb = int(data.get("memory_mb", 512))
    if memory_mb < 128 or memory_mb > 4096:
        raise ValueError("'memory_mb' must be between 128 and 4096")

    entrypoint = data.get("entrypoint", "handler.py")
    if not isinstance(entrypoint, str) or len(entrypoint) > 255:
        raise ValueError("'entrypoint' must be a string <= 255 chars")

    return AgentManifest(
        name=name,
        runtime=runtime,
        skills=skills,
        requirements=requirements,
        env=env,
        cpu=cpu,
        memory_mb=memory_mb,
        entrypoint=entrypoint,
    )


def extract_secret_refs(env: dict[str, str]) -> list[str]:
    """Extract secret names referenced in env vars via ${secrets.NAME} syntax."""
    refs = []
    for v in env.values():
        m = _SECRET_REF_PATTERN.match(v)
        if m:
            refs.append(m.group(1))
    return refs
