package ecs.bench;

import ecs.RedstoneCascade;
import ecs.Scheduler;
import ecs.ArchetypeStore;
import ecs.ArchetypeWorld;
import ecs.Components;
import ecs.scene.RedstoneScene;

import org.openjdk.jmh.annotations.Benchmark;
import org.openjdk.jmh.annotations.BenchmarkMode;
import org.openjdk.jmh.annotations.Level;
import org.openjdk.jmh.annotations.Mode;
import org.openjdk.jmh.annotations.OutputTimeUnit;
import org.openjdk.jmh.annotations.Param;
import org.openjdk.jmh.annotations.Scope;
import org.openjdk.jmh.annotations.Setup;
import org.openjdk.jmh.annotations.State;
import org.openjdk.jmh.annotations.TearDown;
import org.openjdk.jmh.infra.Blackhole;

import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Прод-замер ПОЛНОГО fixpoint-каскада (не одного тика): reference vs parallel. Каждый проход
 * data-parallel, но между проходами barrier+swap → серийная глубина. Проверяет, окупается ли
 * параллелизм при большой глубине каскада на плотной сетке.
 */
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Benchmark)
public class RedstoneBenchmark {

    @Param({"400"})
    public int grid;

    @Param({"8"})
    public int threads;

    private Scheduler sched;
    private ArchetypeWorld wRef;
    private ArchetypeWorld wPar;
    private ExecutorService pool;

    @Setup(Level.Trial)
    public void setup() {
        wRef = RedstoneScene.build(grid, grid);
        wPar = RedstoneScene.build(grid, grid);
        sched = new Scheduler(RedstoneScene.systems(grid), wRef);
        pool = Executors.newFixedThreadPool(threads);
    }

    @Setup(Level.Invocation)
    public void reset() {
        resetPower(wRef);
        resetPower(wPar);
    }

    @TearDown(Level.Trial)
    public void tearDown() {
        pool.shutdownNow();
    }

    /** Сброс каскада между инвокациями: SOURCE сохраняется, буферы POWER обнуляются. */
    private static void resetPower(ArchetypeWorld w) {
        for (ArchetypeStore s : w.stores()) {
            if (!s.has(Components.POWER)) continue;
            Arrays.fill(s.intCol(Components.POWER), 0);
            Arrays.fill(s.intCol(Components.POWER_NEXT), 0);
        }
    }

    @Benchmark
    public void reference(Blackhole bh) {
        bh.consume(RedstoneCascade.runReference(wRef, sched));
    }

    @Benchmark
    public void parallel(Blackhole bh) throws Exception {
        bh.consume(RedstoneCascade.runParallel(wPar, sched, pool, threads));
    }
}
