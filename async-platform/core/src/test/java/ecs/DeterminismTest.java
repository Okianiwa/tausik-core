package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertEquals;

class DeterminismTest {

    private static final int N = 30_000, TICKS = 25;

    private static long runReference() {
        Scheduler s = new Scheduler(BlockEntityScene.systems());
        World w = BlockEntityScene.build(N);
        for (int t = 0; t < TICKS; t++) s.runReference(w);
        return w.checksum();
    }

    private static long runParallel(int threads) throws Exception {
        Scheduler s = new Scheduler(BlockEntityScene.systems());
        World w = BlockEntityScene.build(N);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            for (int t = 0; t < TICKS; t++) s.runParallel(w, pool, threads);
        } finally {
            pool.shutdown();
        }
        return w.checksum();
    }

    @Test
    void parallelMatchesReference() throws Exception {
        long ref = runReference();
        assertEquals(ref, runParallel(8), "parallel@8 должен воспроизвести reference бит-в-бит");
        assertEquals(ref, runParallel(4), "число потоков не должно менять финальное состояние");
    }

    @Test
    void reproducibleAcrossRuns() throws Exception {
        assertEquals(runParallel(8), runParallel(8), "повторный параллельный прогон — тот же checksum");
    }
}
