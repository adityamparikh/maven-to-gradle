# Migration Gotchas & Common Pitfalls

## Table of Contents
1. Dependency scope mapping
2. Version catalog conventions
3. Spring Boot specifics
4. Resource filtering
5. Test configuration
6. Repository configuration
7. Kotlin-specific issues
8. Build performance tips
9. Verification checklist

## 1. Dependency Scope Mapping

| Maven Scope | Gradle Configuration | Notes |
|---|---|---|
| compile (default) | implementation | Not transitive to consumers. Use `api` if transitivity needed (requires `java-library` plugin) |
| provided | compileOnly | NOT on runtime classpath. For servlet API, Lombok, etc. |
| runtime | runtimeOnly | Not available at compile time |
| test | testImplementation | Test compile + runtime |
| system | compileOnly | Avoid; use file dependencies if truly needed |
| import (BOM) | platform() | `implementation(platform(libs.spring.boot.dependencies))` |

**Critical difference**: Maven's `compile` scope is transitive. Gradle's `implementation` is NOT. If downstream modules need a dependency transitively, use the `java-library` plugin and `api` configuration.

## 2. Version Catalog Conventions

**Alias naming**: Use kebab-case segments. Gradle auto-generates accessor chains from separators (`.`, `-`, `_`).

```toml
# libs.versions.toml
spring-boot-starter-web = { ... }
```
```kotlin
// Accessed as:
libs.spring.boot.starter.web
```

**Avoid aliases starting with `bundles`, `versions`, or `plugins`** — these are reserved prefixes.

**BOM dependencies** must still declare version in the catalog, even if managed:
```toml
[libraries]
spring-boot-dependencies = { group = "org.springframework.boot", name = "spring-boot-dependencies", version.ref = "spring-boot" }
```

**Version-less libraries** (managed by BOM): Omit version, but the BOM must be applied as a platform:
```kotlin
dependencies {
    implementation(platform(libs.spring.boot.dependencies))
    implementation(libs.spring.boot.starter.web) // version resolved by platform
}
```

## 3. Spring Boot Specifics

### spring-boot-starter-parent vs dependency-management plugin

Maven's `<parent>spring-boot-starter-parent</parent>` provides:
1. Dependency versions (BOM)
2. Plugin versions
3. Resource filtering with `@property@` syntax
4. Sensible defaults (encoding, Java version)

In Gradle, split across:
- `io.spring.dependency-management` plugin → manages dependency versions
- `org.springframework.boot` plugin → bootJar, bootRun tasks
- Java toolchain → Java version
- Explicit resource filtering if `@..@` tokens are used

### @..@ resource filtering

Spring Boot Maven parent configures `@` delimiters for resource filtering. Gradle does not do this automatically.

```kotlin
tasks.processResources {
    filesMatching("**/application*.properties") {
        filter<org.apache.tools.ant.filters.ReplaceTokens>(
            "tokens" to mapOf(
                "project.version" to project.version.toString(),
                "project.name" to project.name,
            )
        )
    }
}
```

Or switch to `${}` syntax in property files (Gradle default).

### DevTools

Maven auto-marks `spring-boot-devtools` as optional. In Gradle:
```kotlin
dependencies {
    developmentOnly(libs.spring.boot.devtools)
}
```

### Configuration processor

```kotlin
dependencies {
    annotationProcessor(libs.spring.boot.configuration.processor)
}
```

## 4. Resource Filtering

Maven filters `src/main/resources` by default when `<filtering>true</filtering>` is set. Gradle does not filter by default.

```kotlin
tasks.processResources {
    // Expand all properties in .properties files
    filesMatching("**/*.properties") {
        expand(project.properties)
    }
}
```

**Caution**: `expand()` uses Groovy's SimpleTemplateEngine. If resource files contain `$` characters (like Spring's `${spring.datasource.url}`), they must be escaped or use a different filtering strategy:

```kotlin
// Only expand specific tokens, leave Spring ${} alone
tasks.processResources {
    filesMatching("**/application.properties") {
        filter(
            mapOf("tokens" to mapOf("app.version" to project.version.toString())),
            org.apache.tools.ant.filters.ReplaceTokens::class.java
        )
    }
}
```

## 5. Test Configuration

### JUnit 5

Maven surefire needs explicit JUnit Platform provider. Gradle just needs:
```kotlin
tasks.withType<Test> {
    useJUnitPlatform()
}
```

### JUnit 4 + 5 (vintage)

```kotlin
dependencies {
    testRuntimeOnly("org.junit.vintage:junit-vintage-engine")
}
```

### Test resource sharing across modules

Maven shares test-jars via `<type>test-jar</type>`. Gradle approach:

```kotlin
// In the module producing test fixtures
plugins {
    `java-test-fixtures`
}

// In the consuming module
dependencies {
    testImplementation(testFixtures(project(":module-core")))
}
```

### Surefire argLine → jvmArgs

```kotlin
tasks.withType<Test> {
    jvmArgs(
        "-XX:+EnableDynamicAgentLoading",  // for Mockito on Java 21+
        "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
    )
}
```

## 6. Repository Configuration

Maven has `<repositories>` in POM. Gradle convention is to declare in `settings.gradle.kts`:

```kotlin
// settings.gradle.kts (preferred — applies to all projects and buildSrc)
dependencyResolutionManagement {
    repositoriesMode = RepositoriesMode.FAIL_ON_PROJECT_REPOS
    repositories {
        mavenCentral()
        maven { url = uri("https://repo.spring.io/milestone") }
    }
}
```

Or per-project in `build.gradle.kts`:
```kotlin
repositories {
    mavenCentral()
}
```

## 7. Kotlin-Specific Issues

### all-open plugin (for Spring)

Spring requires certain classes to be non-final. Maven uses `kotlin-allopen`. Gradle:

```kotlin
plugins {
    kotlin("plugin.spring") // alias for all-open with Spring annotations
}
```

### no-arg plugin (for JPA)

```kotlin
plugins {
    kotlin("plugin.jpa") // alias for no-arg with JPA annotations
}
```

### kapt vs annotationProcessor

If using Kotlin with annotation processors (Lombok, MapStruct):

```kotlin
plugins {
    kotlin("kapt")
}

dependencies {
    kapt(libs.mapstruct.processor)
}
```

Note: kapt is in maintenance mode. Prefer KSP where supported:

```kotlin
plugins {
    id("com.google.devtools.ksp")
}

dependencies {
    ksp(libs.some.processor)
}
```

## 8. Build Performance Tips

Enable in `gradle.properties`:
```properties
org.gradle.daemon=true
org.gradle.parallel=true
org.gradle.caching=true
org.gradle.configuration-cache=true
org.gradle.jvmargs=-Xmx4g -XX:+UseG1GC
```

For CI, add remote build cache:
```kotlin
// settings.gradle.kts
buildCache {
    local { isEnabled = true }
    remote<HttpBuildCache> {
        url = uri("https://your-cache.example.com/cache/")
        isPush = System.getenv("CI") != null
    }
}
```

## 9. Verification Checklist

After migration, verify:

1. `./gradlew build` completes successfully
2. `./gradlew test` runs all tests
3. `./gradlew bootRun` starts the application (Spring Boot)
4. `./gradlew dependencies` shows correct dependency tree
5. Compare `mvn dependency:tree` with `./gradlew dependencies` for each module
6. Artifact output matches (JAR contents, manifest entries)
7. CI pipeline works with Gradle commands
8. Resource filtering produces correct output
9. All Maven profiles have Gradle equivalents where needed
