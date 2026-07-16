package ecs;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Структурные изменения через CommandBuffer: create/destroy энтити, add/remove компонента.
 *
 * Покрывает решения #6 (раскладка — пересчёт в НАЧАЛЕ тика, мемо по упорядоченному списку масок),
 * #7 (provisional-handle + монотонные id без переиспользования), #8 (порядок — эффекты, затем
 * структурные; «удаление побеждает внутри барьера, ссылка на удалённое раньше — ошибка»).
 *
 * Существующие 45 тестов структурных операций не касаются вообще — до этого файла весь новый код
 * ни разу не исполнялся.
 */
class StructuralChangesTest {

    // SOURCE — маркер архетипа спавнера: без него query спавнера матчил бы и порождаемый архетип
    // (DROP ⊇ {HEALTH}), и спавнер плодил бы дропы от дропов.
    private static final long SPAWNER = Components.mask(Components.HEALTH, Components.SOURCE);
    private static final long DROP    = Components.mask(Components.HEALTH, Components.INVENTORY);

    private static ArchetypeWorld world(long... masks) {
        ArchetypeWorld w = new ArchetypeWorld(Components.standard(), Math.max(1, masks.length));
        for (long m : masks) w.createEntity(m);
        return w;
    }

    private static ExecutorService pool(int n) { return Executors.newFixedThreadPool(n); }

