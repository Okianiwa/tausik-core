plugins {
    java
    id("me.champeau.jmh") version "0.7.2"
}

repositories { mavenCentral() }

// JDK 17 на машине — таргетим 17 без toolchain-автопровижена (фичи 21 не нужны).
java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

// Исходники в UTF-8; на этой Windows javac иначе берёт cp1251 и падает на кириллице.
tasks.withType<JavaCompile>().configureEach { options.encoding = "UTF-8" }
tasks.withType<JavaExec>().configureEach { jvmArgs("-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8") }

dependencies {
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
    testLogging { events("passed", "skipped", "failed") }
}

// Прод-замер (kill-критерий Phase 0 требовал JMH вместо ручного бенча).
jmh {
    warmupIterations = 3
    iterations = 5
    fork = 1
    warmup = "1s"
    timeOnIteration = "1s"
    timeUnit = "ms"
    resultFormat = "TEXT"
    resultsFile = project.layout.buildDirectory.file("results/jmh/results.txt").get().asFile
}

// CLI-раннер: раскладка по стадиям + чек детерминизма ref==parallel.
tasks.register<JavaExec>("demo") {
    group = "application"
    mainClass = "ecs.Main"
    classpath = sourceSets["main"].runtimeClasspath
}

tasks.register<JavaExec>("entityDemo") {
    group = "application"
    mainClass = "ecs.EntityDemo"
    classpath = sourceSets["main"].runtimeClasspath
}
