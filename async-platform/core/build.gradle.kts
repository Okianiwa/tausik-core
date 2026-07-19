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
    // fork=5, НЕ 1. При fork=1 бенч видит РОВНО ОДИН JIT-профиль, поэтому межфорковая дисперсия
    // (нестабильность компиляции, раскладка кода, состояние GC) в ±error не попадает — репортится
    // только внутрифорковый разброс. Отсюда узкие ±err при невоспроизводимых числах: замер сессии #4
    // дал на ОДНОМ коммите дрейф speedup -10.6%..+22.6% между двумя прогонами, то есть в разы больше
    // порога ±5%, который стережёт AC #8. Форки усредняют профили — без этого порог не разрешим.
    fork = 5
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

tasks.register<JavaExec>("redstoneDemo") {
    group = "application"
    mainClass = "ecs.RedstoneDemo"
    classpath = sourceSets["main"].runtimeClasspath
}
