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
    public static int runReference(ArchetypeWorld w, Scheduler s) {
        int passes = 0;
        while (passes < MAX_PASSES) {
            s.runReference(w);
            passes++;
            if (!swapAndCheckChanged(w)) break;
        }
        return passes;
    }

    /** Параллельный fixpoint (проходы data-parallel, swap серийный). */
    public static int runParallel(ArchetypeWorld w, Scheduler s, ExecutorService pool, int threads) throws Exception {
        int passes = 0;
        while (passes < MAX_PASSES) {
            s.runParallel(w, pool, threads);
            passes++;
            if (!swapAndCheckChanged(w)) break;
        }
        return passes;
    }

    /**
     * Копирует POWER_NEXT→POWER, возвращает true если хоть одна клетка изменилась.
     * Идёт по архетипам, несущим оба компонента — какие именно, класс не знает.
     */
    private static boolean swapAndCheckChanged(ArchetypeWorld w) {
        boolean changed = false;
        for (ArchetypeStore s : w.stores()) {
            if (!s.has(Components.POWER) || !s.has(Components.POWER_NEXT)) continue;
            int[] power = s.intCol(Components.POWER);
            int[] next = s.intCol(Components.POWER_NEXT);
            for (int r = 0; r < s.size(); r++) {
                if (power[r] != next[r]) { power[r] = next[r]; changed = true; }
            }
        }
        return changed;
    }

    private RedstoneCascade() {}
}
