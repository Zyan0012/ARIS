# Windows/WSL Command Discipline

Use this reference whenever a Codex skill writes command examples, invokes ARIS helper scripts, or edits workflow defaults that must work from both Windows PowerShell and WSL Bash.

## Principles

1. **Use one native shell end to end.** PowerShell should handle Windows paths such as `D:\ML\research_code`; WSL Bash should handle `/mnt/d/ML/research_code`. Do not split one file operation across shells.
2. **Prefer helper scripts over nested quoting.** If a command needs a heredoc, embedded JSON, several quoted paths, or more than one or two simple commands, put the logic in an ARIS helper script and invoke that script.
3. **Resolve the ARIS install explicitly.** Skills must call helper scripts from `ARIS_REPO` or the workstation default ARIS repo, because the user's active research project usually does not contain ARIS tools.
4. **Keep project paths as arguments.** Pass the active project directory via `--project`, `-Project`, or a positional argument. Do not assume ARIS is the active project.
5. **Make cross-platform examples paired.** If a skill documents a workflow that users may run manually, include a PowerShell form and a WSL Bash form when path syntax matters.
6. **Keep secrets out of command lines.** Prefer environment variables or config files for API keys and tokens.
7. **Use structured parsers for structured data.** JSONL, TOML, YAML, and Markdown manifests should be parsed by a script or library instead of fragile shell text processing when the result affects behavior.

## Avoid

- Complex `wsl -e bash -lc '...'` commands launched from PowerShell, especially with embedded quotes, heredocs, or JSON.
- Commands that assume helper scripts live inside the active research project.
- `cmd /c` wrappers for recursive file moves, deletes, or generated shell strings.
- Updating Windows files from WSL and then immediately editing the same files from PowerShell in the same step unless the path has been resolved and verified.

## Preferred Patterns

PowerShell:

```powershell
$ArisRepo = if ($env:ARIS_REPO) { $env:ARIS_REPO } else { "D:\ML\research_code\ARIS" }
& "$ArisRepo\tools\meta_opt\sync_codex_sessions.ps1" -Project (Get-Location)
python "$ArisRepo\tools\meta_opt\meta_optimize_state.py" --project (Get-Location)
```

WSL Bash:

```bash
ARIS_REPO="${ARIS_REPO:-/mnt/d/ML/research_code/ARIS}"
bash "$ARIS_REPO/tools/meta_opt/sync_codex_sessions.sh" "$PWD"
python "$ARIS_REPO/tools/meta_opt/meta_optimize_state.py" --project "$PWD"
```

When a skill needs more logic than these short invocations, add or reuse a helper under the ARIS repo's `tools/` directory and call it through `ARIS_REPO`.
