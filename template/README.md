# {{project_name}}

{{idea}}

Built with [OhmAI](https://github.com/ohmai-pcb/ohmai) {{ohmai_version}}: an agent does the work, the owner
approves each phase gate.

- Where we are: [`STATUS.md`](STATUS.md)
- Rules for agents: [`AGENTS.md`](AGENTS.md)
- Requirements → [`docs/requirements.md`](docs/requirements.md), architecture → [`docs/architecture.md`](docs/architecture.md),
  decisions → [`docs/decisions.md`](docs/decisions.md)
- KiCad project: [`hardware/`](hardware/). BOM: [`bom/bom.csv`](bom/bom.csv)
- Fab releases: GitHub releases tagged `rev-A`, `rev-B`, ...

```bash
pip install ohmai            # or: pip install -e <ohmai checkout>/cli
ohmai status
ohmai check current
```
