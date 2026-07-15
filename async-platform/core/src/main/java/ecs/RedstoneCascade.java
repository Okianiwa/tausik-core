package ecs;

import java.util.concurrent.ExecutorService;

/**
 * Fixpoint-оркестрация ordered-каскада: повторяем параллельный проход + swap, пока power не
 * стабилизируется. Каждый проход data-parallel (double-buffer), но между проходами — barrier+swap.
 * Число проходов = серийная ГЛУБИНА каскада (дистанция сигнала) — принципиальный предел
 * ordered-propagation, не дефект модели.
 */
public final class RedstoneCascade {
    public static final int MAX_PASSES = 100_000; // страховка от незавершения

    /** Однопоточный fixpoint. Возвращает число проходов до стабилизации. */
    public static int runReference(World w, Scheduler s) {
        int passes = 0;
        while (passes < MAX_PASSES) {
            s.runReference(w);
            passes++;
            if (!swapAndCheckChanged(w)) break;
        }
        return passes;
    }

    /** Параллельный fixpoint (проходы data-parallel, swap серийный). */
    public static int runParallel(World w, Scheduler s, ExecutorService pool, int threads) throws Exception {
        int passes = 0;
        while (passes < MAX_PASSES) {
            s.runParallel(w, pool, threads);
            passes++;
            if (!swapAndCheckChanged(w)) break;
        }
        return passes;
    }

    /** Копирует powerNext→power, возвращает true если хоть одна клетка изменилась. */
    private static boolean swapAndCheckChanged(World w) {
        boolean changed = false;
        for (int e = 0; e < w.size; e++) {
            if (w.power[e] != w.powerNext[e]) { w.power[e] = w.powerNext[e]; changed = true; }
        }
        return changed;
    }

    private RedstoneCascade() {}
}
