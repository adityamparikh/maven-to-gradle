# Maven to Gradle KTS Migration — Claude Code Skill

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that migrates Maven projects to Gradle Kotlin DSL (KTS) with version catalogs (`libs.versions.toml`).

## What It Does

- Parses `pom.xml` files (single-module and multi-module)
- Generates `build.gradle.kts`, `settings.gradle.kts`, `gradle/libs.versions.toml`, and `gradle.properties`
- Handles Spring Boot starter-parent conversion, scope mapping, BOM imports, annotation processors, Kotlin detection, and plugin mapping
- Supports **full migration** or **dual-build overlay** mode (Gradle alongside Maven)
- Provides reference guides for profiles, multi-module patterns, plugin mappings, and common gotchas

## Installation

Add this skill to your Claude Code configuration:

```bash
claude skill add --url https://github.com/adityamparikh/maven-to-gradle
```

Or clone and install locally:

```bash
git clone https://github.com/adityamparikh/maven-to-gradle.git ~/.claude/skills/maven-to-gradle
```

## Usage

In a Claude Code session, the skill is triggered automatically when you ask to:

- "Migrate this Maven project to Gradle"
- "Convert my pom.xml to build.gradle.kts"
- "Switch to Gradle"
- "Add Gradle to my Maven project" (dual-build mode)

The skill guides Claude through a 5-step workflow:

1. **Analyze** the Maven project structure
2. **Run** the migration script to generate Gradle files
3. **Review and refine** the generated output
4. **Handle profiles** and custom plugin configurations
5. **Verify** the build compiles and tests pass

## Standalone Script Usage

The migration script can also be run independently:

```bash
# Full migration (dry-run first)
python3 scripts/migrate.py /path/to/maven-project --dry-run

# Full migration (write files)
python3 scripts/migrate.py /path/to/maven-project

# Dual-build overlay (keeps Maven, adds Gradle alongside)
python3 scripts/migrate.py /path/to/maven-project --mode overlay
```

## Repository Structure

```
.
├── SKILL.md                          # Skill definition (workflow + instructions)
├── scripts/
│   └── migrate.py                    # Migration script (pom.xml → Gradle files)
└── references/
    ├── plugin-mappings.md            # Maven plugin → Gradle plugin mapping
    ├── multi-module.md               # Convention plugins, buildSrc, inter-module deps
    ├── profiles.md                   # Maven profile → Gradle equivalent patterns
    ├── gotchas.md                    # Scope mapping, resource filtering, test config, etc.
    └── dual-build.md                 # Running Maven + Gradle side by side
```

## Reference Guides

| Guide | Description |
|---|---|
| [Plugin Mappings](references/plugin-mappings.md) | Maven plugin → Gradle plugin/task mapping with code examples |
| [Multi-Module](references/multi-module.md) | Convention plugins, buildSrc patterns, inter-module dependencies |
| [Profiles](references/profiles.md) | Maven profile → Gradle equivalent for every activation type |
| [Gotchas](references/gotchas.md) | Scope mapping, resource filtering, test config, Kotlin issues |
| [Dual Build](references/dual-build.md) | Running Maven and Gradle side by side: sync strategies, CI setup |

## Requirements

- Python 3.9+ (for the migration script — uses only stdlib)
- Gradle 8.x (for the generated build files)

## License

Apache License 2.0
