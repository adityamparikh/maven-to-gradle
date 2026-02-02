# Multi-Module Migration Patterns

## Table of Contents
1. Project structure mapping
2. Convention plugins (buildSrc)
3. allprojects/subprojects vs convention plugins
4. Inter-module dependencies
5. Spring Boot multi-module patterns

## 1. Project Structure Mapping

Maven multi-module:
```
parent/
├── pom.xml            (packaging: pom, <modules>)
├── module-api/
│   └── pom.xml        (<parent> → parent)
├── module-core/
│   └── pom.xml
└── module-web/
    └── pom.xml
```

Gradle equivalent:
```
parent/
├── build.gradle.kts        (root — plugins apply false)
├── settings.gradle.kts     (include subprojects)
├── gradle.properties
├── gradle/
│   └── libs.versions.toml  (shared version catalog)
├── buildSrc/               (convention plugins — optional)
│   ├── build.gradle.kts
│   └── src/main/kotlin/
│       └── java-conventions.gradle.kts
├── module-api/
│   └── build.gradle.kts
├── module-core/
│   └── build.gradle.kts
└── module-web/
    └── build.gradle.kts
```

## 2. Convention Plugins (buildSrc)

Convention plugins replace Maven's parent POM `<pluginManagement>` and shared configuration. Create `buildSrc/` for shared build logic.

### buildSrc/build.gradle.kts

```kotlin
plugins {
    `kotlin-dsl`
}

repositories {
    gradlePluginPortal()
}
```

### buildSrc/src/main/kotlin/java-conventions.gradle.kts

```kotlin
plugins {
    java
}

group = "com.example"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

tasks.withType<Test> {
    useJUnitPlatform()
}
```

### buildSrc/src/main/kotlin/spring-boot-conventions.gradle.kts

```kotlin
plugins {
    id("java-conventions")
    id("org.springframework.boot")
    id("io.spring.dependency-management")
}
```

Note: To use version catalog in buildSrc convention plugins, add to `buildSrc/settings.gradle.kts`:

```kotlin
dependencyResolutionManagement {
    versionCatalogs {
        create("libs") {
            from(files("../gradle/libs.versions.toml"))
        }
    }
}
```

### Using convention plugins in submodules

```kotlin
// module-api/build.gradle.kts
plugins {
    id("java-conventions")
}

// module-web/build.gradle.kts
plugins {
    id("spring-boot-conventions")
}
```

## 3. allprojects/subprojects vs Convention Plugins

For simple projects, `allprojects`/`subprojects` in root `build.gradle.kts` works:

```kotlin
// root build.gradle.kts
allprojects {
    group = "com.example"
    version = "1.0.0"
}

subprojects {
    apply(plugin = "java")

    repositories {
        mavenCentral()
    }

    java {
        toolchain {
            languageVersion = JavaLanguageVersion.of(21)
        }
    }

    tasks.withType<Test> {
        useJUnitPlatform()
    }
}
```

**Prefer convention plugins when:**
- Submodules need different plugin combinations (some Boot, some library-only)
- Build logic is complex (>30 lines of shared config)
- The project has 5+ modules

**Use allprojects/subprojects when:**
- All modules share identical build configuration
- The project has 2-4 modules
- Simplicity is preferred

## 4. Inter-Module Dependencies

Maven:
```xml
<dependency>
    <groupId>com.example</groupId>
    <artifactId>module-core</artifactId>
    <version>${project.version}</version>
</dependency>
```

Gradle:
```kotlin
dependencies {
    implementation(project(":module-core"))
}
```

For API/implementation separation (Gradle's `api` configuration):

```kotlin
plugins {
    `java-library`  // enables api configuration
}

dependencies {
    api(project(":module-api"))           // transitive
    implementation(project(":module-core")) // not transitive
}
```

## 5. Spring Boot Multi-Module Patterns

In Spring Boot multi-module projects, only the bootable module applies `spring-boot`:

```kotlin
// root build.gradle.kts
plugins {
    alias(libs.plugins.spring.boot) apply false
    alias(libs.plugins.spring.dependency.management) apply false
}

subprojects {
    apply(plugin = "java")
    apply(plugin = "io.spring.dependency-management")
    // dependency-management on ALL subprojects so BOMs resolve
    // spring-boot plugin only on bootable module
}
```

```kotlin
// module-core/build.gradle.kts (library module)
plugins {
    `java-library`
    // NO spring-boot plugin — this is a library, not a bootable app
}
```

```kotlin
// module-web/build.gradle.kts (bootable module)
plugins {
    alias(libs.plugins.spring.boot)
}

dependencies {
    implementation(project(":module-core"))
}
```

To produce a plain JAR from a library module (prevent Boot from repackaging):

```kotlin
// Already handled: without the spring-boot plugin, jar is plain
// If spring-boot IS applied but you want a plain jar:
tasks.bootJar { enabled = false }
tasks.jar { enabled = true }
```
