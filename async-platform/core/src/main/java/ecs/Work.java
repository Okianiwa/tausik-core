package ecs;

/**
 * Диагностический регулятор веса per-entity работы (как WORK в Phase 0).
 * Позволяет проверить гипотезу: потолок speedup держат накладные при ЛЁГКОМ компьюте.
 * WEIGHT=0 (дефолт) — нулевая стоимость; JMH варьирует его как @Param.
 */
public final class Work {
    public static volatile int WEIGHT = 0;

    /** Детерминированная busy-work (xorshift) — компьют-представительная, без обращения к миру. */
    public static long spin(long seed) {
        long x = seed | 1L;
        for (int i = 0; i < WEIGHT; i++) {
            x ^= x << 13;
            x ^= x >>> 7;
            x ^= x << 17;
        }
        return x;
    }

    private Work() {}
}
