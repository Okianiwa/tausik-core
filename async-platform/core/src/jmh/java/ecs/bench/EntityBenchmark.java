package ecs.bench;

import ecs.GameSystem;
import ecs.Scheduler;
import ecs.ArchetypeWorld;
import ecs.Components;
import ecs.scene.EntityScene;

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
 * Прод-замер грязной подсистемы (стая мобов): reference vs parallel. Per-entity компьют тяжелее
 * блок-энтити (K neighbor-чтений + sqrt) → по выводу среза 3 ожидается хорошая масштабируемость,
 * несмотря на 2 стадии от грязных r/w по POSITION.
 */
@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Benchmark)
public class EntityBenchmark {

    @Param({"150000"})
    public int n;

    @Param({"8"})
    public int threads;

    private Scheduler sched;
    private ArchetypeWorld worldRef;
    private ArchetypeWorld worldPar;
    private ExecutorService pool;

    @Setup(Level.Trial)
    public void setup() {
        List<GameSystem> systems = EntityScene.systems();
        worldRef = EntityScene.build(n);
        worldPar = EntityScene.build(n);
        sched = new Scheduler(systems, worldRef);
        pool = Executors.newFixedThreadPool(threads);
    }

    @TearDown(Level.Trial)
    public void tearDown() {
        pool.shutdownNow();
    }

    /** POSITION.x энтити 0 — дешёвый якорь, чтобы JIT не выбросил тик. */
    private static double posXOfFirst(ArchetypeWorld w) {
        return w.storeOf(0).doubleCol(Components.POSITION)[w.rowOf(0) * 2 + Components.LANE_X];
    }

    @Benchmark
    public void reference(Blackhole bh) {
        sched.runReference(worldRef);
        bh.consume(posXOfFirst(worldRef));
    }

    @Benchmark
    public void parallel(Blackhole bh) throws Exception {
        sched.runParallel(worldPar, pool, threads);
        bh.consume(posXOfFirst(worldPar));
    }
}
