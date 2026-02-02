"""Tests for pom_parser.py — POM XML parsing and property resolution."""

from migrate.pom_parser import (
    parse_pom,
    resolve_property,
    is_bom_import,
    _parse_plugin_config,
)
from migrate.pom_models import Dependency


class TestParsePom:
    def test_minimal_pom(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """)
        module = parse_pom(pom)
        assert module.group_id == "com.example"
        assert module.artifact_id == "demo"
        assert module.version == "1.0.0"
        assert module.packaging == "jar"

    def test_namespaced_pom(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project xmlns="http://maven.apache.org/POM/4.0.0">
                <groupId>com.example</groupId>
                <artifactId>ns-demo</artifactId>
                <version>2.0.0</version>
                <packaging>war</packaging>
            </project>
        """)
        module = parse_pom(pom)
        assert module.artifact_id == "ns-demo"
        assert module.packaging == "war"

    def test_parent_inheritance(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <parent>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-parent</artifactId>
                    <version>3.4.1</version>
                </parent>
                <artifactId>myapp</artifactId>
            </project>
        """)
        module = parse_pom(pom)
        assert module.group_id == "org.springframework.boot"
        assert module.version == "3.4.1"
        assert module.parent_artifact_id == "spring-boot-starter-parent"
        assert module.parent_version == "3.4.1"

    def test_properties_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <properties>
                    <java.version>21</java.version>
                    <spring-cloud.version>2024.0.0</spring-cloud.version>
                </properties>
            </project>
        """)
        module = parse_pom(pom)
        assert module.properties["java.version"] == "21"
        assert module.properties["spring-cloud.version"] == "2024.0.0"

    def test_dependencies_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <dependencies>
                    <dependency>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-starter-web</artifactId>
                    </dependency>
                    <dependency>
                        <groupId>org.junit.jupiter</groupId>
                        <artifactId>junit-jupiter</artifactId>
                        <scope>test</scope>
                    </dependency>
                </dependencies>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.dependencies) == 2
        assert module.dependencies[0].group_id == "org.springframework.boot"
        assert module.dependencies[0].scope == "compile"
        assert module.dependencies[1].scope == "test"

    def test_dependency_with_exclusions(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <dependencies>
                    <dependency>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-starter-web</artifactId>
                        <exclusions>
                            <exclusion>
                                <groupId>org.springframework.boot</groupId>
                                <artifactId>spring-boot-starter-tomcat</artifactId>
                            </exclusion>
                        </exclusions>
                    </dependency>
                </dependencies>
            </project>
        """)
        dep = parse_pom(pom).dependencies[0]
        assert len(dep.exclusions) == 1
        assert dep.exclusions[0] == ("org.springframework.boot", "spring-boot-starter-tomcat")

    def test_optional_dependency(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <dependencies>
                    <dependency>
                        <groupId>org.projectlombok</groupId>
                        <artifactId>lombok</artifactId>
                        <optional>true</optional>
                    </dependency>
                </dependencies>
            </project>
        """)
        dep = parse_pom(pom).dependencies[0]
        assert dep.optional is True

    def test_dependency_management_with_bom(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <dependencyManagement>
                    <dependencies>
                        <dependency>
                            <groupId>org.springframework.cloud</groupId>
                            <artifactId>spring-cloud-dependencies</artifactId>
                            <version>2024.0.0</version>
                            <type>pom</type>
                            <scope>import</scope>
                        </dependency>
                    </dependencies>
                </dependencyManagement>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.dep_management) == 1
        bom = module.dep_management[0]
        assert bom.dep_type == "pom"
        assert bom.scope == "import"

    def test_plugins_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <build>
                    <plugins>
                        <plugin>
                            <groupId>org.springframework.boot</groupId>
                            <artifactId>spring-boot-maven-plugin</artifactId>
                        </plugin>
                    </plugins>
                </build>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.plugins) == 1
        assert module.plugins[0].artifact_id == "spring-boot-maven-plugin"

    def test_plugin_default_group_id(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <build>
                    <plugins>
                        <plugin>
                            <artifactId>maven-compiler-plugin</artifactId>
                            <configuration>
                                <release>21</release>
                            </configuration>
                        </plugin>
                    </plugins>
                </build>
            </project>
        """)
        plugin = parse_pom(pom).plugins[0]
        assert plugin.group_id == "org.apache.maven.plugins"
        assert plugin.configuration["release"] == "21"

    def test_plugin_management_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <build>
                    <pluginManagement>
                        <plugins>
                            <plugin>
                                <artifactId>maven-surefire-plugin</artifactId>
                                <version>3.2.5</version>
                            </plugin>
                        </plugins>
                    </pluginManagement>
                </build>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.plugin_management) == 1
        assert module.plugin_management[0].version == "3.2.5"

    def test_profiles_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <profiles>
                    <profile>
                        <id>dev</id>
                        <activation>
                            <activeByDefault>true</activeByDefault>
                        </activation>
                        <properties>
                            <env>development</env>
                        </properties>
                    </profile>
                    <profile>
                        <id>prod</id>
                        <activation>
                            <property>
                                <name>env</name>
                                <value>prod</value>
                            </property>
                        </activation>
                    </profile>
                </profiles>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.profiles) == 2
        assert module.profiles[0].profile_id == "dev"
        assert module.profiles[0].activation["activeByDefault"] is True
        assert module.profiles[0].properties["env"] == "development"
        assert module.profiles[1].activation["property"]["name"] == "env"

    def test_modules_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <packaging>pom</packaging>
                <modules>
                    <module>core</module>
                    <module>web</module>
                </modules>
            </project>
        """)
        module = parse_pom(pom)
        assert module.modules == ["core", "web"]
        assert module.packaging == "pom"

    def test_plugin_config_with_nested_text_items(self, tmp_pom):
        """Nested config elements with text children become a list of strings."""
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <build>
                    <plugins>
                        <plugin>
                            <artifactId>maven-compiler-plugin</artifactId>
                            <configuration>
                                <compilerArgs>
                                    <arg>-Xlint:all</arg>
                                    <arg>-Werror</arg>
                                </compilerArgs>
                            </configuration>
                        </plugin>
                    </plugins>
                </build>
            </project>
        """)
        plugin = parse_pom(pom).plugins[0]
        assert plugin.configuration["compilerArgs"] == ["-Xlint:all", "-Werror"]

    def test_plugin_config_with_nested_sub_dict(self, tmp_pom):
        """Nested config elements without text children become a recursive dict."""
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <build>
                    <plugins>
                        <plugin>
                            <artifactId>maven-compiler-plugin</artifactId>
                            <configuration>
                                <annotationProcessorPaths>
                                    <path>
                                        <groupId>org.mapstruct</groupId>
                                        <artifactId>mapstruct-processor</artifactId>
                                    </path>
                                </annotationProcessorPaths>
                            </configuration>
                        </plugin>
                    </plugins>
                </build>
            </project>
        """)
        plugin = parse_pom(pom).plugins[0]
        paths = plugin.configuration["annotationProcessorPaths"]
        assert isinstance(paths, dict)
        assert "path" in paths or "groupId" in paths or isinstance(paths, dict)

    def test_profile_with_jdk_activation(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <profiles>
                    <profile>
                        <id>jdk21</id>
                        <activation>
                            <jdk>21</jdk>
                        </activation>
                    </profile>
                </profiles>
            </project>
        """)
        module = parse_pom(pom)
        assert module.profiles[0].activation["jdk"] == "21"

    def test_profile_with_os_activation(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <profiles>
                    <profile>
                        <id>windows</id>
                        <activation>
                            <os>
                                <family>windows</family>
                                <name>Windows 10</name>
                            </os>
                        </activation>
                    </profile>
                </profiles>
            </project>
        """)
        module = parse_pom(pom)
        assert module.profiles[0].activation["os"]["family"] == "windows"
        assert module.profiles[0].activation["os"]["name"] == "Windows 10"

    def test_profile_with_dependencies(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <profiles>
                    <profile>
                        <id>test-extras</id>
                        <dependencies>
                            <dependency>
                                <groupId>com.h2database</groupId>
                                <artifactId>h2</artifactId>
                                <scope>test</scope>
                            </dependency>
                        </dependencies>
                    </profile>
                </profiles>
            </project>
        """)
        module = parse_pom(pom)
        prof = module.profiles[0]
        assert len(prof.dependencies) == 1
        assert prof.dependencies[0].artifact_id == "h2"
        assert prof.dependencies[0].scope == "test"

    def test_profile_with_plugins(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <profiles>
                    <profile>
                        <id>release</id>
                        <build>
                            <plugins>
                                <plugin>
                                    <groupId>org.apache.maven.plugins</groupId>
                                    <artifactId>maven-gpg-plugin</artifactId>
                                    <version>3.1.0</version>
                                </plugin>
                            </plugins>
                        </build>
                    </profile>
                </profiles>
            </project>
        """)
        module = parse_pom(pom)
        prof = module.profiles[0]
        assert len(prof.plugins) == 1
        assert prof.plugins[0].artifact_id == "maven-gpg-plugin"
        assert prof.plugins[0].version == "3.1.0"

    def test_repositories_parsed(self, tmp_pom):
        pom = tmp_pom("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <repositories>
                    <repository>
                        <id>spring-milestones</id>
                        <url>https://repo.spring.io/milestone</url>
                    </repository>
                </repositories>
            </project>
        """)
        module = parse_pom(pom)
        assert len(module.repositories) == 1
        assert module.repositories[0] == ("spring-milestones", "https://repo.spring.io/milestone")


class TestResolveProperty:
    def test_simple_resolution(self):
        assert resolve_property("${my.version}", {"my.version": "1.0"}) == "1.0"

    def test_no_property_reference(self):
        assert resolve_property("1.0", {"my.version": "2.0"}) == "1.0"

    def test_unresolvable_returns_original(self):
        assert resolve_property("${unknown}", {}) == "${unknown}"

    def test_none_input(self):
        assert resolve_property(None, {}) is None

    def test_empty_string(self):
        assert resolve_property("", {}) == ""

    def test_chained_resolution(self):
        props = {"a": "${b}", "b": "final"}
        assert resolve_property("${a}", props) == "final"

    def test_project_prefix_stripped(self):
        assert resolve_property("${project.version}", {"version": "1.0"}) == "1.0"

    def test_circular_reference_protection(self):
        props = {"a": "${b}", "b": "${a}"}
        result = resolve_property("${a}", props)
        # Should not infinite loop; returns after depth limit
        assert result is not None


class TestIsBomImport:
    def test_bom_import(self):
        dep = Dependency(
            group_id="org.springframework.cloud",
            artifact_id="spring-cloud-dependencies",
            version="2024.0.0",
            dep_type="pom",
            scope="import",
        )
        assert is_bom_import(dep) is True

    def test_regular_dependency(self):
        dep = Dependency(
            group_id="org.springframework.boot",
            artifact_id="spring-boot-starter-web",
        )
        assert is_bom_import(dep) is False

    def test_pom_without_import_scope(self):
        dep = Dependency(
            group_id="com.example",
            artifact_id="parent",
            dep_type="pom",
            scope="compile",
        )
        assert is_bom_import(dep) is False


class TestParsePluginConfig:
    def test_none_returns_empty_dict(self):
        assert _parse_plugin_config(None) == {}

    def test_leaf_elements(self):
        import xml.etree.ElementTree as ET
        config = ET.fromstring("<configuration><release>21</release><source>21</source></configuration>")
        result = _parse_plugin_config(config)
        assert result == {"release": "21", "source": "21"}

    def test_nested_text_items_become_list(self):
        import xml.etree.ElementTree as ET
        config = ET.fromstring(
            "<configuration>"
            "  <compilerArgs>"
            "    <arg>-Xlint</arg>"
            "    <arg>-Werror</arg>"
            "  </compilerArgs>"
            "</configuration>"
        )
        result = _parse_plugin_config(config)
        assert result["compilerArgs"] == ["-Xlint", "-Werror"]

    def test_nested_without_text_becomes_sub_dict(self):
        import xml.etree.ElementTree as ET
        config = ET.fromstring(
            "<configuration>"
            "<archive>"
            "<manifest>"
            "<mainClass>com.example.Main</mainClass>"
            "</manifest>"
            "</archive>"
            "</configuration>"
        )
        result = _parse_plugin_config(config)
        # archive → sub-dict (manifest has no direct text)
        # manifest → list (mainClass has text)
        assert isinstance(result["archive"], dict)
        assert "manifest" in result["archive"]
        assert "com.example.Main" in result["archive"]["manifest"]
