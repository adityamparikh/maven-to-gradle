# Maven Plugin → Gradle Mappings

## Direct Plugin Mappings

| Maven Plugin | Gradle Plugin ID | Catalog Alias |
|---|---|---|
| spring-boot-maven-plugin | org.springframework.boot | spring-boot |
| kotlin-maven-plugin | org.jetbrains.kotlin.jvm | kotlin-jvm |
| jib-maven-plugin | com.google.cloud.tools.jib | jib |
| jacoco-maven-plugin | jacoco | jacoco |
| maven-checkstyle-plugin | checkstyle | checkstyle |
| maven-pmd-plugin | pmd | pmd |
| spotbugs-maven-plugin | com.github.spotbugs | spotbugs |
| spotless-maven-plugin | com.diffplug.spotless | spotless |
| maven-shade-plugin | com.github.johnrengelman.shadow | shadow |
| maven-war-plugin | war | (built-in) |
| flyway-maven-plugin | org.flywaydb.flyway | flyway |
| openapi-generator-maven-plugin | org.openapi.generator | openapi-generator |
| git-commit-id-plugin | com.gorylenko.gradle-git-properties | git-properties |
| asciidoctor-maven-plugin | org.asciidoctor.jvm.convert | asciidoctor |
| jooq-codegen-maven | nu.studer.jooq | jooq |

## Plugins Handled by Gradle Conventions (No Plugin Needed)

### maven-compiler-plugin → Java Toolchain

```kotlin
java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}
```

### maven-surefire-plugin → Test Task

```kotlin
tasks.withType<Test> {
    useJUnitPlatform()
    jvmArgs("-XX:+EnableDynamicAgentLoading") // common for Mockito on Java 21+
    testLogging {
        events("passed", "skipped", "failed")
    }
}
```

### maven-failsafe-plugin → Integration Test Suite

```kotlin
testing {
    suites {
        val integrationTest by registering(JvmTestSuite::class) {
            useJUnitJupiter()
            dependencies {
                implementation(project())
            }
        }
    }
}

tasks.named("check") {
    dependsOn(testing.suites.named("integrationTest"))
}
```

### maven-source-plugin / maven-javadoc-plugin

```kotlin
java {
    withSourcesJar()
    withJavadocJar()
}
```

### maven-jar-plugin → Jar Task

```kotlin
tasks.jar {
    manifest {
        attributes(
            "Implementation-Title" to project.name,
            "Implementation-Version" to project.version,
        )
    }
}
```

### maven-resources-plugin → ProcessResources

```kotlin
tasks.processResources {
    filesMatching("application*.yml") {
        expand(project.properties)
    }
}
```

Gradle resource filtering uses `${}` by default. To match Maven's `@property@` syntax (common with Spring Boot):

```kotlin
tasks.processResources {
    filesMatching("application*.properties") {
        filter<org.apache.tools.ant.filters.ReplaceTokens>(
            "tokens" to mapOf("project.version" to project.version.toString())
        )
    }
}
```

### maven-deploy-plugin → Maven Publish

```kotlin
plugins {
    `maven-publish`
}

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            from(components["java"])
        }
    }
    repositories {
        maven {
            url = uri("https://your-repo.example.com/releases")
            credentials {
                username = project.findProperty("repoUser")?.toString()
                password = project.findProperty("repoPassword")?.toString()
            }
        }
    }
}
```

## Plugins With No Direct Equivalent

| Maven Plugin | Gradle Approach |
|---|---|
| maven-enforcer-plugin | Use Java toolchain for JDK enforcement. For dependency convergence: `configurations.all { resolutionStrategy.failOnVersionConflict() }` |
| maven-release-plugin | Use gradle-release plugin (`net.researchgate.release`) or CI-based release workflows |
| versions-maven-plugin | Version catalog + Dependabot/Renovate. Also: `./gradlew dependencyUpdates` with ben-manes plugin |
| flatten-maven-plugin | Not needed — Gradle doesn't have parent POM flattening issues |
| maven-dependency-plugin | `./gradlew dependencies`, `./gradlew dependencyInsight --dependency <dep>` |
