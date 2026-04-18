# `skills-codex`

Codex-native mirror of the base ARIS skill set.

## Scope

This package keeps the main `skills/` workflows available for OpenAI Codex CLI.

Recent core workflow follow-up skills mirrored here include:

- `training-check`
- `result-to-claim`
- `ablation-planner`

These skills cover the experiment follow-up chain:

1. monitor training quality early
2. judge what claims the results actually support
3. design reviewer-facing ablations before paper writing

## Install

> 💡 **Recommended: project-local symlink** (since v0.4.2). Project isolation keeps ARIS workflows separate from other community skill packs (Superpowers, etc.). See issue [#118](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep/issues/118).

```bash
# 1. Clone ARIS once to a stable location
git clone https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep.git ~/aris_repo

# 2. Attach to a Codex project (auto-detects platform from AGENTS.md):
cd ~/your-paper-project
bash ~/aris_repo/tools/install_aris.sh
# → creates .agents/skills/aris symlink → <aris-repo>/skills/skills-codex/
# → adds managed block to AGENTS.md telling agent to use only project-local skills

# Windows (PowerShell, junctions need admin or developer mode):
.\tools\install_aris.ps1 C:\path\to\your-paper-project
```

<details>
<summary><b>Alternative: legacy global install (`~/.codex/skills/`)</b></summary>

```bash
cp -a ~/aris_repo/skills/skills-codex/* ~/.codex/skills/
```

Global install increases the risk of skill name collisions when other community skill packs are also installed globally. Use only if you understand the trade-off and don't mix ARIS with other packs.

</details>

<details>
<summary><b>Alternative: project-local copy (per-project customization)</b></summary>

```bash
mkdir -p ~/your-project/.agents/skills
bash ~/aris_repo/tools/smart_update.sh \
    --project ~/your-project \
    --target-subdir .agents/skills/aris \
    --apply
# Update with the same command (smart_update detects personal customizations)
```

</details>

Optional companion dependency for the `deepxiv` skill:

```bash
pip install deepxiv-sdk
```

If you also use reviewer overlay packages, install this base package first, then apply the overlay on top.
