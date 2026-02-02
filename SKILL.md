---
name: maven-to-gradle
description: Migrate Maven projects to Gradle Kotlin DSL (KTS) with version catalogs (libs.versions.toml). Handles single-module and multi-module projects, Spring Boot parent POM conversion, Maven profile equivalents, and plugin mapping. Use when the user wants to convert a Maven project (pom.xml) to Gradle, migrate from Maven to Gradle, generate build.gradle.kts from pom.xml, set up version catalogs, or convert Maven multi-module projects to Gradle conventions. Also triggered by requests to "switch to Gradle", "convert my build", or "modernize my build system". Supports dual-build overlay mode where Gradle is added alongside Maven without removing it — triggered by "add Gradle to my Maven project", "run both Maven and Gradle", "dual build", or "keep both build systems".
---

# Maven to Gradle KTS Migration

Migrate Maven projects to Gradle Kotlin DSL with version catalogs, following Gradle conventions.

## Migration Workflow

1. **Analyze** the Maven project structure (single vs multi-module, Spring Boot, Kotlin)
2. **Run** the migration script to generate baseline Gradle files
3. **Review and refine** the generated output
4. **Handle profiles** and custom plugin configurations manually
5. **Verify** the build compiles and tests pass

## Step 1: Analyze the Project

Before running the script, examine the Maven project:

- Read the root `pom.xml` to identify: packaging type (jar/pom/war), parent POM, modules, profiles
- For multi-module projects, check each child `pom.xml` for inter-module dependencies
- Identify special plugins that need manual conversion (see references/plugin-mappings.md)

## Step 2: Run the Migration Script

Two modes are available:

**Full migration** (default) — generates Gradle files, suggests removing Maven after verification:
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
