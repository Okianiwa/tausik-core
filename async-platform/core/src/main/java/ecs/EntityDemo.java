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
    private static final String[] COMP = {"INVENTORY","ENERGY","PROGRESS","HEAT","FUEL","RECIPE","LINK","POSITION","VELOCITY","HEALTH"};

    public static void main(String[] args) throws Exception {
        int n       = args.length > 0 ? Integer.parseInt(args[0]) : 150_000;
        int ticks   = args.length > 1 ? Integer.parseInt(args[1]) : 50;
        int threads = args.length > 2 ? Integer.parseInt(args[2]) : 8;

        List<GameSystem> systems = EntityScene.systems();
        World ref = EntityScene.build(n);
        World par = EntityScene.build(n);
        Scheduler sched = new Scheduler(systems, ref);

        System.out.printf("Стая: N=%d мобов (архетип MOB), ticks=%d, threads=%d%n", n, ticks, threads);
        System.out.printf("Стадий: %d (систем: %d)%n", sched.stages.size(), systems.size());
        for (int st = 0; st < sched.stages.size(); st++) {
            System.out.printf("  Стадия %d:%n", st);
            for (int si : sched.stages.get(st)) {
                GameSystem g = systems.get(si);
                System.out.printf("    %-12s r{%s} w{%s}%n", g.name(), names(g.reads()), names(g.writes()));
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

    private static String names(long mask) {
        StringBuilder sb = new StringBuilder();
        for (int c = 0; c < COMP.length; c++)
            if ((mask & Components.bit(c)) != 0) { if (sb.length() > 0) sb.append(','); sb.append(COMP[c]); }
        return sb.length() == 0 ? "-" : sb.toString();
    }

    private EntityDemo() {}
}
