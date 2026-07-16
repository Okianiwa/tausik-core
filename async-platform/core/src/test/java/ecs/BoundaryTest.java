package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;

class BoundaryTest {

    /** Мир из n энтити одного архетипа — заготовка для граничных случаев. */
    static ArchetypeWorld world(long mask, int n) {
        ArchetypeWorld w = new ArchetypeWorld(Components.standard(), Math.max(1, n));
        for (int i = 0; i < n; i++) w.createEntity(mask);
        return w;
    }

    @Test
    void emptyWorldDoesNotCrash() throws Exception {
        ArchetypeWorld ref = BlockEntityScene.build(0);
        ArchetypeWorld par = BlockEntityScene.build(0);
        Scheduler s = new Scheduler(BlockEntityScene.systems(), ref);
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            assertDoesNotThrow(() -> s.runReference(ref));
            s.runParallel(par, pool, 4);
        } finally {
            pool.shutdown();
        }
        assertEquals(ref.checksum(), par.checksum());
    }

    @Test
    void pureReadSystemLeavesWorldUnchanged() {
        GameSystem reader = new GameSystem() {
            public String name() { return "PureRead"; }
            public long reads()  { return Components.bit(Components.HEAT); }
            public long writes() { return 0; }
            public void run(View v, int e, CommandBuffer cb) { v.getDouble(Components.HEAT, e); }
        };
        ArchetypeWorld w = world(Components.bit(Components.HEAT), 1000);
        Scheduler s = new Scheduler(List.of(reader), w);
        long before = w.checksum();
        s.runReference(w);
        assertEquals(before, w.checksum(), "pure-read система не меняет мир");
    }

    /** Система, не матчнутая ни одним архетипом, просто не исполняется — молча, но и без вреда. */
    @Test
    void systemMatchingNoArchetypeRunsOverNothing() {
        GameSystem orphan = new GameSystem() {
            public String name() { return "Orphan"; }
            public long reads()  { return 0; }
            public long writes() { return Components.bit(Components.ENERGY); }
            public void run(View v, int e, CommandBuffer cb) {
                throw new AssertionError("не должна исполниться: нет архетипа с ENERGY");
            }
        };
        ArchetypeWorld w = world(Components.bit(Components.HEAT), 10);
        Scheduler s = new Scheduler(List.of(orphan), w);
        assertDoesNotThrow(() -> s.runReference(w));
    }
}
