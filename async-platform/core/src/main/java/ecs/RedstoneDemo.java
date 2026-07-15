package ecs;

import ecs.scene.RedstoneScene;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * CLI-раннер ordered-каскада: fixpoint редстоуна reference vs parallel. Печатает число проходов
 * (серийная глубина) и проверяет детерминизм (одинаковое финальное POWER и одинаковое число проходов).
 * Аргументы: [gridW] [gridH] [threads] (дефолт 400 400 8 = 160k клеток).
 */
public final class RedstoneDemo {
    public static void main(String[] args) throws Exception {
        int gw = args.length > 0 ? Integer.parseInt(args[0]) : 400;
        int gh = args.length > 1 ? Integer.parseInt(args[1]) : 400;
        int threads = args.length > 2 ? Integer.parseInt(args[2]) : 8;

        World ref = RedstoneScene.build(gw, gh);
        World par = RedstoneScene.build(gw, gh);
        Scheduler sched = new Scheduler(RedstoneScene.systems(), ref);

        System.out.printf("Редстоун: grid %dx%d = %d клеток, threads=%d%n", gw, gh, gw * gh, threads);
        System.out.printf("Стадий: %d (систем: %d — проход двигает сигнал на 1 клетку)%n",
                sched.stages.size(), sched.systems.size());

        int refPasses = RedstoneCascade.runReference(ref, sched);

        ExecutorService pool = Executors.newFixedThreadPool(threads);
        int parPasses;
        try {
            parPasses = RedstoneCascade.runParallel(par, sched, pool, threads);
        } finally {
            pool.shutdown();
        }

        System.out.printf("Проходов до fixpoint: reference=%d parallel=%d (серийная ГЛУБИНА каскада)%n",
                refPasses, parPasses);
        long cRef = ref.checksum(), cPar = par.checksum();
        boolean det = cRef == cPar && refPasses == parPasses;
        System.out.printf("Детерминизм: checksum(ref)=%d checksum(par@%d)=%d → %s%n",
                cRef, threads, cPar, det ? "СОВПАЛО ✓" : "РАСХОЖДЕНИЕ ✗");
        if (!det) throw new IllegalStateException("Недетерминизм каскада: ref != parallel");
    }

    private RedstoneDemo() {}
}
