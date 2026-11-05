"""Shell Integration snippets for command blocks (OSC 133/633).

The snippets only *emit* OSC sequences. They do not run commands, wrap the
user's prompt, or change exit statuses. Installation is opt-in: ``install``
writes a file under the wsctl config directory and prints the one line to add
to the user's rc, unless ``--write-rc`` is explicitly requested.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import default_config_dir

SUPPORTED = ("bash", "zsh", "fish", "powershell")

BASH = r"""# wsctl shell integration (bash) -- emits OSC 133 only.
__wsctl_osc() { printf '\033]%s\007' "$1"; }
__wsctl_preexec() { __wsctl_osc "133;C"; }
__wsctl_precmd() {
  local status=$?
  __wsctl_osc "133;D;$status"
  __wsctl_osc "133;A"
}
if [[ -n "${PROMPT_COMMAND:-}" ]]; then
  PROMPT_COMMAND="__wsctl_precmd; ${PROMPT_COMMAND}"
else
  PROMPT_COMMAND="__wsctl_precmd"
fi
trap '__wsctl_preexec' DEBUG
PS0='\033]133;B\007'
"""

ZSH = r"""# wsctl shell integration (zsh) -- emits OSC 133 only.
autoload -Uz add-zsh-hook
__wsctl_precmd() { printf '\033]133;D;%s\007\033]133;A\007' "$?" }
__wsctl_preexec() { printf '\033]133;C\007' }
add-zsh-hook precmd __wsctl_precmd
add-zsh-hook preexec __wsctl_preexec
"""

FISH = r"""# wsctl shell integration (fish) -- emits OSC 133 only.
function __wsctl_prompt --on-event fish_prompt
    printf '\033]133;A\007'
end
function __wsctl_preexec --on-event fish_preexec
    printf '\033]133;C\007'
end
function __wsctl_postexec --on-event fish_postexec
    printf '\033]133;D;%s\007' $status
end
"""

POWERSHELL = r"""# wsctl shell integration (PowerShell) -- emits OSC 133 only.
function global:__Wsctl-Osc([string]$Payload) {
    [Console]::Write("$([char]27)]$Payload$([char]7)")
}
$global:__WsctlOriginalPrompt = $function:prompt
function global:prompt {
    __Wsctl-Osc "133;D;$LASTEXITCODE"
    __Wsctl-Osc "133;A"
    & $global:__WsctlOriginalPrompt
}
Set-PSReadLineKeyHandler -Key Enter -ScriptBlock {
    __Wsctl-Osc "133;C"
    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
}
"""

TEMPLATES = {
    "bash": BASH,
    "zsh": ZSH,
    "fish": FISH,
    "powershell": POWERSHELL,
}

RC_FILES = {
    "bash": "~/.bashrc",
    "zsh": "~/.zshrc",
    "fish": "~/.config/fish/config.fish",
    "powershell": "~/.config/powershell/Microsoft.PowerShell_profile.ps1",
}


def detect_shell() -> str:
    """Best-effort shell name from ``$SHELL`` (falls back to bash)."""
    name = Path(os.environ.get("SHELL") or "bash").name.lower()
    if name in {"pwsh", "powershell", "powershell.exe"}:
        return "powershell"
    return name if name in TEMPLATES else "bash"


def snippet(shell: str) -> str:
    if shell not in TEMPLATES:
        raise ValueError(f"不支持的 shell：{shell}（可选：{', '.join(SUPPORTED)}）")
    return TEMPLATES[shell]


def install_path(shell: str) -> Path:
    return default_config_dir() / "shell-integration" / f"wsctl.{_extension(shell)}"


def _extension(shell: str) -> str:
    return "ps1" if shell == "powershell" else ("fish" if shell == "fish" else "sh")


def install(shell: str, *, write_rc: bool = False) -> tuple[Path, Path | None]:
    """Write the snippet. Returns ``(snippet_path, rc_path_if_modified)``."""
    text = snippet(shell)
    target = install_path(shell)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    rc_path: Path | None = None
    if write_rc:
        rc = Path(RC_FILES.get(shell, RC_FILES["bash"])).expanduser()
        rc.parent.mkdir(parents=True, exist_ok=True)
        marker = "# wsctl shell integration"
        existing = rc.read_text("utf-8") if rc.is_file() else ""
        if marker not in existing:
            source_line = (
                f'. "{target}"'
                if shell in {"bash", "zsh"}
                else (f"source \"{target}\"" if shell == "fish" else f". \"{target}\"")
            )
            with rc.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(f"\n{marker}\n{source_line}\n")
            rc_path = rc
    return target, rc_path


__all__ = [
    "RC_FILES",
    "SUPPORTED",
    "TEMPLATES",
    "detect_shell",
    "install",
    "install_path",
    "snippet",
]
