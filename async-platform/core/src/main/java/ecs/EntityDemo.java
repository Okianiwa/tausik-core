package ecs;

import ecs.scene.EntityScene;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * CLI-раннер грязной подсистемы: раскладка систем стаи по стадиям + чек детерминизма
 * checksum(reference)==checksum(parallel). Аргументы: [N] [ticks] [threads] (дефолт 150000 50 8).
 */
public final class EntityDemo {

    public static void main(String[] args) throws Exception {
        int n       = args.length > 0 ? Integer.parseInt(args[0]) : 150_000;
        int ticks   = args.length > 1 ? Integer.parseInt(args[1]) : 50;
        int threads = args.length > 2 ? Integer.parseInt(args[2]) : 8;

        List<GameSystem> systems = EntityScene.systems();
        ArchetypeWorld ref = EntityScene.build(n);
        ArchetypeWorld par = EntityScene.build(n);
        Scheduler sched = new Scheduler(systems, ref);

        System.out.printf("Стая: N=%d мобов (архетип MOB), ticks=%d, threads=%d%n", n, ticks, threads);
        System.out.printf("Стадий: %d (систем: %d)%n", sched.stages.size(), systems.size());
        for (int st = 0; st < sched.stages.size(); st++) {
            System.out.printf("  Стадия %d:%n", st);
            for (int si : sched.stages.get(st)) {
                GameSystem g = systems.get(si);
                System.out.printf("    %-12s r{%s} w{%s}%n", g.name(), names(g.reads(), ref.reg), names(g.writes(), ref.reg));
            }
        }

        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            for (int t = 0; t < ticks; t++) sched.runReference(ref);
            for (int t = 0; t < ticks; t++) sched.runParallel(par, pool, threads);
        } finally {
            pool.shutdown();
        }

        long cRef = ref.checksum(), cPar = par.checksum();
        boolean det = cRef == cPar;
        System.out.printf("%nДетерминизм: checksum(ref)=%d checksum(par@%d)=%d → %s%n",
                cRef, threads, cPar, det ? "СОВПАЛО ✓" : "РАСХОЖДЕНИЕ ✗");
        if (!det) throw new IllegalStateException("Недетерминизм грязной подсистемы: ref != parallel");
    }

    /** Имена компонентов — из реестра; раньше здесь был захардкоженный массив. */
    private static String names(long mask, ComponentRegistry reg) {
        String s = Main.names(mask, reg);
        return s.isEmpty() ? "-" : s;
    }

    private EntityDemo() {}
}