    /** Спавнит одну DROP-энтити на каждую свою строку за тик, с начальным HEALTH=7. */
    private static GameSystem spawner() {
        return new GameSystem() {
            public String name() { return "Spawner"; }
            public long reads()  { return SPAWNER; }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) {
                int h = cb.create(DROP);
                cb.init(h, Components.HEALTH, 0, 7);
            }
        };
    }

    // ---------- ИНВАРИАНТ C: гард на КАЖДОЙ новой структурной операции (AC #2) ----------

    /** Система, дёргающая структурную операцию ПРЯМО из run() — то есть внутри исполняющейся стадии. */
    private static GameSystem rogue(Runnable structuralOp) {
        return new GameSystem() {
            public String name() { return "Rogue"; }
            public long reads()  { return Components.mask(Components.HEALTH); }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) {
                if (v.entityAt(row) == 0) structuralOp.run();
            }
        };
    }

    private static void assertGuarded(java.util.function.Function<ArchetypeWorld, Runnable> op, String what) {
        ArchetypeWorld w = world(DROP, DROP);
        Scheduler s = new Scheduler(List.of(rogue(op.apply(w))), w);
        ContractViolation e = assertThrows(ContractViolation.class, () -> s.runReference(w),
                what + " внутри стадии обязана падать");
        assertTrue(e.getMessage().contains("инвариант C"),
                "сообщение обязано называть нарушенный инвариант: " + e.getMessage());
        assertFalse(w.rowsFrozen(), "после падения заморозка снимается через finally");
    }

    @Test
    void createInsideStageThrows() {
        assertGuarded(w -> () -> w.createEntity(DROP), "createEntity");
    }

    @Test
    void destroyInsideStageThrows() {
        assertGuarded(w -> () -> w.destroyEntity(1), "destroyEntity");
    }

    @Test
    void addComponentInsideStageThrows() {
        assertGuarded(w -> () -> w.addComponent(1, Components.ENERGY), "addComponent");
    }

    @Test
    void removeComponentInsideStageThrows() {
        assertGuarded(w -> () -> w.removeComponent(1, Components.INVENTORY), "removeComponent");
    }

    /**
     * СТРАХОВКА ОТ ВАКУУМНОСТИ: без неё все четыре теста выше прошли бы и в случае, если операции
     * ВСЕГДА бросают (например гард залип на true). Тогда «зелёные» негатив-тесты означали бы
     * сломанные структурные изменения, а не работающий гард.
     */
    @Test
    void structuralOpsWorkOutsideStage() {
        ArchetypeWorld w = world(DROP, DROP);
        assertFalse(w.rowsFrozen(), "вне стадии строки разморожены");
        assertDoesNotThrow(() -> w.createEntity(DROP), "createEntity вне стадии разрешён");
        assertDoesNotThrow(() -> w.addComponent(1, Components.ENERGY), "addComponent вне стадии разрешён");
        assertDoesNotThrow(() -> w.removeComponent(1, Components.ENERGY), "removeComponent вне стадии разрешён");
        assertDoesNotThrow(() -> w.destroyEntity(1), "destroyEntity вне стадии разрешён");
        assertFalse(w.isAlive(1), "удалённая мертва");
    }

    /** Гард держит и параллельный путь: исключение из задачи приходит завёрнутым. */
    @Test
    void createInsideParallelStageThrows() throws Exception {
        ArchetypeWorld w = world(DROP, DROP, DROP, DROP);
        Scheduler s = new Scheduler(List.of(rogue(() -> w.createEntity(SPAWNER))), w);
        ExecutorService p = pool(4);
        try {
            ExecutionException e = assertThrows(ExecutionException.class, () -> s.runParallel(w, p, 4));
            assertInstanceOf(ContractViolation.class, e.getCause());
        } finally {
            p.shutdown();
        }
    }

    // ---------- CREATE через буфер: материализация и начальные значения (решения #7, #8) ----------

    @Test
    void createThroughBufferMaterializesOnBarrierWithInitialValues() {
        ArchetypeWorld w = world(SPAWNER);
        Scheduler s = new Scheduler(List.of(spawner()), w);
        assertEquals(1, w.entityCount(), "до тика — только спавнер");

        s.runReference(w);

        assertEquals(2, w.entityCount(), "create применился на барьере");
        assertTrue(w.isAlive(1));
        assertEquals(DROP, w.storeOf(1).mask, "родилась в заказанном архетипе");
        assertEquals(7, w.storeOf(1).intCol(Components.HEALTH)[w.rowOf(1)],
                "начальное значение доехало ВНУТРИ OP_CREATE: отдельным set() его не задать — "
                        + "в фазе эффектов энтити ещё не существует");
    }

    /**
     * Решение #6: архетип, рождённый в тике T, виден системам с тика T+1. Это не «задержка-баг»,
     * а семантика — раскладка тика T построена без него, и ходить по живому storeCount значило бы
     * AIOOBE (Scheduler:93/:117 сайзились по matched, снятому со старого storeCount).
     */
    @Test
    void newArchetypeIsInvisibleUntilNextTickAndDoesNotCrash() {
        ArchetypeWorld w = world(SPAWNER);
        // Спавнер матчит {HEALTH,SOURCE}; DROP-архетипа в мире ещё НЕТ — родится в фазе S.
        Scheduler s = new Scheduler(List.of(spawner()), w);

        assertDoesNotThrow(() -> s.runReference(w), "новый архетип не роняет прогон (AIOOBE снят)");
        assertEquals(2, w.entityCount(), "тик 1: спавнер отработал один раз");

        s.runReference(w);
        assertEquals(3, w.entityCount(),
                "тик 2: спавнер по-прежнему один — дроп НЕ спавнит дропов, значит новый архетип "
                        + "не подмешался спавнеру в матч");
    }

    // ---------- РАСКЛАДКА: «не поехала молча» (AC #3, решение #6) ----------

    /** Пишет ENERGY на архетипах, где есть {ENERGY, HEALTH}. */
    private static GameSystem energyWriter() {
        return new GameSystem() {
            public String name() { return "EnergyWriter"; }
            public long reads()  { return Components.mask(Components.HEALTH); }
            public long writes() { return Components.mask(Components.ENERGY); }
            public void run(View v, int row, CommandBuffer cb) { v.setLong(Components.ENERGY, row, 1); }
        };
    }

    /** Читает ENERGY на архетипах, где есть {ENERGY, POSITION}. */
    private static GameSystem energyReader() {
        return new GameSystem() {
            public String name() { return "EnergyReader"; }
            public long reads()  { return Components.mask(Components.ENERGY, Components.POSITION); }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) { v.getLong(Components.ENERGY, row); }
        };
    }

    /** Спавнит архетип, который матчат ОБЕ системы выше — то есть делает их конфликтующими. */
    private static GameSystem mergedSpawner(long merged) {
        return new GameSystem() {
            public String name() { return "MergedSpawner"; }
            public long reads()  { return SPAWNER; }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) { cb.create(merged); }
        };
    }

    @Test
    void newArchetypeThatCreatesConflictGrowsStageCountVisibly() {
        long forWriter = Components.mask(Components.ENERGY, Components.HEALTH);
        long forReader = Components.mask(Components.ENERGY, Components.POSITION);
        long merged    = Components.mask(Components.ENERGY, Components.HEALTH, Components.POSITION);

        ArchetypeWorld w = world(forWriter, forReader, SPAWNER);
        List<GameSystem> sys = List.of(energyWriter(), energyReader(), mergedSpawner(merged));
        Scheduler s = new Scheduler(sys, w);

        // writer.writes ∩ reader.reads = ENERGY → конфликт ПО КОМПОНЕНТАМ есть всегда.
        // Но архетипы не пересекаются, а conflict = компоненты И архетипы (decision #3) → одна стадия.
        assertEquals(1, s.stages.size(),
                "архетипы writer и reader не пересекаются → конфликта нет → одна стадия");

        s.runReference(w); // тик 1: рождается merged, который матчат ОБЕ системы
        assertEquals(1, s.stages.size(),
                "раскладка тика 1 не переписывается задним числом: план строится в НАЧАЛЕ тика");

        s.runReference(w); // тик 2: ensurePlan видит новый архетип
        assertEquals(2, s.stages.size(),
                "новый архетип сделал writer и reader конфликтующими → стадий стало БОЛЬШЕ. "
                        + "Раскладка обязана ехать ВИДИМО, а не исполняться неверно");
    }

    /**
     * РЕГРЕСС memory #19: один Scheduler гоняет ОБА мира (Main.java:24,31,32 и все бенчи), причём
     * сначала ВСЕ тики ref, потом все тики par. Если раскладка кэшируется в поле «на потом», par
     * стартует с раскладкой ФИНАЛЬНОГО тика ref — тихо и неверно.
     *
     * Обычный DeterminismTest этого НЕ ловит: он строит отдельный Scheduler на каждый мир.
     */
    @Test
    void sharedSchedulerDoesNotLeakLayoutBetweenWorlds() throws Exception {
        // Сцена ОБЯЗАНА быть той, у которой раскладка МЕНЯЕТСЯ (1 стадия → 2 при рождении merged).
        // На сцене с одной системой раскладка всегда 1 стадия — протечке нечего проявить, и тест
        // был бы вакуумным: он проходил бы даже при полностью выключенном ensurePlan.
        long merged = Components.mask(Components.ENERGY, Components.HEALTH, Components.POSITION);
        ArchetypeWorld ref = world(
                Components.mask(Components.ENERGY, Components.HEALTH),
                Components.mask(Components.ENERGY, Components.POSITION),
                SPAWNER);
        ArchetypeWorld par = ref.copy();
        Scheduler s = new Scheduler(List.of(energyWriter(), energyReader(), mergedSpawner(merged)), ref);
        assertEquals(1, s.stages.size(), "старт: одна стадия");

        for (int t = 0; t < 3; t++) s.runReference(ref); // ref уходит вперёд, раскладка у него уже 2
        assertEquals(2, s.stages.size(), "ref успел вырастить merged → в поле лежит раскладка на 2 стадии");

        ExecutorService p = pool(4);
        try {
            // par стартует С НУЛЯ тем же Scheduler. Если раскладка кэшируется «на потом», он возьмёт
            // ДВУХСТАДИЙНУЮ раскладку ref, хотя merged в его мире ещё нет.
            for (int t = 0; t < 3; t++) s.runParallel(par, p, 4);
        } finally {
            p.shutdown();
        }
        assertEquals(ref.checksum(), par.checksum(),
                "общий Scheduler не имеет права протащить раскладку одного мира в другой");
    }

    // ---------- ДЕТЕРМИНИЗМ ВЫДАЧИ ID (AC #4, решение #7) ----------

    private static long spawnScene(int ticks, int threads) throws Exception {
        ArchetypeWorld w = world(SPAWNER, SPAWNER, SPAWNER);
        Scheduler s = new Scheduler(List.of(spawner()), w);
        if (threads == 0) {
            for (int t = 0; t < ticks; t++) s.runReference(w);
        } else {
            ExecutorService p = pool(threads);
            try {
                for (int t = 0; t < ticks; t++) s.runParallel(w, p, threads);
            } finally {
                p.shutdown();
            }
        }
        return w.checksum();
    }

    @Test
    void spawnIsDeterministicAcrossModesAndThreadCounts() throws Exception {
        long ref = spawnScene(5, 0);
        assertEquals(ref, spawnScene(5, 8), "par@8 обязан воспроизвести reference бит-в-бит");
        assertEquals(ref, spawnScene(5, 4), "число потоков не меняет результат");
        assertEquals(spawnScene(5, 8), spawnScene(5, 8), "воспроизводимо между прогонами");
    }

    /**
     * AC #4 требует ИДЕНТИЧНЫХ ID, а не только идентичного checksum. Проверяется отдельно:
     * checksum ключуется на entityId, поэтому теоретически мог бы совпасть при иной раскладке —
     * а вот совпадение самих id по маскам и значениям это исключает.
     */
    @Test
    void spawnedIdsAreIdenticalAcrossThreadCounts() throws Exception {
        ArchetypeWorld a = world(SPAWNER, SPAWNER, SPAWNER);
        ArchetypeWorld b = a.copy();
        Scheduler sa = new Scheduler(List.of(spawner()), a);
        Scheduler sb = new Scheduler(List.of(spawner()), b);

        ExecutorService p8 = pool(8);
        ExecutorService p4 = pool(4);
        try {
            for (int t = 0; t < 5; t++) sa.runParallel(a, p8, 8);
            for (int t = 0; t < 5; t++) sb.runParallel(b, p4, 4);
        } finally {
            p8.shutdown();
            p4.shutdown();
        }

        assertEquals(a.entityCount(), b.entityCount(), "родилось одинаковое число энтити");
        for (int id = 0; id < a.entityCount(); id++) {
            assertEquals(a.isAlive(id), b.isAlive(id), "энтити " + id + ": живость расходится");
            if (!a.isAlive(id)) continue;
            assertEquals(a.storeOf(id).mask, b.storeOf(id).mask,
                    "энтити " + id + " попала в РАЗНЫЕ архетипы при par@8 и par@4 — id не детерминирован");
        }
    }

    // ---------- ПОРЯДОК: удаление побеждает / stale ловится (AC #5, решение #8) ----------

    private static GameSystem killer(int target) {
        return new GameSystem() {
            public String name() { return "Killer"; }
            public long reads()  { return SPAWNER; }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) { cb.destroy(target); }
        };
    }

    private static GameSystem toucher(int target) {
        return new GameSystem() {
            public String name() { return "Toucher"; }
            public long reads()  { return SPAWNER; }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) { cb.setInv(target, Components.SLOT_INPUT, 5); }
        };
    }

    /**
     * Решение #8, случай, который и переломил выбор: моб на 0 HP, система A шлёт destroy, система B
     * в этом же барьере пишет ему в инвентарь. Единый тотальный порядок превратил бы ОБЫЧНУЮ СМЕРТЬ
     * МОБА В КРАШ. Две системы, трогающие одну энтити в одном тике, — нормальный геймплей.
     */
    @Test
    void destroyWinsWithinBarrierAndEffectOnItIsNotAnError() {
        ArchetypeWorld w = world(SPAWNER, DROP);
        // Killer и Toucher: writes у обоих 0 → конфликта нет → ОДНА стадия → ОДИН барьер.
        Scheduler s = new Scheduler(List.of(killer(1), toucher(1)), w);
        assertEquals(1, s.stages.size(), "тест вакуумен, если системы разъехались по стадиям");

        assertDoesNotThrow(() -> s.runReference(w),
                "эффект на энтити, удаляемую в ЭТОМ же барьере, — не ошибка: удаление победило");
        assertFalse(w.isAlive(1), "энтити удалена");
    }

    /**
     * Обратная сторона той же границы: ссылка на энтити, удалённую в ПРЕДЫДУЩЕМ барьере, —
     * это stale-ссылка, то есть ОШИБКА, и она обязана падать громко. Хоппер на снесённую печь.
     */
    @Test
    void effectOnEntityDestroyedInEarlierTickThrowsLoudly() {
        ArchetypeWorld w = world(SPAWNER, DROP);
        Scheduler s = new Scheduler(List.of(killer(1), toucher(1)), w);
        s.runReference(w); // тик 1: энтити 1 удалена

        IllegalStateException e = assertThrows(IllegalStateException.class, () -> s.runReference(w),
                "тик 2: Toucher ссылается на удалённую РАНЬШЕ — это stale, обязан бросок");
        assertTrue(e.getMessage().contains("неразмещённую"),
                "сообщение обязано называть причину: " + e.getMessage());
    }

    /** Две системы независимо решили убить одного моба — законно, второй destroy идемпотентен. */
    @Test
    void doubleDestroyInSameBarrierIsIdempotent() {
        ArchetypeWorld w = world(SPAWNER, DROP);
        Scheduler s = new Scheduler(List.of(killer(1), killer(1)), w);
        assertDoesNotThrow(() -> s.runReference(w), "двойной destroy в одном барьере — не крах");
        assertFalse(w.isAlive(1));
    }

    /**
     * Решение #7: id НЕ переиспользуется. Именно это делает «stale-ссылки невозможны» доказуемым:
     * entityLoc удалённой остаётся UNPLACED навсегда, и ссылка на труп не может попасть в живую цель.
     */
    @Test
    void destroyedIdIsNeverReissued() {
        ArchetypeWorld w = world(SPAWNER, DROP);
        int dead = 1;
        w.destroyEntity(dead);
        assertFalse(w.isAlive(dead));

        for (int i = 0; i < 5; i++) {
            int fresh = w.createEntity(DROP);
            assertNotEquals(dead, fresh, "id удалённой энтити выдан повторно — stale-ссылка попала бы в живую");
        }
        assertFalse(w.isAlive(dead), "удалённая так и осталась мёртвой");
    }

    // ---------- ADD/REMOVE компонента через буфер ----------

    private static GameSystem componentToggler(int target, int comp, boolean add) {
        return new GameSystem() {
            public String name() { return "Toggler"; }
            public long reads()  { return SPAWNER; }
            public long writes() { return 0; }
            public void run(View v, int row, CommandBuffer cb) {
                if (add) cb.addComponent(target, comp); else cb.removeComponent(target, comp);
            }
        };
    }

    @Test
    void addAndRemoveComponentThroughBufferChangeArchetype() {
        ArchetypeWorld w = world(SPAWNER, DROP);
        new Scheduler(List.of(componentToggler(1, Components.ENERGY, true)), w).runReference(w);
        assertEquals(DROP | Components.bit(Components.ENERGY), w.storeOf(1).mask,
                "add компонента = смена архетипа, поверх готовой migrate()");

        new Scheduler(List.of(componentToggler(1, Components.INVENTORY, false)), w).runReference(w);
        assertEquals(Components.mask(Components.HEALTH, Components.ENERGY), w.storeOf(1).mask,
                "remove компонента убирает его из маски");
    }
}
