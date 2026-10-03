# Flamin

Flamin is a kit for building software with a team of AI agents. You describe
what you want in plain language; the agents follow a fixed workflow, and a
small engine tracks progress, enforces file locks, and checks approval gates.

Supports Claude Code, Codex, and Cursor. The engine requires Python 3.11 or
newer and uses only the standard library.

## Getting started

Clone this repository as your master kit, then copy it into a separate folder
for each product. Open the product copy in your AI tool and follow the
[setup and first-run guide](docs/README.md).

The master kit itself never hosts a real product.

For a versioned download, use the full-kit ZIP or tar.gz on the
[GitHub Releases page](https://github.com/peterkoay/flamin/releases). The first
`v1` release and matching `@peterkoay/flamin@1.0.0` GitHub npm package are
prepared but not published yet; see [release instructions](docs/RELEASING.md).

## Documentation

- [Setup and commands](docs/README.md)
- [Workflow](docs/WORKFLOW.md)
- [Design](docs/DESIGN.md)
- [Verification](docs/VERIFICATION.md)
- [Build log](docs/BUILD_LOG.md)
- [Releasing the master kit](docs/RELEASING.md)

## License

Flamin is licensed under the [MIT License](LICENSE).
