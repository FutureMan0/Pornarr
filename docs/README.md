# Pornarr documentation

Start here.

| Document | What it answers |
|---|---|
| [architecture.md](architecture.md) | How the system is put together and how data moves through it |
| [data-model.md](data-model.md) | What is stored, where, and why the schema looks like this |
| [api-contract.md](api-contract.md) | Endpoints, error codes, events, and how the contract is enforced |
| [adr/](adr/) | Every architecture decision, numbered, with its context and consequences |
| [integrations/](integrations/) | Indexers, download clients, metadata providers |
| [pipelines/](pipelines/) | Import, transcoding, recommendations |
| [operations/](operations/) | [Installation](operations/installation.md), deployment, backup, troubleshooting |
| [contributing/](contributing/) | Branching, releases, testing |
| [design/](design/) | What the client looks like: screen mockups, renders, brand assets |

The full system specification is
[superpowers/specs/2026-08-07-pornarr-design.md](superpowers/specs/2026-08-07-pornarr-design.md).
It is the source these documents were derived from; where they disagree, the ADRs win,
because they are updated as decisions change.

Product strategy lives in [`PRODUCT.md`](../PRODUCT.md) and the visual system in
[`DESIGN.md`](../DESIGN.md), both at the repository root. `DESIGN.md` defines the
tokens; [design/](design/) shows them assembled into screens.
