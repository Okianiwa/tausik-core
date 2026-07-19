package ecs.bench;

import ecs.ArchetypeStore;
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
        try {
            checkNotDepleted(worldRef);
            checkNotDepleted(worldPar);
        } finally {
            pool.shutdownNow();
        }
    }

    /**
     * ГАРД ГОРИЗОНТА ГОДНОСТИ (memory #27). Хоппер шлёт команды, только пока его SLOT_OUTPUT > 0,
     * и сам же тратит по 1 за тик со стартовых 100_000 — то есть сцена ИСТОЩАЕТСЯ, а мир живёт
     * один раз на форк и мутирует через все итерации. За ~100k тиков хопперы пустеют, команды
     * исчезают, apply пустеет, и бенч мерит УЖЕ ДРУГУЮ сцену: par work=0 0.540 -> 0.216,
     * sp 2.87 -> 7.22. Мёртвая сцена стабильнее живой, поэтому ±err СУЖАЕТСЯ — привычный признак
     * мусора «широкий err» здесь не срабатывает, и ошибка выглядит как победа.
     *
     * Протокол (3x1s + 5x1s ~ 15k тиков) внутри горизонта. Гард стоит, чтобы горизонт нельзя было
     * перейти МОЛЧА: естественная реакция на шумный par — «греть дольше», и она даст красивое
     * неверное число. Тот же класс, что решение #9 для сцены текучки (там мир монотонно растёт,
     * здесь монотонно истощается), но у ChurnBenchmark он снят свежим миром на эпизод, а здесь
     * ловится гардом: перестраивать мир на итерацию тут значило бы менять единицу замера.
     */
    private static void checkNotDepleted(ArchetypeWorld w) {
        for (ArchetypeStore s : w.stores()) {
            if (s.mask != BlockEntityScene.HOPPER) continue;
            int[] inv = s.intCol(Components.INVENTORY);
            for (int r = 0; r < s.size(); r++) {
                if (inv[r * Components.SLOTS + Components.SLOT_OUTPUT] > 0) continue;
                throw new IllegalStateException("прогон вышел за горизонт годности: хоппер осушён"
                        + " (SLOT_OUTPUT=0), значит команд он больше не шлёт и замер описывает"
                        + " ДРУГУЮ сцену — пустой apply. Горизонт ~100k тиков на форк ("
                        + "SLOT_OUTPUT стартует со 100_000, -1 за тик). Сократи -wi/-w/-i/-r"
                        + " до протокольных 3x1s + 5x1s. См. memory #27.");
            }
        }
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
