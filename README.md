# Maven to Gradle KTS Migration — Claude Code Skill

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that migrates Maven projects to Gradle Kotlin DSL (KTS) with version catalogs (`libs.versions.toml`).

## What It Does

- Parses `pom.xml` files (single-module and multi-module, with nested module recursion)
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

# Write output to a separate directory
python3 scripts/migrate.py /path/to/maven-project --output /path/to/output
```

## Repository Structure

```
.
├── SKILL.md                                  # Skill definition (workflow + instructions)
├── pyproject.toml                            # Python project config (pytest settings)
├── references/
│   ├── dual-build.md                         # Running Maven + Gradle side by side
│   ├── gotchas.md                            # Scope mapping, resource filtering, test config
│   ├── multi-module.md                       # Convention plugins, buildSrc, inter-module deps
│   ├── plugin-mappings.md                    # Maven plugin → Gradle plugin mapping
│   └── profiles.md                           # Maven profile → Gradle equivalent patterns
└── scripts/
    ├── migrate.py                            # Entry point shim
    ├── migrate/                              # Migration package
    │   ├── gradle_file_generator.py          # Generates all Gradle output files
    │   ├── maven_gradle_mappings.py          # Dependency scope + plugin mapping tables
    │   ├── migration_pipeline.py             # CLI, orchestration, and file I/O
    │   ├── pom_models.py                     # Dataclasses: Dependency, Plugin, MavenModule
    │   ├── pom_parser.py                     # XML parsing of pom.xml files
    │   └── tech_stack_detector.py            # Java/Kotlin/Spring Boot detection
    └── tests/                                # 192 tests, 100% statement coverage
        ├── conftest.py                       # Shared pytest fixtures
        ├── test_gradle_file_generator.py     # Version catalog, build files, settings, props
        ├── test_maven_gradle_mappings.py     # Scope + plugin mapping lookups
        ├── test_migration_pipeline.py        # End-to-end migration, CLI args, multi-module
        ├── test_pom_parser.py                # POM parsing: deps, plugins, profiles, config
        └── test_tech_stack_detector.py       # Java/Kotlin version, Spring Boot detection
```

## Reference Guides

| Guide | Description |
|---|---|
| [Plugin Mappings](references/plugin-mappings.md) | Maven plugin → Gradle plugin/task mapping with code examples |
| [Multi-Module](references/multi-module.md) | Convention plugins, buildSrc patterns, inter-module dependencies |
| [Profiles](references/profiles.md) | Maven profile → Gradle equivalent for every activation type |
| [Gotchas](references/gotchas.md) | Scope mapping, resource filtering, test config, Kotlin issues |
| [Dual Build](references/dual-build.md) | Running Maven and Gradle side by side: sync strategies, CI setup |

## FAQ

**Q: Does the generated output build immediately without any manual edits?**
A: For simple single-module projects it often does. For multi-module projects or those with custom plugins, expect to make manual adjustments — particularly for inter-module `project(":module")` dependencies, which the script cannot infer from POM files alone.

**Q: What's the difference between "migrate" and "overlay" mode?**
A: **Migrate** mode generates Gradle files with the expectation that you'll remove `pom.xml` files after verifying the build. **Overlay** mode adds Gradle alongside Maven so both build systems work simultaneously — useful for gradual migration or teams that need to keep Maven during a transition period. Overlay mode also manages `.gitignore` entries for Gradle build artifacts.

**Q: How does the script handle Spring Boot parent POM?**
A: It converts `spring-boot-starter-parent` into the `org.springframework.boot` and `io.spring.dependency-management` Gradle plugins, and moves the Spring Boot version into the version catalog. The Spring Boot dependency management plugin handles BOM resolution the same way the parent POM did in Maven.

**Q: What happens with Maven profiles?**
A: The script detects profiles and adds comments in the generated build files identifying each one (including activation type and any profile-specific dependencies or plugins). You need to convert them manually — see [references/profiles.md](references/profiles.md) for Gradle equivalents of every activation type.

**Q: Can I use this without Claude Code, as a standalone script?**
A: Yes. The migration script is plain Python 3.9+ with no third-party dependencies. Run `python3 scripts/migrate.py /path/to/project --dry-run` to preview output, then drop the `--dry-run` flag to write files. See [Standalone Script Usage](#standalone-script-usage) above.

**Q: How are Maven scopes mapped to Gradle configurations?**
A: `compile` → `implementation`, `provided` → `compileOnly`, `runtime` → `runtimeOnly`, `test` → `testImplementation`, `system` → `compileOnly`. Annotation processors (Lombok, MapStruct, etc.) are additionally wired to the `annotationProcessor` configuration.

**Q: Does the script handle nested multi-module projects?**
A: Yes. It recursively parses `<modules>` declarations, so a parent with a mid-level aggregator that itself declares child modules will produce build files for all levels. Circular module references are detected and handled.

**Q: What if a Maven plugin has no Gradle equivalent?**
A: The script checks a mapping table of common plugins. Known plugins are converted to their Gradle equivalents. Unknown plugins are skipped with a logged warning — you'll need to find or write a Gradle equivalent manually. See [references/plugin-mappings.md](references/plugin-mappings.md) for the full mapping table.

**Q: Why Gradle Kotlin DSL instead of Groovy DSL?**
A: Kotlin DSL (`build.gradle.kts`) provides compile-time type checking, better IDE auto-completion, and is the default for new Gradle projects. It's the direction Gradle is heading. The version catalog (`libs.versions.toml`) is format-agnostic and works with both DSLs.

**Q: How do I run the tests?**
A: From the `scripts/` directory: `python -m pytest tests/ -v`. The test suite has 192 tests with 100% statement coverage across all source modules. The only dependency is `pytest` (install via `pip install pytest`).

## Requirements

- Python 3.9+ (for the migration script — uses only stdlib)
- Gradle 8.x (for the generated build files)

## License

Apache License 2.0
