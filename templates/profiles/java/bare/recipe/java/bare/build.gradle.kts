plugins {
    application
    java
}

group = "com.example.cadence"
version = "1.0.0"

repositories {
    mavenCentral()
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(17)
    }
}

dependencies {
    implementation("com.uber.cadence:cadence-client:3.13.5")

    testImplementation("org.assertj:assertj-core:3.27.3")
    testImplementation("org.junit.jupiter:junit-jupiter:5.11.4")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.11.4")
    testRuntimeOnly("org.slf4j:slf4j-simple:1.7.36")
}

application {
    mainClass = "com.example.cadence.recipe.RecipeApplication"
    applicationDefaultJvmArgs = listOf(
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.time=ALL-UNNAMED"
    )
}

tasks.test {
    useJUnitPlatform()
    jvmArgs(
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.time=ALL-UNNAMED"
    )
}

dependencyLocking {
    lockAllConfigurations()
}
