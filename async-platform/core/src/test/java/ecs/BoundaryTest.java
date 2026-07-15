package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;

class BoundaryTest {

    @Test
    void emptyWorldDoesNotCrash() throws Exception {
        Scheduler s = new Scheduler(BlockEntityScene.systems());
        World ref = new World(0);
        World par = new World(0);
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
            public void run(View v, int e, CommandBuffer cb) { v.heat(e); }
        };
        Scheduler s = new Scheduler(List.of(reader));
        World w = new World(1000);
        long before = w.checksum();
        s.runReference(w);
        assertEquals(before, w.checksum(), "pure-read система не меняет мир");
    }
}
