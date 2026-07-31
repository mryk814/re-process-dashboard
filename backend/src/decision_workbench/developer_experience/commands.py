from __future__ import annotations

from collections.abc import Iterable

from decision_workbench.developer_experience.schemas import CommandPlatform, DeveloperCommand


def _quote(argument: str) -> str:
    if not argument or any(character.isspace() for character in argument):
        escaped = argument.replace('"', '\\"')
        return f'"{escaped}"'
    return argument


def developer_command(
    executable: str,
    arguments: Iterable[str] = (),
    *,
    display_text: str | None = None,
    platform: CommandPlatform = "cross-platform",
) -> DeveloperCommand:
    normalized = list(arguments)
    return DeveloperCommand(
        executable=executable,
        arguments=normalized,
        display_text=display_text or " ".join([_quote(executable), *(_quote(item) for item in normalized)]),
        platform=platform,
    )
