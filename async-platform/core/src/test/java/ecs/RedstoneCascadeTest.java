package ecs;

import ecs.scene.RedstoneScene;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RedstoneCascadeTest {

    @Test
    void parallelFixpointMatchesReference() throws Exception {
        World ref = RedstoneScene.build(200, 200);
        World par = RedstoneScene.build(200, 200);
        Scheduler s = new Scheduler(RedstoneScene.systems(), ref);

        int refPasses = RedstoneCascade.runReference(ref, s);
        ExecutorService pool = Executors.newFixedThreadPool(8);
        int parPasses;
        try {
            parPasses = RedstoneCascade.runParallel(par, s, pool, 8);
        } finally {
            pool.shutdown();
        }
        assertEquals(refPasses, parPasses, "число проходов детерминировано");
        assertEquals(ref.checksum(), par.checksum(), "финальное POWER бит-в-бит совпадает");
        assertTrue(refPasses > 1, "каскад — многопроходный (серийная глубина > 1)");
    }

    @Test
    void propagationDecaysByManhattanDistance() {
        World w = RedstoneScene.build(4, 4); // источник в углу (0,0)
        Scheduler s = new Scheduler(RedstoneScene.systems(), w);
        RedstoneCascade.runReference(w, s);
        assertEquals(15, w.power[0], "источник = 15");
        assertEquals(9, w.power[15], "дальний угол (3,3): manhattan 6 → 15-6=9");
        assertEquals(14, w.power[1], "сосед (1,0): 14");
    }

    @Test
    void undeclaredPowerReadThrows() {
        GameSystem rogue = new GameSystem() {
            public String name() { return "RoguePower"; }
            public int archetype() { return Archetype.REDSTONE; }
            public long reads()  { return 0; }
            public long writes() { return Components.mask(Components.POWER_NEXT); }
            public void run(View v, int e, CommandBuffer cb) { v.setPowerNext(e, v.power(e)); }
        };
        World w = RedstoneScene.build(3, 3);
        Scheduler s = new Scheduler(List.of(rogue), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void emptyGridDoesNotCrash() throws Exception {
        World ref = RedstoneScene.build(0, 0);
        World par = RedstoneScene.build(0, 0);
        Scheduler s = new Scheduler(RedstoneScene.systems(), ref);
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            assertDoesNotThrow(() -> RedstoneCascade.runReference(ref, s));
            RedstoneCascade.runParallel(par, s, pool, 4);
        } finally {
            pool.shutdown();
        }
        assertEquals(ref.checksum(), par.checksum());
    }
}
