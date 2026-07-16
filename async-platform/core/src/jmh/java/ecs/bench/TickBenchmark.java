package ecs.bench;

import ecs.GameSystem;
import ecs.Scheduler;
import ecs.ArchetypeWorld;
import ecs.Components;
import ecs.scene.BlockEntityScene;

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

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Прод-замер стоимости одного тика: reference (1 поток) vs parallel (@threads).
 * speedup = time(reference)/time(parallel). Плотная сцена блок-энтити — реальная логика,
 * а не toy-мешалка Phase 0. Пул размерять по ФИЗ.ядрам (находка Phase 0: SMT хуже).
 */
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Benchmark)
public class TickBenchmark {

    @Param({"150000"})
    public int n;

    @Param({"8"})
    public int threads;

    @Param({"0", "64"})
    public int work;

    private Scheduler sched;
    private ArchetypeWorld worldRef;
    private ArchetypeWorld worldPar;
    private ExecutorService pool;

    @Setup(Level.Trial)
    public void setup() {
        ecs.Work.WEIGHT = work;
        List<GameSystem> systems = BlockEntityScene.systems();
        worldRef = BlockEntityScene.build(n);
        worldPar = BlockEntityScene.build(n);
        sched = new Scheduler(systems, worldRef);
        pool = Executors.newFixedThreadPool(threads);
    }

    @TearDown(Level.Trial)
    public void tearDown() {
        pool.shutdownNow();
    }

    /** PROGRESS энтити 0 (печь) — дешёвый якорь, чтобы JIT не выбросил тик. */
    private static int progressOfFirst(ArchetypeWorld w) {
        return w.storeOf(0).intCol(Components.PROGRESS)[w.rowOf(0)];
    }

    @Benchmark
    public void reference(Blackhole bh) {
        sched.runReference(worldRef);
        bh.consume(progressOfFirst(worldRef));
    }

    @Benchmark
    public void parallel(Blackhole bh) throws Exception {
        sched.runParallel(worldPar, pool, threads);
        bh.consume(progressOfFirst(worldPar));
    }
}
