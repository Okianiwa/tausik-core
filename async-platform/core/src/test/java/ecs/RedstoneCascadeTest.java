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

    /** POWER клетки по её стабильному entityId. */
    private static int power(ArchetypeWorld w, int entityId) {
        return w.storeOf(entityId).intCol(Components.POWER)[w.rowOf(entityId)];
    }

    @Test
    void parallelFixpointMatchesReference() throws Exception {
        ArchetypeWorld ref = RedstoneScene.build(200, 200);
        ArchetypeWorld par = RedstoneScene.build(200, 200);
        Scheduler s = new Scheduler(RedstoneScene.systems(200), ref);

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
        ArchetypeWorld w = RedstoneScene.build(4, 4); // источник в углу (0,0)
        Scheduler s = new Scheduler(RedstoneScene.systems(4), w);
        RedstoneCascade.runReference(w, s);
        assertEquals(15, power(w, 0), "источник = 15");
        assertEquals(9, power(w, 15), "дальний угол (3,3): manhattan 6 → 15-6=9");
        assertEquals(14, power(w, 1), "сосед (1,0): 14");
    }

    @Test
    void undeclaredPowerReadThrows() {
        GameSystem rogue = new GameSystem() {
            public String name() { return "RoguePower"; }
            public long reads()  { return 0; }
            public long writes() { return Components.mask(Components.POWER_NEXT); }
            public long query()  { return RedstoneScene.REDSTONE; }
            public void run(View v, int e, CommandBuffer cb) {
                v.setInt(Components.POWER_NEXT, e, v.getInt(Components.POWER, e));
            }
        };
        ArchetypeWorld w = RedstoneScene.build(3, 3);
        Scheduler s = new Scheduler(List.of(rogue), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void emptyGridDoesNotCrash() throws Exception {
        ArchetypeWorld ref = RedstoneScene.build(0, 0);
        ArchetypeWorld par = RedstoneScene.build(0, 0);
        Scheduler s = new Scheduler(RedstoneScene.systems(0), ref);
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
