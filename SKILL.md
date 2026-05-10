---
name: maven-to-gradle
description: Migrates Maven projects to Gradle Kotlin DSL (build.gradle.kts) with version catalogs (libs.versions.toml). Covers single-module and multi-module projects, Spring Boot starter-parent conversion, dependency-scope and BOM mapping, Maven plugin to Gradle plugin translation, and Maven profile equivalents. Also supports a dual-build overlay mode that adds Gradle alongside Maven without removing pom.xml. Triggers on requests to convert pom.xml to Gradle, migrate from Maven to Gradle, generate build.gradle.kts, set up a Gradle version catalog from Maven, modernize a JVM build system, or add Gradle alongside Maven for gradual migration. Does not apply to Gradle-to-Maven conversion, authoring brand-new Gradle projects from scratch with no Maven source, Groovy DSL (build.gradle) authoring unrelated to migration, or non-JVM build tools (npm, Bazel, sbt, Make).
allowed-tools: Read(*), Glob(*), Grep(*), Bash(python3 scripts/migrate.py:*), Bash(./mvnw:*), Bash(mvn:*), Bash(./gradlew:*), Bash(gradle:*), Bash(gh api:*), WebFetch(*), WebSearch(*), mcp__claude_ai_Context7__*, mcp__plugin_context7_context7__*
---

# Maven to Gradle KTS Migration

## Workflow

1. **Analyze** the Maven project structure (single vs multi-module, Spring Boot, Kotlin).
2. **Run** `scripts/migrate.py` to generate baseline Gradle files.
3. **Review and refine** the generated output.
4. **Handle profiles** and custom plugin configurations manually.
5. **Verify** the build compiles and tests pass.

## Verify Library and Framework Versions

Plugin IDs, DSL syntax, and dependency coordinates change between Gradle versions. Before finalizing output, look up current docs (Context7 MCP, web fetch, Gradle Plugin Portal, or `gh api repos/{owner}/{repo}/releases/latest`) for any plugin or framework whose version is newer than the model's training data, or whose Gradle equivalent is uncommon. Do not assume a plugin ID or DSL form is correct from memory.

## Step 1: Analyze the Project

- Read the root `pom.xml`: packaging type (jar/pom/war), parent POM, modules, profiles.
- For multi-module projects, check each child `pom.xml` for inter-module dependencies.
- Identify special plugins that need manual conversion (see `references/plugin-mappings.md`).

## Step 2: Run the Migration Script

Two modes:

**Full migration** (default) — generates Gradle files; suggests removing Maven after verification:
```bash
python3 scripts/migrate.py <path-to-maven-project> --dry-run
```

**Overlay / dual-build** — adds Gradle alongside Maven so both build systems work:
```bash
python3 scripts/migrate.py <path-to-maven-project> --mode overlay --dry-run
```

Overlay mode also appends Gradle entries to `.gitignore` and preserves all `pom.xml` files.

The script generates:
- `gradle/libs.versions.toml` — version catalog with all dependencies, BOMs, and plugins
- `settings.gradle.kts` — project name and module includes
- `build.gradle.kts` — root build file (and per-module for multi-module projects)
- `gradle.properties` — daemon, parallel, caching settings

Review `--dry-run` output first, then run without the flag to write files.

The script handles:
- Spring Boot starter-parent → spring-boot + dependency-management plugins
- Maven scope → Gradle configuration mapping (compile→implementation, provided→compileOnly, etc.)
- BOM imports → `platform()` dependencies
- Annotation processors (Lombok, MapStruct, etc.) → `annotationProcessor` configuration
- Dependency exclusions
- Java toolchain from compiler plugin or properties
- Kotlin detection and plugin setup

## Step 3: Review and Refine

The generated files are a **starting point**. Always review and adjust:

**Version catalog** (`libs.versions.toml`):
- Consolidate duplicate version refs (script deduplicates where possible)
- Add `[bundles]` for commonly grouped dependencies
- Verify BOM entries have correct versions

**Build files** (`build.gradle.kts`):
- Add inter-module `project(":module-name")` dependencies (script cannot infer these)
- Configure `developmentOnly` for Spring Boot DevTools
- Set up publishing if needed
- Wire annotation processors for Kotlin projects (kapt/ksp)

**For multi-module projects:**
- Decide between `allprojects`/`subprojects` blocks vs convention plugins (buildSrc)
- See `references/multi-module.md` for patterns — prefer convention plugins for 5+ modules
- Ensure `spring-boot` plugin only applies to bootable module(s)
- Apply `io.spring.dependency-management` to all modules that need Spring BOM resolution

## Step 4: Handle Profiles

Maven profiles require manual conversion. The script adds comments identifying each profile.

See `references/profiles.md` for complete patterns:
- Property-activated → `project.hasProperty("name")` checks
- Default-active → `gradle.properties` defaults
- JDK-activated → `JavaVersion.current()` checks
- Environment profiles → Spring Boot's own profile mechanism preferred

## Step 5: Verify

```bash
gradle wrapper --gradle-version=8.12
./gradlew build
./gradlew dependencies  # compare with: mvn dependency:tree
```

See `references/gotchas.md` section 9 for the full verification checklist.

## Known Limitations

The migration script provides a solid starting point but has these limitations that require manual intervention:

- **Custom Maven plugins** — Plugins without a direct Gradle equivalent are flagged with TODO comments but not converted
- **Profile conversion** — Maven profiles are identified and commented in the output; actual Gradle equivalent logic must be written manually (see `references/profiles.md`)
- **Resource filtering** — Maven-style `${property}` resource filtering is not auto-configured; Gradle's `processResources` expand must be set up manually
- **Publishing configuration** — `maven-publish` plugin setup (POM metadata, repository credentials) is not generated
- **Concatenated property expressions** — Properties like `${prefix}/${suffix}` with multiple interpolations in a single value are not resolved
- **Kotlin KSP** — The script detects Kotlin and adds `kotlin("jvm")` but does not auto-detect whether KSP should replace kapt for annotation processing
- **Repository credentials** — Authenticated repositories from Maven `settings.xml` are not migrated (Gradle uses different credential mechanisms)
- **Shade/Assembly plugins** — `maven-shade-plugin` and `maven-assembly-plugin` configurations require manual conversion to Gradle's `shadowJar` or custom `Jar` tasks

## Reference Files

- **[plugin-mappings.md](references/plugin-mappings.md)** — Maven plugin → Gradle plugin/task mapping with code examples
- **[multi-module.md](references/multi-module.md)** — Convention plugins, buildSrc patterns, inter-module dependencies, Spring Boot multi-module
- **[profiles.md](references/profiles.md)** — Maven profile → Gradle equivalent for every activation type
- **[gotchas.md](references/gotchas.md)** — Scope mapping, resource filtering, test config, Kotlin issues, performance tips, verification checklist
- **[dual-build.md](references/dual-build.md)** — Running Maven and Gradle side by side: sync strategies, CI setup, gradual migration path
