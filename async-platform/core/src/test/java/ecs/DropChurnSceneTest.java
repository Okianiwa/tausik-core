package ecs;

import ecs.scene.DropChurnScene;
import org.junit.jupiter.api.Test;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Сцена структурной текучки — ВХОД для среза 7 (structural-changes-benchmark).
 * Задача этих тестов: сцена обязана быть ЗЕЛЁНОЙ и ОСМЫСЛЕННОЙ до всякого замера. Мерить перф на
 * несходящейся семантике — потратить сессию на числа, которые ничего не значат.
 */
class DropChurnSceneTest {

    private static final int SPAWNERS = 24;

    private static long run(int ticks, int threads) throws Exception {
        ArchetypeWorld w = DropChurnScene.build(SPAWNERS);
        Scheduler s = new Scheduler(DropChurnScene.systems(), w);
        if (threads == 0) {
            for (int t = 0; t < ticks; t++) s.runReference(w);
        } else {
            ExecutorService p = Executors.newFixedThreadPool(threads);
            try {
                for (int t = 0; t < ticks; t++) s.runParallel(w, p, threads);
            } finally {
                p.shutdown();
            }
        }
        return w.checksum();
    }

    /**
     * Раскладка сцены — ОДНА стадия. Проверяется, а не подразумевается: DropSpawn и DropMove
     * конфликтуют ПО КОМПОНЕНТАМ (обе трогают POSITION), и если бы правило конфликта смотрело
     * только на компоненты, стадий было бы две, а параллелизм сцены — вдвое меньше заявленного.
     * Спасает вторая половина правила (decision #3): архетипы SPAWNER и DROP не пересекаются.
     */
    @Test
    void layoutIsSingleStage() {
        ArchetypeWorld w = DropChurnScene.build(SPAWNERS);
        Scheduler s = new Scheduler(DropChurnScene.systems(), w);
        assertEquals(1, s.stages.size(),
                "Spawn и Move трогают POSITION, но матчат разные архетипы → конфликта нет");

        for (int t = 0; t < 3; t++) s.runReference(w); // родился DROP — новый архетип
        assertEquals(1, s.stages.size(),
                "рождение DROP не должно склеить системы в конфликт: у SPAWNER нет VELOCITY, "
                        + "у DROP нет SOURCE — пересечения архетипов так и не возникло");
    }

    /** Архетип DROP рождается на лету, а не при сборке — сцена проходит настоящий путь. */
    @Test
    void dropArchetypeIsBornOnTheFly() {
        ArchetypeWorld w = DropChurnScene.build(SPAWNERS);
        assertEquals(1, w.storeCount(), "при сборке есть только SPAWNER");

        new Scheduler(DropChurnScene.systems(), w).runReference(w);
        assertEquals(2, w.storeCount(), "DROP родился в фазе S первого тика");
    }

    /** Текучка реальна: за тик рождается SPAWNERS дропов, и в установившемся режиме столько же умирает. */
    @Test
    void churnReachesSteadyState() {
        ArchetypeWorld w = DropChurnScene.build(SPAWNERS);
        Scheduler s = new Scheduler(DropChurnScene.systems(), w);

        for (int t = 0; t < DropChurnScene.LIFETIME + 5; t++) s.runReference(w);

        int alive = 0;
        for (int id = 0; id < w.entityCount(); id++) if (w.isAlive(id)) alive++;
        int drops = alive - SPAWNERS;

        assertEquals(SPAWNERS * DropChurnScene.LIFETIME, drops,
                "установившееся состояние = источники × время жизни: спавн и деспавн сошлись");
        assertTrue(w.entityCount() > drops + SPAWNERS,
                "id монотонны и не переиспользуются → счётчик обогнал число живых. "
                        + "Равенство означало бы, что id пошли по кругу (decision #7 нарушен)");
    }

    /** AC #4: детерминизм на текучке — там, где каждый тик двигает строки и растит карту. */
    @Test
    void churnIsDeterministicAcrossModesAndThreadCounts() throws Exception {
        long ref = run(25, 0);
        assertEquals(ref, run(25, 8), "par@8 обязан воспроизвести reference бит-в-бит на текучке");
        assertEquals(ref, run(25, 4), "число потоков не меняет результат");
        assertEquals(run(25, 8), run(25, 8), "воспроизводимо между прогонами");
    }

    /**
     * Дропы действительно живут и умирают, а не «спавнятся и копятся».
     *
     * Арифметика жизни (не интуитивная, поэтому записана): дроп рождается в фазе S тика T, а стареть
     * начинает с тика T+1 — в тике T его архетипа ещё нет в matched (decision #6, новый архетип виден
     * с T+1). Значит HEALTH=LIFETIME обнуляется на тике T+LIFETIME, и первый деспавн наступает на
     * тике LIFETIME+1, а не LIFETIME. Ровно на этом и упала первая версия теста — ожидание было
     * неверным, код прав.
     */
    @Test
    void dropsActuallyDespawn() {
        ArchetypeWorld w = DropChurnScene.build(1);
        Scheduler s = new Scheduler(DropChurnScene.systems(), w);

        int ticks = DropChurnScene.LIFETIME + 2;
        for (int t = 0; t < ticks; t++) s.runReference(w);
        assertEquals(ticks, w.entityCount() - 1, "один источник — один дроп за тик");

        int dead = 0;
        for (int id = 1; id < w.entityCount(); id++) if (!w.isAlive(id)) dead++;
        assertTrue(dead > 0, "к тику LIFETIME+1 первые дропы обязаны были деспавниться, "
                + "иначе сцена мерит накопление, а не текучку");
        assertEquals(1, w.entityCount() - 1 - dead - DropChurnScene.LIFETIME + 1,
                "живых дропов = LIFETIME (по одному на каждый тик жизни), остальные деспавнены");
    }
}
