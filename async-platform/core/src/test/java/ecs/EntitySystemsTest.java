package ecs;

import ecs.scene.EntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class EntitySystemsTest {

    private static final int N = 20_000, TICKS = 20;

    @Test
    void dirtyLayoutPacksSenseAndCombatButSplitsMove() {
        World w = EntityScene.build(N);
        Scheduler s = new Scheduler(EntityScene.systems(), w);
        // Sense+Combat читают POSITION (read∩read, пишут разное) → одна стадия; Move пишет POSITION → отдельно.
        assertEquals(2, s.stages.size(), "грязная подсистема: [Sense,Combat] | [Move]");
        assertEquals(2, s.stages.get(0).length, "Sense+Combat пакуются");
        assertEquals("MobSense",   s.systems.get(s.stages.get(0)[0]).name());
        assertEquals("MobCombat",  s.systems.get(s.stages.get(0)[1]).name());
        assertEquals("MobMove",    s.systems.get(s.stages.get(1)[0]).name());
    }

    @Test
    void parallelMatchesReferenceOnDirtySubsystem() throws Exception {
        assertEquals(runReference(), runParallel(8), "parallel@8 == reference на грязной стае");
        assertEquals(runReference(), runParallel(4), "число потоков не меняет результат");
        assertEquals(runParallel(8), runParallel(8), "воспроизводимо между прогонами");
    }

    @Test
    void neighbourReadWithoutDeclaringPositionThrows() {
        GameSystem rogue = new GameSystem() {
            public String name() { return "RogueNeighbour"; }
            public int archetype() { return Archetype.MOB; }
            public long reads()  { return 0; }
            public long writes() { return Components.mask(Components.VELOCITY); }
            public void run(View v, int e, CommandBuffer cb) { v.setVelX(e, v.posX((e + 1) % v.size())); }
        };
        World w = EntityScene.build(16);
        Scheduler s = new Scheduler(List.of(rogue), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void emptyFlockDoesNotCrash() throws Exception {
        World ref = EntityScene.build(0);
        World par = EntityScene.build(0);
        Scheduler s = new Scheduler(EntityScene.systems(), ref);
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            assertDoesNotThrow(() -> s.runReference(ref));
            s.runParallel(par, pool, 4);
        } finally {
            pool.shutdown();
        }
        assertEquals(ref.checksum(), par.checksum());
    }

    private static long runReference() {
        World w = EntityScene.build(N);
        Scheduler s = new Scheduler(EntityScene.systems(), w);
        for (int t = 0; t < TICKS; t++) s.runReference(w);
        return w.checksum();
    }

    private static long runParallel(int threads) throws Exception {
        World w = EntityScene.build(N);
        Scheduler s = new Scheduler(EntityScene.systems(), w);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            for (int t = 0; t < TICKS; t++) s.runParallel(w, pool, threads);
        } finally {
            pool.shutdown();
        }
        return w.checksum();
    }
}
