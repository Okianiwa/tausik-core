package ecs.bench;

import ecs.ArchetypeWorld;
import ecs.Scheduler;
import ecs.scene.DropChurnScene;

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

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Прод-замер СТРУКТУРНОЙ ТЕКУЧКИ (срез 7): спавн/деспавн дропа, reference vs parallel@threads.
 * Класс нагрузки, которого у стенда не было — срезы 1-6 мерялись при статичном мире.
 *
 * ЕДИНИЦА ЗАМЕРА — ЭПИЗОД ИЗ `ticks` ТИКОВ, А НЕ ОДИН ТИК, И ЭТО ВЫНУЖДЕННО.
 * Форма остальных бенчей (AverageTime, op = один тик, мир строится раз на trial) для этой сцены
 * НЕПРИМЕНИМА: id монотонны и не переиспользуются (решение #7), поэтому entityLoc растёт по числу
 * КОГДА-ЛИБО созданных, а не живых. При timeOnIteration=1s итерация прогнала бы тысячи тиков и мир
 * вырос бы на миллионы строк карты: замер упёрся бы в память, а число стало бы функцией длины окна
 * замера — поменяй 1s на 2s, и «стоимость тика» изменится. Такое число не значит ничего.
 * Здесь op = `ticks` тиков на СВЕЖЕМ мире: граница явная, объявленная и воспроизводимая.
 *
 * Отсюда же SingleShotTime: он даёт ровно одну инвокацию на итерацию, а @Setup(Level.Iteration)
 * — свежий мир под неё, и время сетапа В ЗАМЕР НЕ ВХОДИТ. Level.Invocation здесь был бы ловушкой:
 * его фикстура из замера не вычитается, а постройка+прогрев мира дороже самого тика.
 *
 * ПРОГРЕВ ДО STEADY STATE — В СЕТАПЕ, ПОЭТОМУ МЕРЯЕТСЯ УСТАНОВИВШАЯСЯ ТЕКУЧКА.
 * Сцена выходит на режим не сразу: архетип DROP рождается в фазе S первого тика, дропы копятся
 * LIFETIME тиков, первый деспавн — на тике LIFETIME+1. Без прогрева эпизод мерил бы разгон
 * (полупустой мир + разовый пересчёт раскладки), а не текучку. Прогрев идёт runReference'ом ДЛЯ
 * ОБОИХ бенчей: ref и par по контракту дают идентичное состояние (checksum(ref)==checksum(par)),
 * значит стартовый мир у них один и тот же, а не «похожий».
 */
@BenchmarkMode(Mode.SingleShotTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Benchmark)
public class ChurnBenchmark {

    /**
     * Источников дропа. 7500 × LIFETIME=20 = 150k живых дропов — тот же масштаб, что у сцены
     * блок-энтити (N=150k), и работа per-entity такая же ЛЁГКАЯ (DropMove/DropAge не зовут
     * Work.spin). Это делает сравнение со срезом 1-2 (3.21×) сопоставлением при прочих равных:
     * разница speedup = цена структурной текучки, а не разница масштаба или веса работы.
     * Текучка при этом высокая: 7500 спавнов + 7500 деспавнов за тик ≈ 9.5% мира.
     */
    @Param({"7500"})
    public int spawners;

    /** Длина измеряемого эпизода. Часть единицы замера — поэтому @Param, а не константа. */
    @Param({"100"})
    public int ticks;

    @Param({"8"})
    public int threads;

    private ExecutorService pool;
    private ArchetypeWorld world;
    private Scheduler sched;

    @Setup(Level.Trial)
    public void setupPool() {
        pool = Executors.newFixedThreadPool(threads);
    }

    /**
     * Свежий мир на каждую итерацию + вывод его на установившуюся текучку. Свежий — потому что
     * прогнанный мир необратимо вырос (id не переиспользуются), и второй эпизод на нём мерил бы
     * уже другую сцену. LIFETIME+2 тика: к этому моменту живут все LIFETIME когорт и деспавн уже
     * идёт каждый тик, то есть спавн и деспавн сбалансированы.
     */
    @Setup(Level.Iteration)
    public void freshSteadyWorld() {
        world = DropChurnScene.build(spawners);
        sched = new Scheduler(DropChurnScene.systems(), world);
        for (int t = 0; t < DropChurnScene.LIFETIME + 2; t++) sched.runReference(world);
        checkNotVacuous();
    }

    /**
     * ГАРД ВАКУУМНОГО ЗАМЕРА: сцена обязана ТЕЧЬ, иначе бенч мерит пустой тик и рапортует бодрое
     * число ни о чём. Отдельный гард, а не доверие к сцене: сломайся спавн или деспавн — тесты
     * сцены упадут, но бенч бы этого не заметил и выдал «результат». Проверяется установившийся
     * режим целиком: живых дропов ровно spawners×LIFETIME (спавн и деспавн сбалансированы), и это
     * же ловит перекос в любую сторону — не родилось ничего, или не умирает ничего.
     */
    private void checkNotVacuous() {
        int live = 0;
        for (ecs.ArchetypeStore s : world.stores())
            if (s.mask == DropChurnScene.DROP) live += s.size();
        int expect = spawners * DropChurnScene.LIFETIME;
        if (live != expect)
            throw new IllegalStateException("вакуумный/перекошенный замер: живых дропов " + live
                    + ", ожидалось " + expect + " (spawners=" + spawners
                    + " × LIFETIME=" + DropChurnScene.LIFETIME + "). Текучки нет — число не значит ничего.");
    }

    @TearDown(Level.Trial)
    public void tearDown() {
        pool.shutdownNow();
    }

    @Benchmark
    public void reference(Blackhole bh) {
        for (int t = 0; t < ticks; t++) sched.runReference(world);
        bh.consume(world.entityCount());
    }

    @Benchmark
    public void parallel(Blackhole bh) throws Exception {
        for (int t = 0; t < ticks; t++) sched.runParallel(world, pool, threads);
        bh.consume(world.entityCount());
    }
}
