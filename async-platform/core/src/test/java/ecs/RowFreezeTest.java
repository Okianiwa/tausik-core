package ecs;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * ИНВАРИАНТ C (AC #4): пока стадия исполняется, карта архетип→строки заморожена; строки двигаются
 * только на барьере. Инвариант несущий — на нём стоит корректность планировщика: он пустил системы
 * параллельно, доказав непересечение архетипов, а системы адресуют соседей по localRow. Переставь
 * строку под ними — и оба факта разом станут ложью, ТИХО, без единого исключения.
 *
 * Механизм миграции покрыт ArchetypeWorldTest (состояние, карта, checksum). ЗДЕСЬ — только гард:
 * что нарушение падает громко, а не портит данные.
 */
class RowFreezeTest {

    private static final long MOBISH = Components.mask(Components.HEALTH, Components.POSITION);
    private static final long MOBISH_PLUS = Components.mask(
            Components.HEALTH, Components.POSITION, Components.ENERGY);

    private static ArchetypeWorld world(int n) {
        ArchetypeWorld w = new ArchetypeWorld(Components.standard(), Math.max(1, n));
        for (int i = 0; i < n; i++) w.createEntity(MOBISH);
        return w;
    }

    /** Система, которая мигрирует энтити 0 прямо из run() — то есть внутри исполняющейся стадии. */
    private static GameSystem migrator(ArchetypeWorld w) {
        return new GameSystem() {
            public String name() { return "Migrator"; }
            public long reads()  { return Components.mask(Components.HEALTH); }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) {
                if (v.entityAt(row) == 0) w.migrate(0, MOBISH_PLUS);
            }
        };
    }

    @Test
    void migrationInsideReferenceStageThrows() {
        ArchetypeWorld w = world(4);
        Scheduler s = new Scheduler(List.of(migrator(w)), w);
        ContractViolation e = assertThrows(ContractViolation.class, () -> s.runReference(w));
        assertTrue(e.getMessage().contains("инвариант C"),
                "сообщение обязано называть нарушенный инвариант, а не просто падать: " + e.getMessage());
    }

    /** То же в параллельном пути: исключение из задачи приходит завёрнутым в ExecutionException. */
    @Test
    void migrationInsideParallelStageThrows() throws Exception {
        ArchetypeWorld w = world(64);
        Scheduler s = new Scheduler(List.of(migrator(w)), w);
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            ExecutionException e = assertThrows(ExecutionException.class, () -> s.runParallel(w, pool, 4));
            assertInstanceOf(ContractViolation.class, e.getCause());
        } finally {
            pool.shutdown();
        }
    }

    /**
     * СТРАХОВКА ОТ ВАКУУМНОСТИ: без неё оба теста выше прошли бы и в случае, если migrate() ВСЕГДА
     * бросает — например если гард залип на true и заморозка не снимается на барьере. Тогда «зелёные»
     * негатив-тесты означали бы сломанную миграцию, а не работающий гард.
     */
    @Test
    void migrationOutsideStageIsAllowedAndThawed() {
        ArchetypeWorld w = world(4);
        Scheduler s = new Scheduler(List.of(migrator(w)), w);

        assertFalse(w.rowsFrozen(), "вне стадии строки разморожены");
        assertDoesNotThrow(() -> w.migrate(1, MOBISH_PLUS), "на барьере миграция разрешена");

        // Прогон упал на гарде — заморозка обязана сняться через finally, а не остаться висеть.
        assertThrows(ContractViolation.class, () -> s.runReference(w));
        assertFalse(w.rowsFrozen(), "после падения стадии строки разморожены (finally)");
        assertDoesNotThrow(() -> w.migrate(2, MOBISH_PLUS), "миграция снова разрешена");
    }

    /** Заморожен весь параллельный путь, а не только его начало: флаг виден рабочему потоку. */
    @Test
    void rowsAreFrozenWhileTaskRuns() throws Exception {
        ArchetypeWorld w = world(64);
        boolean[] seenFrozen = new boolean[1];
        GameSystem probe = new GameSystem() {
            public String name() { return "Probe"; }
            public long reads()  { return Components.mask(Components.HEALTH); }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) {
                if (w.rowsFrozen()) seenFrozen[0] = true;
            }
        };
        Scheduler s = new Scheduler(List.of(probe), w);
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            s.runParallel(w, pool, 4);
        } finally {
            pool.shutdown();
        }
        assertTrue(seenFrozen[0], "внутри задачи строки обязаны быть заморожены");
        assertFalse(w.rowsFrozen(), "после барьера — разморожены");
    }
}
