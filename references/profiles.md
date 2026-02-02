# Maven Profiles → Gradle Equivalents

## Table of Contents
1. Property-activated profiles
2. Default-active profiles
3. JDK-activated profiles
4. OS-activated profiles
5. Environment-specific configurations
6. Dependency profiles
7. CI/CD profiles

## 1. Property-Activated Profiles

**Maven:**
```xml
<profile>
    <id>integration-tests</id>
    <activation>
        <property><name>it</name></property>
    </activation>
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-failsafe-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</profile>
```
Activated with: `mvn verify -Dit`

**Gradle:**
```kotlin
if (project.hasProperty("it")) {
    testing {
        suites {
            val integrationTest by registering(JvmTestSuite::class) {
                useJUnitJupiter()
            }
        }
    }
    tasks.named("check") {
        dependsOn(testing.suites.named("integrationTest"))
    }
}
```
Activated with: `./gradlew check -Pit`

## 2. Default-Active Profiles

**Maven:**
```xml
<profile>
    <id>dev</id>
    <activation>
        <activeByDefault>true</activeByDefault>
    </activation>
    <properties>
        <spring.profiles.active>dev</spring.profiles.active>
    </properties>
</profile>
```

**Gradle** — use gradle.properties for defaults, override with `-P`:
```properties
# gradle.properties
springProfile=dev
```

```kotlin
// build.gradle.kts
val springProfile: String by project  // reads from gradle.properties

tasks.bootRun {
    systemProperty("spring.profiles.active", springProfile)
}
```
Override: `./gradlew bootRun -PspringProfile=prod`

## 3. JDK-Activated Profiles

**Maven:**
```xml
<profile>
    <id>jdk17-args</id>
    <activation>
        <jdk>[17,)</jdk>
    </activation>
    <properties>
        <jvm.args>--add-opens java.base/java.lang=ALL-UNNAMED</jvm.args>
    </properties>
</profile>
```

**Gradle:**
```kotlin
val javaVersion = JavaVersion.current()
if (javaVersion >= JavaVersion.VERSION_17) {
    tasks.withType<Test> {
        jvmArgs("--add-opens", "java.base/java.lang=ALL-UNNAMED")
    }
}
```

## 4. OS-Activated Profiles

**Maven:**
```xml
<profile>
    <id>mac</id>
    <activation>
        <os><family>mac</family></os>
    </activation>
    <dependencies>
        <dependency>
            <groupId>io.netty</groupId>
            <artifactId>netty-resolver-dns-native-macos</artifactId>
            <classifier>osx-aarch_64</classifier>
        </dependency>
    </dependencies>
</profile>
```

**Gradle:**
```kotlin
val osName = System.getProperty("os.name").lowercase()
val osArch = System.getProperty("os.arch")

if (osName.contains("mac")) {
    dependencies {
        runtimeOnly("io.netty:netty-resolver-dns-native-macos:${libs.versions.netty.get()}:osx-aarch_64")
    }
}
```

## 5. Environment-Specific Configurations (Dev/Staging/Prod)

**Maven:**
```xml
<profiles>
    <profile>
        <id>dev</id>
        <properties>
            <db.url>jdbc:h2:mem:devdb</db.url>
        </properties>
    </profile>
    <profile>
        <id>prod</id>
        <properties>
            <db.url>jdbc:postgresql://prod-host/db</db.url>
        </properties>
    </profile>
</profiles>
```

**Gradle** — prefer Spring profiles over build-time property injection:

```kotlin
// build.gradle.kts
val env = project.findProperty("env")?.toString() ?: "dev"

tasks.processResources {
    filesMatching("application.properties") {
        expand("env" to env)
    }
}

tasks.bootRun {
    systemProperty("spring.profiles.active", env)
}
```

Or better — use Spring Boot's own profile mechanism with `application-dev.yml`, `application-prod.yml` and no build-time switching.

## 6. Dependency Profiles

**Maven:**
```xml
<profile>
    <id>with-testcontainers</id>
    <dependencies>
        <dependency>
            <groupId>org.testcontainers</groupId>
            <artifactId>postgresql</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>
</profile>
```

**Gradle** — use feature variants or simple property checks:

```kotlin
if (project.hasProperty("withTestcontainers")) {
    dependencies {
        testImplementation(libs.testcontainers.postgresql)
    }
}
```

For reusable feature-based dependencies, use Gradle's feature variants:

```kotlin
java {
    registerFeature("testcontainersSupport") {
        usingSourceSet(sourceSets["test"])
    }
}

dependencies {
    "testcontainersSupportImplementation"(libs.testcontainers.postgresql)
}
```

## 7. CI/CD Profiles

**Maven:**
```xml
<profile>
    <id>ci</id>
    <activation>
        <property><name>env.CI</name></property>
    </activation>
    <properties>
        <maven.test.failure.ignore>false</maven.test.failure.ignore>
    </properties>
</profile>
```

**Gradle:**
```kotlin
val isCi = System.getenv("CI") != null

tasks.withType<Test> {
    if (isCi) {
        maxParallelForks = Runtime.getRuntime().availableProcessors()
        testLogging {
            events("passed", "skipped", "failed")
            showStandardStreams = true
        }
    }
}
```
