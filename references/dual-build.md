# Dual-Build: Maven + Gradle Side by Side

## Table of Contents
1. When to use dual-build
2. Project structure
3. Keeping dependencies in sync
4. CI/CD with dual builds
5. Build output isolation
6. Gradual migration path

## 1. When to Use Dual-Build

Dual-build is useful when:
- Team needs time to evaluate Gradle before committing
- CI/CD pipeline migration needs to happen gradually
- Some developers prefer Maven while others adopt Gradle
- Corporate build infrastructure only supports Maven but local dev benefits from Gradle speed
- You want to validate Gradle produces identical artifacts before switching

## 2. Project Structure

Maven and Gradle files coexist naturally since they use different filenames:

```
project/
├── pom.xml                    ← Maven
├── build.gradle.kts           ← Gradle
├── settings.gradle.kts        ← Gradle
├── gradle.properties          ← Gradle
├── gradle/
│   ├── wrapper/               ← Gradle wrapper
│   └── libs.versions.toml     ← Gradle version catalog
├── .mvn/                      ← Maven wrapper (if used)
├── src/                       ← shared source (both use this)
│   ├── main/
│   └── test/
├── target/                    ← Maven output
└── build/                     ← Gradle output
```

Key point: `src/` is shared — both build systems compile the same source tree. Only the output directories differ (`target/` vs `build/`).

## 3. Keeping Dependencies in Sync

This is the primary maintenance burden of dual-build. When adding or updating a dependency, both files must be updated.

### Manual approach

When adding a dependency:
1. Add to `pom.xml` (Maven)
2. Add to `gradle/libs.versions.toml` (version) and `build.gradle.kts` (usage)

### Verification script

Run both dependency trees and diff:

```bash
# Export Maven dependency tree
mvn dependency:tree -DoutputType=text -DoutputFile=deps-maven.txt

# Export Gradle dependency tree
./gradlew dependencies --configuration runtimeClasspath > deps-gradle.txt

# Compare (manual review — formats differ)
echo "=== Maven ===" && cat deps-maven.txt
echo "=== Gradle ===" && cat deps-gradle.txt
```

### CI gate

Add a CI step that builds with both systems to catch sync drift:

```yaml
# GitHub Actions example
jobs:
  maven-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: mvn verify

  gradle-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gradle/actions/setup-gradle@v4
      - run: ./gradlew build
```

## 4. CI/CD with Dual Builds

### Parallel pipelines

Run both build systems in CI to ensure parity:
- Maven pipeline: existing, unchanged
- Gradle pipeline: new, validates Gradle produces working artifacts

### Artifact comparison

For critical projects, compare the JAR/WAR output:

```bash
# Build with both
mvn package -DskipTests
./gradlew build -x test

# Compare JAR contents (ignoring timestamps)
diff <(jar tf target/my-app-1.0.jar | sort) <(jar tf build/libs/my-app-1.0.jar | sort)
```

### Gradual CI migration

1. Start: Maven is the primary CI, Gradle runs as a non-blocking validation
2. Middle: Both are required to pass
3. End: Gradle becomes primary, Maven runs as validation
4. Final: Remove Maven (if migrating)

## 5. Build Output Isolation

Maven outputs to `target/`, Gradle to `build/`. Ensure `.gitignore` covers both:

```gitignore
# Maven
target/

# Gradle
.gradle/
build/
!gradle/wrapper/gradle-wrapper.jar
!**/src/main/**/build/
!**/src/test/**/build/
```

Both can coexist without conflicts since they use separate output directories.

## 6. Gradual Migration Path

If the goal is eventual full migration:

1. **Week 1-2**: Overlay — add Gradle files, run both in CI
2. **Week 3-4**: Validation — compare artifacts, dependency trees, test results
3. **Month 2**: Developer adoption — team starts using Gradle for local builds
4. **Month 3**: CI primary — switch Gradle to the primary CI pipeline
5. **Month 4+**: Cleanup — remove pom.xml files, Maven wrapper, Maven CI jobs

This approach de-risks the migration by proving Gradle equivalence before committing.
